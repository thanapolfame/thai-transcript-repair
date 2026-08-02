import csv
from pathlib import Path

import pytest

from thairepair import load_overrides, repair_text
from thairepair.numbers import readings

WORDS_CSV = Path(__file__).parent.parent / "resource" / "word.csv"


def fix(text, **kwargs):
    return repair_text(text, **kwargs)[0]


# --- resource/word.csv is the regression suite ------------------------------


def pairs():
    with open(WORDS_CSV, encoding="utf-8-sig", newline="") as fh:
        return [(row["correct"], row["wrong"]) for row in csv.DictReader(fh)]


@pytest.mark.parametrize("correct,wrong", pairs())
def test_every_known_pair_is_repaired_without_the_override_table(correct, wrong):
    """The lexicon tier alone should already handle everything in word.csv."""
    assert fix(f"เรื่อง{wrong}นี้") == f"เรื่อง{correct}นี้"


@pytest.mark.parametrize("correct,wrong", pairs())
def test_correct_words_are_left_alone(correct, wrong):
    assert fix(f"เรื่อง{correct}นี้") == f"เรื่อง{correct}นี้"


# --- the two documented examples, in running text ---------------------------


def test_repairs_multiple_words_in_one_sentence():
    assert fix("ผมเ4ยงมากในอุตสา6รรมนี้") == "ผมเสี่ยงมากในอุตสาหกรรมนี้"


def test_reports_word_level_before_and_after():
    _, changes = repair_text("ผมเ4ยงมาก")
    assert [(c.before, c.after, c.confidence) for c in changes] == [
        ("เ4ยง", "เสี่ยง", "high")
    ]


def test_change_carries_line_and_column():
    _, changes = repair_text("บรรทัดแรก\nผมเ4ยงมาก")
    assert (changes[0].line, changes[0].col) == (2, 3)


# --- the ITN step sometimes padded its replacement with spaces --------------


@pytest.mark.parametrize(
    "corrupt,expected",
    [
        ("ความเ 4 ยง", "ความเสี่ยง"),  # spaces on both sides
        ("ความเ 4ยง", "ความเสี่ยง"),  # left only
        ("ความเ4 ยง", "ความเสี่ยง"),  # right only
        ("ความเ\t4\tยง", "ความเสี่ยง"),  # tabs count too
        ("งานไม่ 3 ารถจะทำได้", "งานไม่สามารถจะทำได้"),
        ("มีความรู้ความ 3ารถ ดี", "มีความรู้ความสามารถ ดี"),
        ("ให้มีความรัก3ัคคี", "ให้มีความรักสามัคคี"),
    ],
)
def test_repairs_spaced_and_tight_forms_alike(corrupt, expected):
    assert fix(corrupt) == expected


@pytest.mark.parametrize(
    "corrupt,expected",
    [
        # Spaces land *inside* the wreckage too, not just around the digit.
        ("เราไม่3 า รถจะไปรับผิดชอบ", "เราไม่สามารถจะไปรับผิดชอบ"),
        ("ที่เค้า 3 า รถจะทำงานได้", "ที่เค้าสามารถจะทำงานได้"),
        ("เราไม่3 า ร ถจะไป", "เราไม่สามารถจะไป"),
    ],
)
def test_closes_up_spaces_stranded_inside_a_broken_word(corrupt, expected):
    assert fix(corrupt) == expected


def test_a_double_space_is_phrase_separation_and_blocks_the_join():
    text = "เราไม่3 า  รถจะไป"
    assert fix(text, do_space_numbers=False) == text


@pytest.mark.parametrize(
    "text",
    [
        "ฉบับ สมบูร ณ ์ แล้ว",  # karan stranded by spaces
        "ว่า กร ณ ี โครงการวิจัย",  # vowel stranded by spaces
        "เรา วิจาร ณ ์ ได้",
    ],
)
def test_normalization_never_deletes_an_orphaned_diacritic(text):
    """normalize() drops a mark with no base consonant — that is data loss."""
    assert fix(text) == text


# --- what must NOT be touched ----------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "ราคา 45 บาท",  # spaced digits are a real number
        "ปี 2568 นี้",
        "COVID-19",
        "00:04:45",  # transcript timestamps
        "ห้อง 4 ชั้น 6",
    ],
)
def test_leaves_genuine_numbers_alone(text):
    assert fix(text) == text


