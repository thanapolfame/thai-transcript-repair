"""Thai readings for Arabic digits.

The corruption this package repairs is an inverse-text-normalization (ITN) step
that rewrote Thai number words as digits without checking word boundaries, so
number words buried inside ordinary words got eaten too::

    เสี่ยง  ->  เ4ยง        (สี่ = 4)
    อุตสาหกรรม -> อุตสา6รรม  (หก = 6)

To undo it we need every way a digit run could have been spelled out.
"""

from itertools import product
from typing import Iterator

from pythainlp.util import num_to_thaiword

#: Readings for a single digit, most common first.  1 and 2 are genuinely
#: ambiguous in Thai: เอ็ด/ยี่ are the forms used inside compound numerals
#: (ยี่สิบเอ็ด = 21), หนึ่ง/สอง the standalone forms.
DIGIT_READINGS = {
    "0": ("ศูนย์",),
    "1": ("หนึ่ง", "เอ็ด"),
    "2": ("สอง", "ยี่"),
    "3": ("สาม",),
    "4": ("สี่",),
    "5": ("ห้า",),
    "6": ("หก",),
    "7": ("เจ็ด",),
    "8": ("แปด",),
    "9": ("เก้า",),
}

#: Above this many digits the per-digit cartesian product stops being worth it.
_MAX_CARTESIAN_DIGITS = 4


def readings(digits: str) -> Iterator[str]:
    """Yield plausible Thai spellings of ``digits``, best guess first.

    Two families are produced: the numeral reading of the run as a whole
    (``45`` -> ``สี่สิบห้า``) and the digit-by-digit concatenation
    (``45`` -> ``สี่ห้า``).  Which one is right depends on how the ITN step
    tokenized the original text, so both are offered and the caller picks
    whichever yields a real word.
    """
    seen = set()

    if len(digits) > 1:
        try:
            whole = num_to_thaiword(int(digits))
        except (ValueError, TypeError):
            whole = ""
        if whole:
            seen.add(whole)
            yield whole

    if len(digits) > _MAX_CARTESIAN_DIGITS:
        return

    for combo in product(*(DIGIT_READINGS[d] for d in digits)):
        candidate = "".join(combo)
        if candidate not in seen:
            seen.add(candidate)
            yield candidate
