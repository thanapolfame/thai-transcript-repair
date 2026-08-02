#!/usr/bin/env python3
"""CLI for repairing Thai transcripts mangled by number normalization.

    ./repair.py transcript.txt -o fixed.txt --report report.csv
"""

import argparse
import sys
from collections import Counter
from pathlib import Path

from thairepair import load_overrides, repair_text, write_report

DEFAULT_WORDS = Path(__file__).parent / "resource" / "word.csv"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
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
        help=f"curated correct,wrong pairs (default: {DEFAULT_WORDS})",
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
        "--no-space-numbers",
        action="store_true",
        help="leave genuine numbers flush against Thai text (ได้18ท่าน)",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    text = sys.stdin.read() if str(args.input) == "-" else args.input.read_text("utf-8")
    overrides = load_overrides(args.words) if args.words and args.words.exists() else {}

    fixed, changes = repair_text(
        text,
        overrides=overrides,
        aggressive=args.aggressive,
        do_normalize=not args.no_normalize,
        do_space_numbers=not args.no_space_numbers,
    )

    if args.output:
        args.output.write_text(fixed, "utf-8")
    else:
        sys.stdout.write(fixed)

    if args.report:
        write_report(args.report, changes)

    # Report by rule, not confidence: "13 digit, 205 spacing" says far more than
    # "218 high", since spacing is cosmetic and digit repairs are not.
    counts = Counter(
        change.rule if change.applied else "unresolved" for change in changes
    )
    summary = ", ".join(f"{n} {name}" for name, n in counts.most_common()) or "no changes"
    print(f"thairepair: {summary}", file=sys.stderr)
    # Unresolved digits are the review queue, not a crash: exit 1 so a pipeline
    # can gate on them without losing the repaired output.
    return 1 if counts["unresolved"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