@pytest.mark.parametrize(
    "text",
    [
        # Spaced numbers from the real transcript that the loose rule mangled
        # into สามล / ยู่ยี่ / ที่สอง before the segmentation gate existed.
        "ในช่วงปี 67 ถึง 69 นะคะ",
        "ประชุมทั้งหมดอยู่ 4 ครั้งนะครับ",
        "มูลค่าอีก 3 ล้านบาทนะครับ",
        "ความกังวลของทั้ง 2 ท่านไว้นะครับ",
        "เพิ่มจำนวนเป็น 2 เท่าครับ",
        "ส่วนประเด็นที่ 2 ที่ปู๊ดได้ดู",
        "มันแบ่งมีอยู่ 2 งบครับ",
        # ...and tight ones, which the lexicon alone would have rewritten.
        "ลงคะแนนเสียงได้18ท่าน",
        "มีคนมาฟังพูดทั้ง2ทาง",
        "ผมมาเป็นลูกบอดโดนไป4ทุ่ม",
        "จะเสร็จวันที่21มีนา",
        "ผมว่ามันมี5กลุ่มใหญ่ๆ",
    ],
)
def test_genuine_numbers_survive_because_they_break_no_words(text):
    """They stay digits; spacing them out is a separate, cosmetic step."""
    assert fix(text, do_space_numbers=False) == text


def test_unresolved_digits_are_reported_but_not_edited():
    text = "รหัสคือก4ขนะ"
    fixed, changes = repair_text(text, do_space_numbers=False)
    assert fixed == text
    assert [c.confidence for c in changes] == ["unresolved"]


# --- magnitude words the ITN step left stranded -----------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("มากกว่าพัน 200 ทุนนะครับ", "มากกว่า 1,200 ทุนนะครับ"),
        ("มีอาจารย์ตั้งพัน 200 คน", "มีอาจารย์ตั้ง 1,200 คน"),
        ("ไปทำให้มาเป็นพัน200ทุน", "ไปทำให้มาเป็น 1,200 ทุน"),
        ("มีอาจารย์เพียงร้อย46 คน", "มีอาจารย์เพียง 146 คน"),
        ("อยู่สิบ 5 คน", "อยู่ 15 คน"),
    ],
)
def test_folds_a_magnitude_word_into_the_digits_it_belongs_to(text, expected):
    assert fix(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        # Colloquial shorthand: หมื่นห้า is 15,000, not 10,005; ล้านหก is
        # 1,600,000, not 1,000,006.  And ร้อยห้า is heard as both 105 and 150.
        "อยากจะจ่ายสักหมื่น 5 ก่อน",
        "มีการกันเงินไว้ล้าน 6 นะครับ",
        "ขาดแคลนพัน 2 ทุน",
        "เหลืออีกร้อย 5 บาท",
        # Already has a coefficient of its own, so the head is not an implicit one.
        "มีสองพัน 200 คน",
    ],
)
def test_ambiguous_magnitudes_are_queued_for_review_not_guessed(text):
    fixed, changes = repair_text(text)
    assert fixed == text
    assert [c.confidence for c in changes if c.rule == "numword"] == ["unresolved"]


def test_a_magnitude_after_the_digits_means_two_separate_quantities():
    """"ล้าน93 ล้าน" is 93 million alongside another figure, not 1,000,093."""
    fixed, changes = repair_text("ได้รับ 7,000,080 ล้าน93 ล้านแล้ว")
    assert fixed == "ได้รับ 7,000,080 ล้าน 93 ล้านแล้ว"  # spacing only
    assert [c for c in changes if c.rule == "numword"] == []


@pytest.mark.parametrize("text", ["ร้อยละ 20 ของงบ", "งบ 81 ล้านนะครับ"])
def test_ordinary_magnitude_usage_is_untouched(text):
    assert fix(text) == text


