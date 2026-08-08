---
name: add-repair-option
description: Add a new on/off repair option to thairepair end-to-end — repair_text() parameter, CLI flag, GUI wiring, and checkbox. Use when adding a toggleable repair behavior (like the existing --aggressive, --no-yamok, --no-join-words flags) so no interface is left out of sync.
---

# Add a repair option

A repair option is a boolean toggle that has to flow through three files in
lockstep: `repair_text()` → CLI flag → GUI checkbox. Missing one leaves the
option working in one interface and silently absent in the other.

## The three places (per `.claude/CLAUDE.md`)

1. **`thairepair/repair.py`** — add the `do_<name>: bool` parameter to
   `repair_text()` and gate the relevant pass on it. This is the only place
   behavior actually lives; the other two are just wiring.

2. **`repair.py` (CLI)**, in `build_parser()` and `main()`:
   - Add `parser.add_argument(...)` with `action="store_true"`.
   - Pull it into a typed local in `main()`
     (`no_<name>: bool = args.no_<name>`), following the existing "argparse
     hands back `Any`, pin it at the boundary" pattern.
   - Pass it through to `repair_text()`.
   - Match the existing naming convention: options that default **on** are
     framed as opt-out (`--no-<name>`, negated when passed down); options
     that default **off** are framed as opt-in (`--<name>`, passed straight
     through) — see `--aggressive` vs. `--no-yamok`.

3. **`thairepair/webgui.py`** — add `do_<name>=_flag(params, "<name>",
   <default>)` to the `repair_text()` call inside `repair_upload()`.

4. **`thairepair/webgui.html`** — add a checkbox
   `<input type="checkbox" id="<name>" ...>` (ship `checked` if the default
   is on) to the options list, and add `"<name>"` to the `OPTIONS` array.
   **The checkbox `id` is the wire format** — it must match the
   query-string key read by `_flag()` in step 3 exactly, character for
   character, or the checkbox silently falls back to the default with no
   error anywhere.

## Before calling it done

This project's gate is non-negotiable — run it, don't just eyeball the diff:

```bash
.venv/bin/python -m mypy
.venv/bin/python -m pytest -q
```

If mypy complains about an empty container or a `None`-initialized
accumulator, annotate it explicitly rather than relaxing anything — see the
"Code standard" section of `.claude/CLAUDE.md`.
