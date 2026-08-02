"""Repair Thai words whose number-word syllables were rewritten as digits."""

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from pythainlp.util import normalize

from .lexicon import (
    THAI_LETTER,
    covering_word,
    default_lexicon,
    is_thai_letter,
    oov_count,
    token_span,
)
from .numbers import COEFFICIENTS, MAGNITUDES, joined_value, readings

#: A magnitude word with digits stuck to it — the ITN step converted the tail of
#: a numeral but not its head, leaving "พัน 200" where "1,200" belongs.
_MAGNITUDE_RE = re.compile(f"({'|'.join(MAGNITUDES)})[ \t]*([0-9][0-9,]*)")

#: Another magnitude right after the digits means they are that one's
#: coefficient, not a remainder: "ล้าน93 ล้าน" is two quantities, not 1,000,093.
_MAGNITUDE_AHEAD_RE = re.compile(f"[ \t]*(?:{'|'.join(MAGNITUDES)})")

#: A digit run with Thai letters on both sides, optionally spaced off from
#: them.  The ITN step sometimes padded its replacement, so the same corruption
#: shows up as "ความเ4ยง", "ความเ 4 ยง" or "ความ 3ารถ"; any spaces caught here
#: are part of the damage and get removed along with the digits.
STRICT_RE = re.compile(
    f"(?<=[{THAI_LETTER}])[ \t]*[0-9]+[ \t]*(?=[{THAI_LETTER}])"
)

#: Opt-in: also consider digits touching Thai on only one side.  This catches
#: corruption at a word edge but will also fire on legitimate unspaced text
#: such as "45บาท", so every hit needs review.
LOOSE_RE = re.compile(
    f"(?<=[{THAI_LETTER}])[ \t]*[0-9]+|[0-9]+[ \t]*(?=[{THAI_LETTER}])"
)

#: Context, in characters, used to judge whether a repair improved segmentation.
WINDOW_PAD = 40

CONFIDENCES = ("override", "high", "ambiguous", "unresolved")

#: A maximal stretch of Thai letters and digits — i.e. one candidate word,
#: never spanning whitespace.
_WORD_RUN_RE = re.compile(f"[{THAI_LETTER}0-9]+")

#: Base consonants ก-ฮ.  A run without one is not a syllable.
_CONSONANT_RE = re.compile(r"[ก-ฮ]")


def normalize_words(text: str) -> Tuple[str, List["Change"]]:
    """Apply PyThaiNLP normalization to each word, leaving layout untouched.

    ``pythainlp.util.normalize`` strips and collapses whitespace, which would
    destroy line structure in a transcript, so it is scoped to word runs where
    it can only do what we actually want: reorder tone marks and drop
    duplicated vowels (เเปลก -> แปลก, กำำ -> กำ).

    Runs holding no base consonant are skipped.  ASR output strands diacritics
    between spaces — "สมบูร ณ ์" leaves "์" standing alone — and normalizing an
    orphan mark deletes it, because a diacritic with nothing to attach to is not
    a valid syllable.  Dropping it would destroy the only evidence of what the
    word was.
    """
    changes: List["Change"] = []
    out = []
    pos = 0
    for match in _WORD_RUN_RE.finditer(text):
        run = match.group()
        fixed = normalize(run) if _CONSONANT_RE.search(run) else run
        if fixed != run:
            line, col = _line_col(text, match.start())
            changes.append(Change(line, col, run, fixed, "high", "normalize"))
        out.append(text[pos : match.start()])
        out.append(fixed)
        pos = match.end()
    out.append(text[pos:])
    return "".join(out), changes


@dataclass(frozen=True)
class Change:
    """One edit (or one refusal to edit), for the human-readable report."""

    line: int
    col: int
    before: str
    after: str
    confidence: str
    rule: str

    @property
    def applied(self) -> bool:
        return self.confidence != "unresolved"


def load_overrides(path: Path) -> Dict[str, str]:
    """Read ``wrong -> correct`` pairs from a ``correct,wrong`` CSV."""
    overrides: Dict[str, str] = {}
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            correct = (row.get("correct") or "").strip()
            wrong = (row.get("wrong") or "").strip()
            if correct and wrong and correct != wrong:
                overrides[wrong] = correct
    return overrides


def _line_col(text: str, offset: int) -> Tuple[int, int]:
    line = text.count("\n", 0, offset) + 1
    col = offset - (text.rfind("\n", 0, offset) + 1) + 1
    return line, col


def _apply_overrides(text: str, overrides: Dict[str, str]) -> Tuple[str, List[Change]]:
    """Exact replacements from the curated CSV, longest pattern first."""
    changes: List[Change] = []
    for wrong in sorted(overrides, key=len, reverse=True):
        correct = overrides[wrong]
        cursor = 0
        while True:
            idx = text.find(wrong, cursor)
            if idx < 0:
                break
            line, col = _line_col(text, idx)
            changes.append(Change(line, col, wrong, correct, "override", "csv"))
            text = text[:idx] + correct + text[idx + len(wrong) :]
            cursor = idx + len(correct)
    return text, changes


