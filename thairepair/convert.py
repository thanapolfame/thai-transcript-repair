"""Convert between Markdown and Word's ``.docx``, by way of pandoc.

The conversion itself is one subprocess per file.  Pandoc reads the source from
standard input and writes the result to standard output — binary formats
included, which is what lets this keep the promise the rest of the GUI makes:
the document exists in memory and in the pipe, and never as a file on disk.
:mod:`thairepair.pandoc` is what finds or fetches the executable.

``gfm`` is the Markdown dialect on both sides.  It is what people mean when they
say a ``.md`` file — pipe tables, task lists, fenced code — and it is one
constant to change if that ever turns out to be the wrong guess.
"""

import subprocess
import zipfile
from io import BytesIO

from . import pandoc

#: The Markdown dialect read and written.  See the module docstring.
MARKDOWN_FORMAT = "gfm"

#: What a browser should be told each output is.
DOCX_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
MARKDOWN_TYPE = "text/markdown; charset=utf-8"

#: Long enough for a book, short enough that a wedged pandoc does not hold a
#: request open for the rest of the afternoon.
TIMEOUT = 120


def _run(arguments: list[str], source: bytes) -> bytes:
    """Pipe ``source`` through pandoc and return what it writes.

    Pandoc reports a refusal on stderr and a non-zero status; both become a
    ``ValueError``, which is what the web handler already turns into a 400 and
    the CLI already prints.
    """
    executable = pandoc.find()
    if executable is None:
        raise ValueError(
            "ยังไม่ได้ติดตั้ง pandoc — กดปุ่มติดตั้งในหน้าเว็บ หรือสั่ง convert.py --install-pandoc "
            "(pandoc is not installed)"
        )

    try:
        result = subprocess.run(
            [str(executable), *arguments],
            input=source,
            capture_output=True,
            timeout=TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("แปลงไฟล์นานเกินไป (the conversion timed out)") from exc
    except OSError as exc:
        raise ValueError(f"เรียก pandoc ไม่สำเร็จ (could not run pandoc): {exc}") from exc

    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise ValueError(f"pandoc แปลงไฟล์ไม่สำเร็จ (conversion failed): {detail}")
    return result.stdout


def md_to_docx(text: str) -> bytes:
    """Convert Markdown text to the bytes of a ``.docx`` file."""
    return _run(
        [
            "--from",
            MARKDOWN_FORMAT,
            "--to",
            "docx",
            # Without this pandoc emits a document fragment rather than a file
            # Word will open.
            "--standalone",
            "--output",
            "-",
        ],
        text.encode("utf-8"),
    )


def docx_to_md(raw: bytes) -> str:
    """Convert the bytes of a ``.docx`` file to Markdown text."""
    if not raw.startswith(b"PK"):
        # Every .docx is a zip.  Catching it here names the problem; letting
        # pandoc catch it produces a wall of Haskell.
        raise ValueError("ไฟล์นี้ไม่ใช่ .docx (this is not a .docx file)")
    return _run(
        [
            "--from",
            "docx",
            "--to",
            MARKDOWN_FORMAT,
            # Pandoc hard-wraps at 72 columns by default.  Thai runs without
            # spaces between words, so a wrapped line breaks wherever a space
            # happens to fall — usually mid-sentence, sometimes mid-phrase.
            "--wrap=none",
        ],
        raw,
    ).decode("utf-8")


def has_media(raw: bytes) -> bool:
    """Whether a ``.docx`` carries images.

    Images have nowhere to go: the Markdown comes back as a single file, so a
    picture would become a link to something the browser never received.  They
    are dropped, and this is how the page knows to say so.
    """
    try:
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            return any(name.startswith("word/media/") for name in archive.namelist())
    except zipfile.BadZipFile:
        return False