# --- spacing genuine numbers off from the Thai text -------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("ลงคะแนนเสียงได้18ท่าน", "ลงคะแนนเสียงได้ 18 ท่าน"),
        ("ถนนเนี่ย 7เมตร", "ถนนเนี่ย 7 เมตร"),
        ("จาก49เหลือ42", "จาก 49 เหลือ 42"),
        ("45บาท", "45 บาท"),
        ("ก่อนหน้าปี2560", "ก่อนหน้าปี 2560"),
        ("สไลด์ที่2", "สไลด์ที่ 2"),
        ("ผมมาเป็นลูกบอดโดนไป4ทุ่ม", "ผมมาเป็นลูกบอดโดนไป 4 ทุ่ม"),
    ],
)
def test_spaces_numbers_off_from_thai(text, expected):
    assert fix(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "00:04:45",  # timestamps
        "5.2 ต่อ 00:00:01",  # decimals
        "นั่นเป็น 1/12 อันดับแรก",  # ratios
        "18%10 ก็ไม่เป็น 10",  # percentages
        "4-69 part2.m4a",  # Latin filenames
        "ห้อง 4 ชั้น 6",  # already spaced
    ],
)
def test_spacing_only_triggers_on_a_thai_neighbour(text):
    assert fix(text) == text


def test_spacing_runs_after_repair_so_it_cannot_split_a_broken_word():
    assert fix("ผมเ4ยงมากในอุตสา6รรมนี้") == "ผมเสี่ยงมากในอุตสาหกรรมนี้"


def test_spacing_is_reported_under_its_own_rule():
    _, changes = repair_text("ได้18ท่าน")
    spacing = [(c.before, c.after) for c in changes if c.rule == "spacing"]
    assert spacing == [("ได้18ท่าน", "ได้ 18 ท่าน")]


def test_a_glued_digit_is_still_flagged_for_review_after_spacing():
    """Spacing is cosmetic — it does not settle whether the digit was damage."""
    _, changes = repair_text("ได้18ท่าน")
    assert [c.confidence for c in changes if c.rule == "digit"] == ["unresolved"]


def test_spaced_numbers_are_not_added_to_the_review_queue():
    """Reporting every "ปี 67 ถึง 69" would bury the real cases."""
    _, changes = repair_text("ในช่วงปี 67 ถึง 69 นะคะ")
    assert changes == []


def test_no_line_is_edited_without_a_report_row():
    """A transcript tool must never rewrite anything silently."""
    text = (
        "ผมเ4ยงมากในอุตสา6รรมนี้\n"
        "เราไม่3 า รถจะไป\n"
        "ฉบับ สมบูร ณ ์ แล้ว\n"
        "ราคา 45 บาท ห้อง 4 ชั้น 6\n"
        "เเปลกกำำ\n"
    )
    fixed, changes = repair_text(text)
    edited = {
        i
        for i, (a, b) in enumerate(zip(text.splitlines(), fixed.splitlines()), 1)
        if a != b
    }
    assert edited == {c.line for c in changes if c.applied}


# --- layout must survive ----------------------------------------------------


def test_preserves_line_structure_and_spacing():
    text = "  ผมเ4ยงมาก  \n\nบรรทัดสาม\n"
    assert fix(text) == "  ผมเสี่ยงมาก  \n\nบรรทัดสาม\n"


def test_normalizes_tone_marks_and_duplicate_vowels():
    assert fix("เเปลกกำำ") == "แปลกกำ"


def test_report_is_ordered_by_position():
    _, changes = repair_text(
        "อุตสา6รรมเ4ยง\nเ4ยง", overrides={"เ4ยง": "เสี่ยง"}
    )
    assert [(c.line, c.col) for c in changes] == sorted(
        (c.line, c.col) for c in changes
    )


# --- tiers ------------------------------------------------------------------


def test_override_beats_the_lexicon():
    overrides = {"เ4ยง": "เสียง"}
    fixed, changes = repair_text("ผมเ4ยงดัง", overrides=overrides)
    assert fixed == "ผมเสียงดัง"
    assert changes[0].confidence == "override"


def test_load_overrides_maps_wrong_to_correct():
    assert load_overrides(WORDS_CSV)["เ4ยง"] == "เสี่ยง"


# --- digit readings ---------------------------------------------------------


def test_single_digit_readings_are_ordered_by_likelihood():
    assert list(readings("1")) == ["หนึ่ง", "เอ็ด"]


def test_multi_digit_offers_numeral_and_per_digit_readings():
    got = list(readings("45"))
    assert got[0] == "สี่สิบห้า"
    assert "สี่ห้า" in got
