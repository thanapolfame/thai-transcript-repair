#!/usr/bin/env python3
"""Convert between Markdown and Word, on the command line.

    ./convert.py notes.md              -> notes.docx
    ./convert.py report.docx           -> report.md
    ./convert.py notes.md -o out.docx
    ./convert.py --install-pandoc

The direction is the input's extension; there is nothing else it could mean.
The same conversion is the second tab of the GUI.

Conversion needs pandoc, which is a binary rather than a Python package.
``--install-pandoc`` downloads the build for this machine into
``~/.thairepair/pandoc`` — no administrator rights, no system-wide install. A
pandoc already on ``PATH`` is used in preference to it.
"""

import argparse
from pathlib import Path

from thairepair import pandoc
from thairepair.convert import docx_to_md, md_to_docx

#: What each input extension converts to, and the suffix of the output.
DIRECTIONS = {".md": ".docx", ".markdown": ".docx", ".docx": ".md"}


def _install() -> int:
    """Fetch pandoc, printing progress as it goes."""
    try:
        path = pandoc.install(progress=lambda message: print(f"  {message}"))
    except ValueError as exc:
        print(f"convert.py: {exc}")
        return 1
    print(f"convert.py: pandoc {pandoc.version()} -> {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input", nargs="?", type=Path, help=".md or .docx to convert")
    parser.add_argument(
        "-o", "--output", type=Path, help="where to write (default: alongside the input)"
    )
    parser.add_argument(
        "--install-pandoc",
        action="store_true",
        help="download pandoc for this machine and exit",
    )
    args = parser.parse_args(argv)

    # argparse hands back a Namespace of Any; pin the types at the boundary.
    input_path: Path | None = args.input
    output_path: Path | None = args.output
    install_pandoc: bool = args.install_pandoc

    if install_pandoc:
        return _install()
    if input_path is None:
        parser.error("ต้องระบุไฟล์ (an input file is required)")

    target = DIRECTIONS.get(input_path.suffix.lower())
    if target is None:
        print(f"convert.py: รองรับเฉพาะ .md และ .docx (unsupported: {input_path.suffix})")
        return 2

    if not pandoc.available():
        print("convert.py: ยังไม่ได้ติดตั้ง pandoc — สั่ง convert.py --install-pandoc ก่อน")
        print("convert.py: pandoc is not installed; run convert.py --install-pandoc")
        return 3

    destination = output_path or input_path.with_suffix(target)
    try:
        if target == ".docx":
            destination.write_bytes(md_to_docx(input_path.read_text(encoding="utf-8")))
        else:
            destination.write_text(docx_to_md(input_path.read_bytes()), encoding="utf-8")
    except ValueError as exc:
        print(f"convert.py: {exc}")
        return 1

    print(f"convert.py: {input_path} -> {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
