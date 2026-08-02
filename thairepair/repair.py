"""Repair Thai words whose number-word syllables were rewritten as digits."""

import csv
import io
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pythainlp.util import normalize

from .lexicon import (
    THAI_LETTER,
    Lexicon,
    WordMatch,
    covering_word,
    default_lexicon,
    is_thai_letter,
    oov_count,
    token_span,
    word_tokens,
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

#: Two or more Thai fragments separated by single spaces.  A double space ends
#: the run, so genuine phrase separation is never swallowed.
_PHRASE_RE = re.compile(f"[{THAI_LETTER}]+(?:[ \t][{THAI_LETTER}]+)+")

_FRAGMENT_RE = re.compile(f"[{THAI_LETTER}]+")

#: How many space-separated fragments may be pulled back into one word.
MAX_JOIN_FRAGMENTS = 4

#: A single space between two Thai letters — the kind the ASR sprinkles between
#: tokens.  A double space is left alone; that is real phrase separation.
_INNER_SPACE_RE = re.compile(f"(?<=[{THAI_LETTER}])[ \t](?=[{THAI_LETTER}])")

#: A line whose Thai runs are this short on average was emitted with a space
#: between nearly every token.  On a real transcript the two populations
#: separate cleanly: over-spaced lines top out at a mean of 5.7 characters and
#: ordinary lines start at 13.1, so anything in between is a safe cut.
OVERSPACED_MEAN_FRAGMENT = 7.0

#: Enough fragments for that mean to carry any weight.
OVERSPACED_MIN_FRAGMENTS = 8

CONFIDENCES: tuple[str, ...] = ("override", "high", "ambiguous", "unresolved")

#: A maximal stretch of Thai letters and digits — i.e. one candidate word,
#: never spanning whitespace.
_WORD_RUN_RE = re.compile(f"[{THAI_LETTER}0-9]+")

#: Base consonants ก-ฮ.  A run without one is not a syllable.
_CONSONANT_RE = re.compile(r"[ก-ฮ]")

#: A digit run, group separators included, as one token.
_NUMBER_RE = re.compile(r"[0-9]+(?:,[0-9]+)*")


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


#: A join candidate, ranked by ``(is_lexicon_word, tokens_collapsed, -width)``
#: and carrying the fragment range it covers plus the joined string.
type _JoinScore = tuple[bool, int, int]
type _JoinCandidate = tuple[_JoinScore, int, int, str]

#: A digit-repair candidate, ranked by ``(oov_removed, word_len, -reading_rank)``
#: and carrying the reading tried, the word it landed in, and the patched text.
type _DigitCandidate = tuple[int, int, int, str, WordMatch, str]


def normalize_words(text: str) -> tuple[str, list[Change]]:
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
    changes: list[Change] = []
    out: list[str] = []
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


def load_overrides(path: Path) -> dict[str, str]:
    """Read ``wrong -> correct`` pairs from a ``correct,wrong`` CSV."""
    overrides: dict[str, str] = {}
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            correct = (row.get("correct") or "").strip()
            wrong = (row.get("wrong") or "").strip()
            if correct and wrong and correct != wrong:
                overrides[wrong] = correct
    return overrides


def _line_col(text: str, offset: int) -> tuple[int, int]:
    line = text.count("\n", 0, offset) + 1
    col = offset - (text.rfind("\n", 0, offset) + 1) + 1
    return line, col


def _apply_overrides(
    text: str, overrides: dict[str, str]
) -> tuple[str, list[Change]]:
    """Exact replacements from the curated CSV, longest pattern first."""
    changes: list[Change] = []
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


def _thai_run(text: str, lo: int, hi: int) -> tuple[int, int]:
    """Widen ``[lo, hi)`` over adjacent Thai letters, for reporting context."""
    while lo > 0 and is_thai_letter(text[lo - 1]):
        lo -= 1
    while hi < len(text) and is_thai_letter(text[hi]):
        hi += 1
    return lo, hi


def join_split_words(text: str, lexicon: Lexicon) -> tuple[str, list[Change]]:
    """Close up spaces the ASR inserted inside a word.

    This output puts a space between nearly every token, and some of those
    splits land mid-word: ``ประสบการ ณ ์``, ``บริษั ท``, ``ขอข ยาย``.  The tell is
    the one used everywhere else here — a split word leaves an orphan behind.
    An orphan is a fragment that cannot be read as words at all, which is a
    stricter thing than "not a single word": ``พอเรา`` and ``ดูว่า`` are two words
    run together, perfectly readable, and nothing about them is broken.

    Repair grows outward from each orphan, smallest window first, and stops at
    the first join that yields something real — a lexicon word, or a string that
    segments into nothing but known words, which is what rescues ``ขอข ยาย`` ->
    ``ขอขยาย`` where the space was merely in the wrong place.  Fragments that
    are already fine are never pulled in, so ``เท็จ จริง`` and ``นะ ครับ`` keep
    their spacing.
    """
    stats: dict[str, tuple[bool, int]] = {}

    def analyse(fragment: str) -> tuple[bool, int]:
        """Whether ``fragment`` reads as known words, and how many it takes."""
        if fragment not in stats:
            tokens = word_tokens(fragment)
            stats[fragment] = (all(t in lexicon for t in tokens), len(tokens))
        return stats[fragment]

    def is_readable(fragment: str) -> bool:
        return analyse(fragment)[0]

    changes: list[Change] = []
    out: list[str] = []
    pos = 0

    for phrase in _PHRASE_RE.finditer(text):
        body = phrase.group()
        spans = [m.span() for m in _FRAGMENT_RE.finditer(body)]
        fragments = [body[a:b] for a, b in spans]
        separators = [
            body[spans[j][1] : spans[j + 1][0]] for j in range(len(spans) - 1)
        ]
        count = len(fragments)

        joins: dict[int, int] = {}  # start index -> end index of an accepted join
        taken = [False] * count

        orphaned = [not is_readable(fragment) for fragment in fragments]

        for orphan in range(count):
            if taken[orphan] or not orphaned[orphan]:
                continue
            best: _JoinCandidate | None = None
            for size in range(2, MAX_JOIN_FRAGMENTS + 1):
                for start in range(orphan - size + 1, orphan + 1):
                    stop = start + size
                    if start < 0 or stop > count or any(taken[start:stop]):
                        continue
                    joined = "".join(fragments[start:stop])
                    readable, joined_tokens = analyse(joined)
                    if not (joined in lexicon or readable):
                        continue
                    # The join has to mend more than it breaks.  "กกต" glued to
                    # "อย่างนั้นเนี่ย" re-segments as กก|ตอ|ย่าง|นั้น|เนี่ย — every
                    # piece is a lexicon entry, but it shredded อย่าง, which was
                    # fine before.  Real repairs collapse fragments into fewer
                    # words, never more.
                    before_tokens = sum(analyse(f)[1] for f in fragments[start:stop])
                    if joined_tokens >= before_tokens:
                        continue
                    # A real dictionary word outranks everything: it is the
                    # strongest evidence that this exact span was one word.  Raw
                    # collapse cannot lead, or a wider window would always win by
                    # merging more — "ได้มี ประสบการ ณ ์" would become one blob
                    # instead of mending ประสบการณ์ and leaving ได้มี alone.
                    score: _JoinScore = (
                        joined in lexicon,
                        before_tokens - joined_tokens,
                        -size,
                    )
                    if best is None or score > best[0]:
                        best = (score, start, stop, joined)
            if best is None:
                continue
            _, best_start, best_stop, best_joined = best
            offset = phrase.start() + spans[best_start][0]
            line, col = _line_col(text, offset)
            original = body[spans[best_start][0] : spans[best_stop - 1][1]]
            changes.append(Change(line, col, original, best_joined, "high", "join"))
            joins[best_start] = best_stop
            for index in range(best_start, best_stop):
                taken[index] = True

        parts: list[str] = []
        index = 0
        while index < count:
            stop = joins.get(index, index + 1)
            parts.append("".join(fragments[index:stop]))
            if stop - 1 < len(separators):
                parts.append(separators[stop - 1])
            index = stop

        out.append(text[pos : phrase.start()])
        out.append("".join(parts))
        pos = phrase.end()

    out.append(text[pos:])
    return "".join(out), changes


def is_overspaced(line: str) -> bool:
    """True when a line was emitted with a space between nearly every token."""
    fragments = _FRAGMENT_RE.findall(line)
    if len(fragments) < OVERSPACED_MIN_FRAGMENTS:
        return False
    mean = sum(len(fragment) for fragment in fragments) / len(fragments)
    return mean < OVERSPACED_MEAN_FRAGMENT


def collapse_overspaced_lines(text: str) -> tuple[str, list[Change]]:
    """Remove inter-word spaces on lines that have a space between every token.

    Thai prose runs words together and uses spaces to break phrases, so these
    lines are only readable once the token spacing comes out.  The test is
    applied per line, which keeps it away from lines that were already fine —
    their phrase breaks carry meaning and are none of this function's business.
    """
    changes: list[Change] = []
    lines = text.split("\n")
    for index, line in enumerate(lines):
        if not is_overspaced(line):
            continue
        collapsed = _INNER_SPACE_RE.sub("", line)
        if collapsed != line:
            changes.append(Change(index + 1, 1, line, collapsed, "high", "collapse"))
            lines[index] = collapsed
    return "\n".join(lines), changes


def join_magnitude_words(text: str) -> tuple[str, list[Change]]:
    """Fold a leftover Thai magnitude word into the digits it belongs to.

    ``มากกว่าพัน 200 ทุน`` -> ``มากกว่า 1,200 ทุน``.  Only the canonical form is
    folded; readings that Thai itself leaves ambiguous (``หมื่น 5``, ``ล้าน 6``)
    are left untouched and put on the review queue instead of being guessed at,
    because getting a figure wrong in a budget transcript is worse than leaving
    it as the speaker said it.
    """
    changes: list[Change] = []
    out: list[str] = []
    pos = 0
    for match in _MAGNITUDE_RE.finditer(text):
        # Both groups are unconditional in the pattern, so they always matched.
        word: str = match.group(1)
        digits_text: str = match.group(2)
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


def space_numbers(text: str) -> tuple[str, list[Change]]:
    """Separate genuine numbers from the Thai text they are flush against.

    Run after repair, so every digit still standing is a real number.  Only
    Thai-letter neighbours count, which leaves timestamps (``00:04:45``),
    decimals (``5.2``), ratios (``1/12``), percentages (``18%10``) and Latin
    tokens (``part2.m4a``) exactly as they were.
    """
    changes: list[Change] = []
    out: list[str] = []
    pos = 0
    # Group-separated numbers count as one token, so 1,200 is spaced as a whole
    # rather than as "1" and "200".
    for match in _NUMBER_RE.finditer(text):
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


def _context_window(text: str, start: int, end: int) -> tuple[int, int]:
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
    text: str, lexicon: Lexicon, aggressive: bool
) -> tuple[str, list[Change]]:
    pattern = LOOSE_RE if aggressive else STRICT_RE
    changes: list[Change] = []
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
        candidates: list[_DigitCandidate] = []
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
    overrides: dict[str, str] | None = None,
    lexicon: Lexicon | None = None,
    aggressive: bool = False,
    do_normalize: bool = True,
    do_space_numbers: bool = True,
    do_join_words: bool = True,
    do_collapse_spaces: bool = True,
) -> tuple[str, list[Change]]:
    """Repair ``text`` and return the result plus a log of every decision.

    Tiers are applied in order of trust: the curated CSV wins outright, then
    digit expansion validated against the lexicon.  Digits that look corrupted
    but resolve to nothing are left alone and reported as ``unresolved``.

    Spacing runs last, once the surviving digits are known to be real numbers.
    """
    changes: list[Change] = []
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

    # After the digit tier, which has its own handling of spaces stranded around
    # a digit and is calibrated on the original spacing.
    if do_join_words:
        text, join_changes = join_split_words(text, lexicon)
        changes.extend(join_changes)

    # After joining, which is the precise tool: it names the word it mended,
    # while this only reports that a line was closed up.
    if do_collapse_spaces:
        text, collapse_changes = collapse_overspaced_lines(text)
        changes.extend(collapse_changes)

    if do_space_numbers:
        text, spacing_changes = space_numbers(text)
        changes.extend(spacing_changes)

    # Tiers run in trust order, so collect them in reading order for the report.
    changes.sort(key=lambda c: (c.line, c.col))
    return text, changes


def report_csv(changes: Sequence[Change]) -> str:
    """The report as one CSV string, for callers that do not want a file."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
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
    return buffer.getvalue()


def write_report(path: Path, changes: Sequence[Change]) -> None:
    # newline="" so the csv module's own \r\n line endings survive untranslated.
    path.write_text(report_csv(changes), encoding="utf-8", newline="")
