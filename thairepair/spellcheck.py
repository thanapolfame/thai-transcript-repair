"""Flag Thai words that are not in the lexicon — the wrong-word report.

This pass never edits anything.  The repair tiers only touch text they can
justify, so whatever damage they could not name survives into the output; this
walks the *repaired* text — the version a human is going to read — and lists
every word the lexicon does not know, so that the review has somewhere to
start.

Two things make the list readable rather than a wall of noise:

- **Adjacent unknown tokens are merged.**  ``newmm`` segments with the same
  dictionary we check against, so a word it does not know comes back shredded
  into one- and two-character pieces.  Reporting those pieces would name
  nothing; gluing a maximal run of them back together recovers the string the
  ASR actually produced.
- **Identical words are one row, counted.**  A transcript repeats its own
  vocabulary, and a mangled proper noun that occurs 40 times is one decision,
  not 40.  That decision usually ends in a row added to
  ``resource/word-replace.csv``, which is applied document-wide anyway — so the
  aggregate is the shape of the work.
"""

import csv
import io
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .lexicon import THAI_LETTER, Lexicon, default_lexicon, spaced_word_tokens

#: A token made of nothing but Thai word characters.  Anything holding a digit,
#: Latin script or punctuation is not this pass's business: a digit left inside
#: a word is already the digit tier's ``unresolved`` queue, and reporting it
#: twice would just split the reviewer's attention.
_THAI_TOKEN_RE = re.compile(f"^[{THAI_LETTER}]+$")

#: Base consonants ก-ฮ.  A run without one is not a word — it is a stranded
#: vowel or tone mark, or a bare ๆ/ฯ, both of which live inside ``THAI_LETTER``
#: but say nothing about spelling.
_CONSONANT_RE = re.compile(r"[ก-ฮ]")

#: Characters of the surrounding line shown either side of the word.  Enough to
#: recognise the sentence, short enough to stay in one spreadsheet cell.
CONTEXT_PAD = 24

#: What replaces the part of the line the context window cut off.
ELLIPSIS = "…"


@dataclass(frozen=True)
class Misspelling:
    """One unknown word, with the evidence needed to judge it.

    ``line`` and ``col`` point at the *first* occurrence and ``context`` quotes
    it; ``count`` is how many times the word appears in the whole document.
    """

    word: str
    count: int
    line: int
    col: int
    context: str


def _line_bounds(text: str, offset: int) -> tuple[int, int]:
    """Start and end of the line ``offset`` falls on, newline excluded."""
    start = text.rfind("\n", 0, offset) + 1
    end = text.find("\n", offset)
    return start, len(text) if end < 0 else end


def _context(text: str, start: int, end: int) -> str:
    """The word quoted inside its line, clipped to ``CONTEXT_PAD`` either side.

    Clipped to the line and not to the character count alone: a window that ran
    past a newline would quote a sentence the word is not in.
    """
    line_start, line_end = _line_bounds(text, start)
    lo = max(line_start, start - CONTEXT_PAD)
    hi = min(line_end, end + CONTEXT_PAD)
    return (
        (ELLIPSIS if lo > line_start else "")
        + text[lo:hi]
        + (ELLIPSIS if hi < line_end else "")
    )


def _unknown_runs(text: str, lexicon: Lexicon) -> list[tuple[int, int]]:
    """Spans of maximal runs of adjacent Thai tokens the lexicon does not know.

    A known token ends a run, and so does anything that is not a Thai token at
    all — whitespace, punctuation, a number.  So a run never reaches across a
    word the tokenizer was sure about, and the merged string is always
    something that was written without a break.
    """
    spans: list[tuple[int, int]] = []
    pos = 0
    start: int | None = None
    for token in spaced_word_tokens(text):
        unknown = bool(_THAI_TOKEN_RE.match(token)) and token not in lexicon
        if unknown and start is None:
            start = pos
        elif not unknown and start is not None:
            spans.append((start, pos))
            start = None
        pos += len(token)
    if start is not None:
        spans.append((start, pos))
    return spans


def find_misspellings(text: str, lexicon: Lexicon | None = None) -> list[Misspelling]:
    """Every unknown word in ``text``, most frequent first.

    Ties break on position, so the order is stable and a re-run of the same
    transcript produces the same file.
    """
    if lexicon is None:
        lexicon = default_lexicon()

    counts: dict[str, int] = {}
    first: dict[str, tuple[int, int]] = {}
    for start, end in _unknown_runs(text, lexicon):
        word = text[start:end]
        if not _CONSONANT_RE.search(word):
            continue
        counts[word] = counts.get(word, 0) + 1
        first.setdefault(word, (start, end))

    found: list[Misspelling] = []
    for word, count in counts.items():
        start, end = first[word]
        line_start, _ = _line_bounds(text, start)
        found.append(
            Misspelling(
                word=word,
                count=count,
                line=text.count("\n", 0, start) + 1,
                col=start - line_start + 1,
                context=_context(text, start, end),
            )
        )
    found.sort(key=lambda m: (-m.count, m.line, m.col))
    return found


def spell_report_csv(found: Sequence[Misspelling]) -> str:
    """The wrong-word report as one CSV string.

    The string form is the primary one for the same reason as ``report_csv``:
    the GUI hands it to the browser and the CLI writes it to a file, and the two
    have to be the same bytes.
    """
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(["word", "count", "line", "col", "context"])
    for item in found:
        writer.writerow([item.word, item.count, item.line, item.col, item.context])
    return buffer.getvalue()


def write_spell_report(path: Path, found: Sequence[Misspelling]) -> None:
    # newline="" so the csv module's own \r\n line endings survive untranslated.
    path.write_text(spell_report_csv(found), encoding="utf-8", newline="")
