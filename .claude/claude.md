# thairepair — working notes

Repairs Thai ASR transcripts corrupted by an over-eager inverse-text-
normalization step. `thairepair/` is the library, `repair.py` the CLI, `gui.py`
the browser front end, `resource/word.csv` the curated regression corpus.

## Code standard: strict type checking

**Every change must pass `mypy --strict` before it is done.** This is not
advisory; it is the gate.

```bash
.venv/bin/python -m mypy          # config lives in pyproject.toml
.venv/bin/python -m pytest -q
```

The config is `[tool.mypy]` in `pyproject.toml`: `strict = true` plus
`warn_unreachable`, covering `repair.py`, `gui.py`, `thairepair/` and `tests/`.
A module inside `thairepair/` is covered the moment it exists; a new script at
the root has to be listed in `files` by hand. Do not put anything in an exclude
list, and do not relax a setting to make an error go away.

### What strict means in practice

- **Annotate every function**, including tests, private helpers and nested
  closures. `-> None` on a test is not noise; it is what keeps the test file
  inside the type-checked set.
- **Annotate every empty container** at its assignment: `out: list[str] = []`,
  `changes: list[Change] = []`. Mypy cannot infer from a later `append`.
- **Annotate accumulators that start as `None`**: `best: _JoinCandidate | None =
  None`. If the resulting type is a wide tuple, give it a module-level alias
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
  lexicon is cached and shared by every pass, so a mutable `set[str]` in a
  signature is a bug waiting to happen.

### Python version

Runtime is **3.13** (Homebrew `python@3.13`) and `python_version = "3.13"` is
pinned in the mypy config. Write modern annotations:

- **Builtin generics and `|`**: `list[str]`, `dict[str, str]`,
  `tuple[int, int]`, `str | None`. Never `typing.List` / `Optional[...]` — the
  `typing` aliases are deprecated and this project has none left.
- **`collections.abc` for protocols**: `Sequence`, `Iterator`, `Set as
  AbstractSet`. `typing` is now only for things with no abc equivalent —
  `NamedTuple`, `Any`.
- **`type` statements for aliases** (PEP 695): `type Lexicon = AbstractSet[str]`,
  `type _DigitCandidate = tuple[...]`. They are lazily evaluated, so an alias
  may reference something defined later in the module.
- **No `from __future__ import annotations`.** Annotations are evaluated at
  runtime, so a name must exist before it is used in a signature — that is why
  `Change` sits above the functions that return it in `thairepair/repair.py`.
  Order the module rather than reaching for string forward references.

If you bump the runtime again, `python_version` in `pyproject.toml`, the version
in the README's Usage section, the interpreter search in `start-gui.sh` and
`start-gui.bat` (both refuse anything older), and this section all have to move
together.

## The GUI

`gui.py` is a launcher; the work is in `thairepair/webgui.py`, a stdlib
`http.server` bound to loopback that serves `webgui.html` and answers
`POST /repair` with the repaired text and the report as JSON. No new
dependencies — the browser is the toolkit, and "download" is the only way the
outputs reach disk.

The page has a second tab, the `.md ⇄ .docx` converter, and it holds that line:
`requirements.txt` is unchanged, because pandoc is a binary rather than a Python
package. `thairepair/pandoc.py` finds it or downloads the archive built for this
machine into `~/.thairepair/pandoc`, and `thairepair/convert.py` pipes documents
through it — stdin in, stdout out, binary included, so a conversion never
becomes a file on disk either. Two rules there are load-bearing:

- **A pandoc that exists is not a pandoc that runs.** `pypandoc-binary`'s
  `macosx_11_0_arm64` wheel ships an x86_64 executable, which is why the search
  in `pandoc.py` executes a candidate before believing in it. That is also why
  the dependency was dropped in favour of pandoc's own release archives.
- **The download is never automatic.** `install()` is reached only from the
  page's button or `convert.py --install-pandoc`; fetching 40 MB is the user's
  decision. Everything else degrades to "the tab says pandoc is missing", and
  the repair path never imports either module.

Two things to keep in mind when changing it:

- **The page is outside the type gate.** `webgui.html` is HTML and JavaScript,
  which mypy never sees, so decisions belong in Python where they are checked.
  The JS is deliberately thin: post the file, render counts, save two blobs.
- **A repair option lives in three places.** Adding one means a flag in
  `repair.py`, a `_flag(...)` read in `repair_upload()`, and a checkbox whose
  `id` matches the query-string key plus an entry in the page's `OPTIONS` list.
  The `id` *is* the wire format; a typo silently falls back to the default.
  This applies to the repair tab only — the converter has no options, and
  nothing about it belongs in `OPTIONS`.

`report_csv()` is the report as a string and `write_report()` is a thin wrapper
over it — the GUI needs the text, the CLI needs the file, and both must produce
the same bytes. `test/*.report.csv` are the CLI's own output, so comparing
against them catches a drift between the two paths.

Uploads decode UTF-8 first, then cp874: Word and Excel on a Thai Windows
machine still emit TIS-620, and the strictness of UTF-8 is what does the
detecting. The report downloads with a BOM, without which Excel renders the Thai
as mojibake.

## Testing

`tests/test_repair.py` is parametrized over `resource/word.csv` and asserts, for
every row, that the corrupted form is repaired *without* the override table and
the correct form is left untouched. A row that only passes through the override
tier means the lexicon tier has a gap — that is the signal to look at, not a
reason to add an override.

Two invariants the suite pins and changes must not break: nothing is ever
rewritten without a report row, and line structure/spacing survives verbatim.