def _thai_run(text: str, lo: int, hi: int) -> Tuple[int, int]:
    """Widen ``[lo, hi)`` over adjacent Thai letters, for reporting context."""
    while lo > 0 and is_thai_letter(text[lo - 1]):
        lo -= 1
    while hi < len(text) and is_thai_letter(text[hi]):
        hi += 1
    return lo, hi


def join_magnitude_words(text: str) -> Tuple[str, List[Change]]:
    """Fold a leftover Thai magnitude word into the digits it belongs to.

    ``มากกว่าพัน 200 ทุน`` -> ``มากกว่า 1,200 ทุน``.  Only the canonical form is
    folded; readings that Thai itself leaves ambiguous (``หมื่น 5``, ``ล้าน 6``)
    are left untouched and put on the review queue instead of being guessed at,
    because getting a figure wrong in a budget transcript is worse than leaving
    it as the speaker said it.
    """
    changes: List[Change] = []
    out = []
    pos = 0
    for match in _MAGNITUDE_RE.finditer(text):
        word, digits_text = match.groups()
        if _MAGNITUDE_AHEAD_RE.match(text, match.end()):
            continue
        line, col = _line_col(text, match.start())
        value = joined_value(MAGNITUDES[word], int(digits_text.replace(",", "")))
        if value is None or text[: match.start()].endswith(COEFFICIENTS):
            # Ambiguous, or the magnitude already has a coefficient of its own
            # (สองพัน 200).  Flag it and change nothing.
            changes.append(
                Change(line, col, match.group(), match.group(), "unresolved", "numword")
            )
            continue
        joined = f"{value:,}"
        changes.append(Change(line, col, match.group(), joined, "high", "numword"))
        out.append(text[pos : match.start()])
        out.append(joined)
        pos = match.end()
    out.append(text[pos:])
    return "".join(out), changes


def space_numbers(text: str) -> Tuple[str, List[Change]]:
    """Separate genuine numbers from the Thai text they are flush against.

    Run after repair, so every digit still standing is a real number.  Only
    Thai-letter neighbours count, which leaves timestamps (``00:04:45``),
    decimals (``5.2``), ratios (``1/12``), percentages (``18%10``) and Latin
    tokens (``part2.m4a``) exactly as they were.
    """
    changes: List[Change] = []
    out = []
    pos = 0
    # Group-separated numbers count as one token, so 1,200 is spaced as a whole
    # rather than as "1" and "200".
    for match in re.finditer(r"[0-9]+(?:,[0-9]+)*", text):
        start, end = match.span()
        pad_left = start > 0 and is_thai_letter(text[start - 1])
        pad_right = end < len(text) and is_thai_letter(text[end])
        if not (pad_left or pad_right):
            continue
        spaced = f"{' ' if pad_left else ''}{match.group()}{' ' if pad_right else ''}"
        lo, hi = _thai_run(text, start, end)
        line, col = _line_col(text, lo)
        changes.append(
            Change(
                line,
                col,
                text[lo:hi],
                text[lo:start] + spaced + text[end:hi],
                "high",
                "spacing",
            )
        )
        out.append(text[pos:start])
        out.append(spaced)
        pos = end
    out.append(text[pos:])
    return "".join(out), changes


def _context_window(text: str, start: int, end: int) -> Tuple[int, int]:
    """A whitespace-aligned slice around ``[start, end)`` to score segmentation in.

    Aligning both edges to whitespace keeps the window comparable before and
    after the substitution, and stops a clipped word from registering as a
    fake segmentation defect.
    """
    lo = max(0, start - WINDOW_PAD)
    hi = min(len(text), end + WINDOW_PAD)
    limit = 0
    while lo > 0 and not text[lo - 1].isspace() and limit < WINDOW_PAD:
        lo -= 1
        limit += 1
    limit = 0
    while hi < len(text) and not text[hi].isspace() and limit < WINDOW_PAD:
        hi += 1
        limit += 1
    return lo, hi


