"""Thai lexicon lookups used to decide whether a repair produced a real word."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import AbstractSet, FrozenSet, List, NamedTuple, Optional, Tuple

from pythainlp.corpus import thai_words
from pythainlp.tokenize import word_tokenize

#: Thai letters, vowels, tone marks and diacritics — but *not* Thai digits
#: (๐-๙, U+0E50-U+0E59) and not ๏/฿, which never appear mid-word.
THAI_LETTER = r"ก-๎"

_THAI_LETTER_RE = re.compile(f"[{THAI_LETTER}]")

#: Longest word we will try to reconstruct around a digit.  The longest entry
#: in the PyThaiNLP lexicon is well under this; going wider only adds noise.
MAX_WORD_LEN = 24

#: A lexicon is read-only everywhere it is passed, so it travels as an
#: ``AbstractSet`` and is built as a ``frozenset``: the default one is cached
#: and handed to every pass, and a shared mutable set would be a trap.
Lexicon = AbstractSet[str]


def is_thai_letter(ch: str) -> bool:
    return bool(_THAI_LETTER_RE.match(ch))


@lru_cache(maxsize=1)
def default_lexicon() -> FrozenSet[str]:
    """The PyThaiNLP word list (~62k entries), loaded once."""
    return frozenset(thai_words())


#: Single spaces the ITN step scattered through the wreckage may be closed up
#: while reconstructing a word ("ไม่3 า รถ" -> "ไม่สามารถ").  Two in a row are
#: left alone: that is real phrase separation, not damage.
MAX_JOINED_SPACES = 2

SKIPPABLE_SPACE = " \t"


class WordMatch(NamedTuple):
    """A lexicon word found around a repair site.

    ``word`` is the reconstructed word and ``[start, end)`` the span of ``text``
    it replaces.  The two differ in length when spaces were closed up.
    """

    start: int
    end: int
    word: str


def _left_indices(text: str, lo: int) -> List[int]:
    """Indices of word characters running leftwards from ``lo``, ascending."""
    indices: List[int] = []
    gaps = 0
    i = lo - 1
    while i >= 0 and len(indices) < MAX_WORD_LEN:
        if is_thai_letter(text[i]):
            indices.append(i)
            i -= 1
        elif (
            text[i] in SKIPPABLE_SPACE
            and gaps < MAX_JOINED_SPACES
            and i > 0
            and is_thai_letter(text[i - 1])
        ):
            gaps += 1
            i -= 1
        else:
            break
    indices.reverse()
    return indices


def _right_indices(text: str, hi: int) -> List[int]:
    """Indices of word characters running rightwards from ``hi``, ascending."""
    indices: List[int] = []
    gaps = 0
    i = hi
    while i < len(text) and len(indices) < MAX_WORD_LEN:
        if is_thai_letter(text[i]):
            indices.append(i)
            i += 1
        elif (
            text[i] in SKIPPABLE_SPACE
            and gaps < MAX_JOINED_SPACES
            and i + 1 < len(text)
            and is_thai_letter(text[i + 1])
        ):
            gaps += 1
            i += 1
        else:
            break
    return indices


def covering_word(
    text: str, lo: int, hi: int, lexicon: Lexicon
) -> Optional[WordMatch]:
    """Find the longest lexicon word around ``[lo, hi)`` that fully contains it.

    Boundaries extend across Thai letters and, at most twice, across a single
    space — the ITN step left spaces strewn through the words it broke, so
    ``เค้า 3 า รถ`` has to be readable as ``เค้าสามารถ``.  Everything else
    (digits, punctuation, a double space, a newline) stops the search, so a
    candidate can never span a real phrase break.

    Returns ``None`` when the region does not sit inside any known word.
    """
    core = text[lo:hi]
    left = _left_indices(text, lo)
    right = _right_indices(text, hi)

    best: Optional[WordMatch] = None
    for take_left in range(len(left) + 1):
        prefix_idx: List[int] = left[len(left) - take_left :] if take_left else []
        prefix = "".join(text[i] for i in prefix_idx)
        for take_right in range(len(right), -1, -1):
            suffix_idx = right[:take_right]
            word = prefix + core + "".join(text[i] for i in suffix_idx)
            if len(word) > MAX_WORD_LEN:
                continue
            if best is not None and len(word) <= len(best.word):
                break
            if word in lexicon:
                best = WordMatch(
                    prefix_idx[0] if prefix_idx else lo,
                    suffix_idx[-1] + 1 if suffix_idx else hi,
                    word,
                )
                break
    return best


def word_tokens(text: str) -> List[str]:
    """Segment ``text`` into words, dropping whitespace."""
    return word_tokenize(text, engine="newmm")


def _token_spans(text: str) -> List[Tuple[int, int]]:
    spans: List[Tuple[int, int]] = []
    pos = 0
    for token in word_tokenize(text, engine="newmm", keep_whitespace=True):
        spans.append((pos, pos + len(token)))
        pos += len(token)
    return spans


def oov_count(text: str, lexicon: Lexicon) -> int:
    """How many Thai tokens in ``text`` are not real words.

    This is the segmentation-defect metric.  A digit that swallowed part of a
    word leaves an orphan fragment behind (``ไม่ 3 ารถ`` -> ``ารถ``), so
    repairing it drives this number down; a digit that is a genuine number sits
    between two well-formed words and repairing it changes nothing.
    """
    return sum(
        1
        for token in word_tokens(text)
        if _THAI_LETTER_RE.search(token) and token not in lexicon
    )


def token_span(text: str, lo: int, hi: int) -> Tuple[int, int]:
    """Span of the token(s) that ``[lo, hi)`` falls inside.

    Used for reporting: once a repair is applied the text segments cleanly, so
    the tokenizer names the repaired word better than a lexicon scan can.  Two
    different lexicon words can contain the same substitution — ``ผู้ที่สามารถ``
    holds both ``ที่สาม`` and ``สามารถ`` — and only the tokenizer knows which
    one the sentence actually means.
    """
    covering = [sp for sp in _token_spans(text) if sp[0] < hi and sp[1] > lo]
    if not covering:
        return lo, hi
    return covering[0][0], covering[-1][1]
