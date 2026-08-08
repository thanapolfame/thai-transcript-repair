"""The .md ⇄ .docx converter.

Every test that actually converts is skipped where pandoc is absent, so the
suite still passes on a machine that has only ever repaired transcripts.  The
tests that do not need it — the direction table, the rejections — always run,
because those are the paths a user hits *before* pandoc matters.
"""

import zipfile
from io import BytesIO

import pytest

from thairepair import pandoc
from thairepair.convert import docx_to_md, has_media, md_to_docx
from thairepair.webgui import convert_upload

needs_pandoc = pytest.mark.skipif(
    not pandoc.available(), reason="pandoc is not installed"
)

SAMPLE = """# รายงานการประชุม

ข้อความ **หนา** และ *เอียง* กับ `code`

- ข้อหนึ่ง
- ข้อสอง
  - ข้อย่อย

1. ลำดับหนึ่ง
2. ลำดับสอง

> คำพูดอ้างอิง

| คำ | จำนวน |
|----|-------|
| ความเสี่ยง | 3 |
"""


# --- the round trip ---------------------------------------------------------


@needs_pandoc
def test_markdown_survives_a_round_trip_through_word() -> None:
    """Thai text and every structural marker come back from the .docx."""
    returned = docx_to_md(md_to_docx(SAMPLE))

    assert "# รายงานการประชุม" in returned
    assert "**หนา**" in returned
    assert "*เอียง*" in returned
    assert "`code`" in returned
    assert "ข้อย่อย" in returned          # the nested bullet
    assert "ลำดับสอง" in returned          # the numbered list
    assert "> คำพูดอ้างอิง" in returned
    assert "ความเสี่ยง" in returned        # the table cell


@needs_pandoc
def test_thai_is_not_hard_wrapped() -> None:
    """``--wrap=none`` holds: a long Thai line comes back as one line.

    Thai has no spaces between words, so pandoc's default 72-column wrap breaks
    at whatever space it can find — which is mid-sentence.
    """
    sentence = "ความเสี่ยงของการดำเนินงาน " * 12
    returned = docx_to_md(md_to_docx(sentence))
    assert len([line for line in returned.splitlines() if line.strip()]) == 1


@needs_pandoc
def test_output_is_a_word_document() -> None:
    raw = md_to_docx("# หัวข้อ")
    assert raw.startswith(b"PK")
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        assert "word/document.xml" in archive.namelist()


# --- refusals ---------------------------------------------------------------


def test_junk_is_rejected_before_pandoc_sees_it() -> None:
    """Not a zip, so not a .docx — named here rather than inside pandoc."""
    with pytest.raises(ValueError, match="docx"):
        docx_to_md(b"this is not a document")


def test_txt_is_sent_to_the_repair_tab() -> None:
    """A transcript on the converter tab is a mistake worth naming precisely."""
    with pytest.raises(ValueError, match="repair tab"):
        convert_upload("ข้อความ".encode(), "transcript.txt")


def test_an_unsupported_extension_is_refused() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        convert_upload(b"...", "photo.png")


# --- the upload boundary ----------------------------------------------------


@needs_pandoc
def test_upload_picks_the_direction_from_the_extension() -> None:
    body, content_type, name = convert_upload(SAMPLE.encode(), "รายงาน.md")
    assert name == "รายงาน.docx"
    assert "wordprocessingml" in content_type
    assert body.startswith(b"PK")

    back, content_type, name = convert_upload(body, "รายงาน.docx")
    assert name == "รายงาน.md"
    assert content_type.startswith("text/markdown")
    assert "รายงานการประชุม" in back.decode("utf-8")


@needs_pandoc
def test_a_tis620_markdown_upload_is_decoded() -> None:
    """Word on a Thai Windows machine writes cp874; the upload path handles it."""
    body, _, _ = convert_upload("# หัวข้อไทย".encode("cp874"), "note.md")
    assert "หัวข้อไทย" in docx_to_md(body)


# --- images -----------------------------------------------------------------


@needs_pandoc
def test_a_document_without_images_reports_none() -> None:
    assert has_media(md_to_docx(SAMPLE)) is False


def test_has_media_finds_the_media_folder() -> None:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", "<document/>")
        archive.writestr("word/media/image1.png", b"\x89PNG")
    assert has_media(buffer.getvalue()) is True


def test_has_media_on_junk_is_false_not_an_error() -> None:
    assert has_media(b"not a zip") is False


# --- locating pandoc --------------------------------------------------------


def test_this_platform_has_a_known_asset_or_says_so() -> None:
    """``asset_suffix`` is what decides whether the page offers a button."""
    suffix = pandoc.asset_suffix()
    assert suffix is None or suffix.endswith((".zip", ".tar.gz"))


@needs_pandoc
def test_the_pandoc_found_reports_a_version() -> None:
    version = pandoc.version()
    assert version is not None and version[0].isdigit()