def _repair_digits(
    text: str, lexicon: Set[str], aggressive: bool
) -> Tuple[str, List[Change]]:
    pattern = LOOSE_RE if aggressive else STRICT_RE
    changes: List[Change] = []
    cursor = 0

    while True:
        match = pattern.search(text, cursor)
        if match is None:
            return text, changes

        start, end = match.span()
        digits = match.group().strip()
        win_lo, win_hi = _context_window(text, start, end)
        before_oov = oov_count(text[win_lo:win_hi], lexicon)

        # A candidate has to clear two independent bars.  It must land inside a
        # real word — and one bigger than the substituted reading itself, or it
        # says nothing about the surrounding characters.  And it must repair a
        # segmentation defect: genuine numbers such as "ได้ 18 ท่าน" already sit
        # between well-formed words, so spelling them out improves nothing and
        # they are left alone.
        candidates = []
        for rank, reading in enumerate(readings(digits)):
            patched = text[:start] + reading + text[end:]
            stop = start + len(reading)
            found = covering_word(patched, start, stop, lexicon)
            if found is None or len(found.word) == len(reading):
                continue
            # Rebuild the word in place; this also closes up any spaces the ITN
            # step left inside it.
            fixed = patched[: found.start] + found.word + patched[found.end :]
            shift = len(fixed) - len(text)
            after_oov = oov_count(fixed[win_lo : win_hi + shift], lexicon)
            if after_oov >= before_oov:
                continue
            candidates.append(
                (before_oov - after_oov, len(found.word), -rank, reading, found, fixed)
            )

        if not candidates:
            # Only flag the tight form for review.  With spaces present the
            # overwhelmingly likely reading is an ordinary number, and reporting
            # every one of those would bury the real cases.
            if match.group() == digits:
                lo, hi = _thai_run(text, start, end)
                line, col = _line_col(text, lo)
                changes.append(
                    Change(line, col, text[lo:hi], text[lo:hi], "unresolved", "digit")
                )
            cursor = end
            continue

        candidates.sort(reverse=True)
        _, _, _, reading, found, fixed = candidates[0]
        stop = start + len(reading)
        word_start, word_end, word = found

        if word_end - word_start == len(word):
            # No spaces were closed, so offsets in `fixed` still line up with the
            # substitution and the tokenizer can arbitrate between two lexicon
            # words that both contain it (ผู้ที่สามารถ holds ที่สาม and สามารถ) —
            # but never let it shorten the match, since it stops happily at a
            # prefix that is also a word (เสี่ย ⊂ เสี่ยง).
            shift = len(fixed) - len(text)
            tok_lo, tok_hi = token_span(
                fixed[win_lo : win_hi + shift], start - win_lo, stop - win_lo
            )
            tok_lo += win_lo
            tok_hi += win_lo
            if tok_lo <= start and tok_hi >= stop and tok_hi - tok_lo >= len(word):
                word_start, word_end, word = tok_lo, tok_hi, fixed[tok_lo:tok_hi]

        # Map the repaired word back onto the pre-repair text for the report.
        before = text[word_start : word_end - len(reading) + (end - start)]
        line, col = _line_col(text, word_start)
        confidence = "high" if len(candidates) == 1 else "ambiguous"
        changes.append(Change(line, col, before, word, confidence, "digit"))

        text = fixed
        cursor = word_start + len(word)


def repair_text(
    text: str,
    overrides: Optional[Dict[str, str]] = None,
    lexicon: Optional[Set[str]] = None,
    aggressive: bool = False,
    do_normalize: bool = True,
    do_space_numbers: bool = True,
) -> Tuple[str, List[Change]]:
    """Repair ``text`` and return the result plus a log of every decision.

    Tiers are applied in order of trust: the curated CSV wins outright, then
    digit expansion validated against the lexicon.  Digits that look corrupted
    but resolve to nothing are left alone and reported as ``unresolved``.

    Spacing runs last, once the surviving digits are known to be real numbers.
    """
    changes: List[Change] = []
    if do_normalize:
        # ASR output often has reordered tone marks or duplicated vowels, which
        # would make otherwise-correct candidates miss in the lexicon.
        text, normalize_changes = normalize_words(text)
        changes.extend(normalize_changes)
    if lexicon is None:
        lexicon = default_lexicon()

    if overrides:
        text, override_changes = _apply_overrides(text, overrides)
        changes.extend(override_changes)

    # Before the digit tier, so "พัน200ทุน" is already "1,200ทุน" and its digits
    # are no longer a glued-between-Thai site to puzzle over.
    text, magnitude_changes = join_magnitude_words(text)
    changes.extend(magnitude_changes)

    text, digit_changes = _repair_digits(text, lexicon, aggressive)
    changes.extend(digit_changes)

    if do_space_numbers:
        text, spacing_changes = space_numbers(text)
        changes.extend(spacing_changes)

    # Tiers run in trust order, so collect them in reading order for the report.
    changes.sort(key=lambda c: (c.line, c.col))
    return text, changes


def write_report(path: Path, changes: Sequence[Change]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["line", "col", "before", "after", "confidence", "rule"])
        for change in changes:
            writer.writerow(
                [
                    change.line,
                    change.col,
                    change.before,
                    change.after,
                    change.confidence,
                    change.rule,
                ]
            )
