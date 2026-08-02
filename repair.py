#!/usr/bin/env python3
"""CLI for repairing Thai transcripts mangled by number normalization.

    ./repair.py transcript.txt -o fixed.txt --report report.csv
"""

import argparse
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from thairepair import Change, load_overrides, repair_text, write_report

DEFAULT_WORDS = Path(__file__).parent / "resource" / "word.csv"

#: argparse only ever sees this line; the rest of the docstring is the usage
#: example, which it would reflow.
DESCRIPTION = "Repair Thai transcripts mangled by number normalization."


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument("input", type=Path, help="transcript to repair ('-' for stdin)")
    parser.add_argument(
        "-o", "--output", type=Path, help="where to write the repaired text (default: stdout)"
    )
    parser.add_argument(
        "--report", type=Path, help="write a CSV log of every change and non-change"
    )
    parser.add_argument(
        "--words",
        type=Path,
        default=DEFAULT_WORDS,
        help=f"curated wrong,correct pairs (default: {DEFAULT_WORDS})",
    )
    parser.add_argument(
        "--aggressive",
        action="store_true",
        help="also repair digits touching Thai on one side only; needs review",
    )
    parser.add_argument(
        "--no-normalize",
        action="store_true",
        help="skip PyThaiNLP tone-mark/vowel normalization of the input",
    )
    parser.add_argument(
        "--no-join-words",
        action="store_true",
        help="leave words the ASR split across spaces (ประสบการ ณ ์)",
    )
    parser.add_argument(
        "--no-collapse-spaces",
        action="store_true",
        help="keep the token spacing on lines that have a space between every word",
    )
    parser.add_argument(
        "--no-replace",
        action="store_true",
        help="skip the curated find-and-replace corpora in resource/",
    )
    parser.add_argument(
        "--no-yamok",
        action="store_true",
        help="leave a repeated word spelled out instead of folding it to ๆ (เร็วเร็ว)",
    )
    parser.add_argument(
        "--no-space-numbers",
        action="store_true",
        help="leave genuine numbers flush against Thai text (ได้18ท่าน)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # argparse hands back a Namespace of Any, so the types are pinned here, at
    # the boundary — everything downstream is checked.
    input_path: Path = args.input
    output_path: Path | None = args.output
    report_path: Path | None = args.report
    words_path: Path | None = args.words
    aggressive: bool = args.aggressive
    no_normalize: bool = args.no_normalize
    no_space_numbers: bool = args.no_space_numbers
    no_join_words: bool = args.no_join_words
    no_collapse_spaces: bool = args.no_collapse_spaces
    no_yamok: bool = args.no_yamok
    no_replace: bool = args.no_replace

    text = sys.stdin.read() if str(input_path) == "-" else input_path.read_text("utf-8")
    overrides: dict[str, str] = (
        load_overrides(words_path) if words_path and words_path.exists() else {}
    )

    fixed, changes = repair_text(
        text,
        overrides=overrides,
        aggressive=aggressive,
        do_normalize=not no_normalize,
        do_space_numbers=not no_space_numbers,
        do_join_words=not no_join_words,
        do_collapse_spaces=not no_collapse_spaces,
        do_yamok=not no_yamok,
        do_replace=not no_replace,
    )

    if output_path:
        output_path.write_text(fixed, "utf-8")
    else:
        sys.stdout.write(fixed)

    if report_path:
        write_report(report_path, changes)

    print(f"thairepair: {_summarize(changes)}", file=sys.stderr)
    # Unresolved digits are the review queue, not a crash: exit 1 so a pipeline
    # can gate on them without losing the repaired output.
    return 1 if any(not change.applied for change in changes) else 0


def _summarize(changes: Sequence[Change]) -> str:
    """One line of counts for stderr.

    Reported by rule, not confidence: "13 digit, 205 spacing" says far more than
    "218 high", since spacing is cosmetic and digit repairs are not.
    """
    counts: Counter[str] = Counter(
        change.rule if change.applied else "unresolved" for change in changes
    )
    parts: list[str] = [f"{n} {name}" for name, n in counts.most_common()]
    return ", ".join(parts) or "no changes"


if __name__ == "__main__":
    raise SystemExit(main())
