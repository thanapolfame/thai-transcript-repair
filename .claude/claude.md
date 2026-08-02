# thairepair — working notes

Repairs Thai ASR transcripts corrupted by an over-eager inverse-text-
normalization step. `thairepair/` is the library, `repair.py` the CLI,
`resource/word.csv` the curated regression corpus.

## Code standard: strict type checking

**Every change must pass `mypy --strict` before it is done.** This is not
advisory; it is the gate.

```bash
.venv/bin/python -m mypy          # config lives in pyproject.toml
.venv/bin/python -m pytest -q
```

The config is `[tool.mypy]` in `pyproject.toml`: `strict = true` plus
`warn_unreachable`, covering `repair.py`, `thairepair/` and `tests/`. A new
module is covered the moment it exists — do not add it to an exclude list, and
do not relax a setting to make an error go away.

### What strict means in practice

- **Annotate every function**, including tests, private helpers and nested
  closures. `-> None` on a test is not noise; it is what keeps the test file
  inside the type-checked set.
- **Annotate every empty container** at its assignment: `out: List[str] = []`,
  `changes: List[Change] = []`. Mypy cannot infer from a later `append`.
- **Annotate accumulators that start as `None`**: `best: Optional[_JoinCandidate]
  = None`. If the resulting type is a wide tuple, give it a module-level alias
  (`_JoinCandidate`, `_DigitCandidate` in `thairepair/repair.py`) rather than
  repeating it — the alias is also where you document what the tuple ranks by.
- **Pin `Any` at the boundary, never let it spread.** Untyped sources — argparse
  `Namespace`, `re.Match.group(n)`, CSV rows — hand back `Any`. Assign it
  straight into an explicitly annotated local (`input_path: Path = args.input`,
  `word: str = match.group(1)`) so everything downstream is checked. `main()` in
  `repair.py` and `join_magnitude_words()` are the two examples to copy.
- **`# type: ignore` needs a reason** — narrow code (`# type: ignore[arg-type]`)
  and a comment saying why. `warn_unused_ignores` is on, so a stale one fails
  the build.
- **Read-only set parameters take `Lexicon`** (`AbstractSet[str]`, defined in
  `thairepair/lexicon.py`); `default_lexicon()` returns a `frozenset`. The
  lexicon is cached and shared by every pass, so a mutable `Set[str]` in a
  signature is a bug waiting to happen.

### Python version

Runtime is **3.9** and `python_version = "3.9"` is pinned in the mypy config.
So:

- Every module starts with `from __future__ import annotations`, which is what
  makes forward references work without quoting.
- Use `typing.List` / `Dict` / `Tuple` / `Optional`, not `list[str]` or
  `str | None`. The `__future__` import would let the builtin generics past the
  parser in annotations, but they still break in any evaluated position, so the
  project keeps one spelling everywhere.

## Testing

`tests/test_repair.py` is parametrized over `resource/word.csv` and asserts, for
every row, that the corrupted form is repaired *without* the override table and
the correct form is left untouched. A row that only passes through the override
tier means the lexicon tier has a gap — that is the signal to look at, not a
reason to add an override.

Two invariants the suite pins and changes must not break: nothing is ever
rewritten without a report row, and line structure/spacing survives verbatim.
