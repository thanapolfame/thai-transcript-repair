# thai-asr-transcript

Repairs Thai transcripts in which number-word syllables were rewritten as
Arabic digits.

## The bug this undoes

An inverse-text-normalization step (the thing that turns `สี่สิบห้าบาท` into
`45 บาท`) replaced Thai number words with digits without checking word
boundaries, so number words buried inside ordinary words were eaten too:

| correct | corrupted | swallowed |
| --- | --- | --- |
| เ**สี่**ยง | เ**4**ยง | สี่ = 4 |
| อุตสา**หก**รรม | อุตสา**6**รรม | หก = 6 |
| **สาม**ารถ | **3**ารถ, `ความ 3ารถ`, `ไม่ 3 ารถ` | สาม = 3 |
| **สาม**ัคคี | **3**ัคคี | สาม = 3 |

The replacement is sometimes padded with spaces, and spaces also end up
scattered through the wreckage itself, so one corruption shows up as `ความเ4ยง`,
`ความเ 4 ยง`, `ความ 3ารถ` or `ไม่3 า รถ`. Those spaces are part of the damage
and are closed up along with the digits — but a **double** space is left alone,
since that is real phrase separation rather than a broken word.

## Usage

Python 3.13 or newer.

```bash
python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python repair.py transcript.txt -o fixed.txt --report report.csv
```

With no `-o` the repaired text goes to stdout, so it pipes. Exit status is `1`
when anything was left unresolved, so a batch job can gate on the review queue
without losing the output.

| flag | effect |
| --- | --- |
| `--words FILE` | curated `correct,wrong` pairs (default `resource/word.csv`) |
| `--aggressive` | also repair digits touching Thai on **one** side; will fire on legitimate unspaced text like `45บาท`, so review every hit |
| `--no-normalize` | skip tone-mark / duplicate-vowel normalization of the input |
| `--no-join-words` | leave words the ASR split across spaces (`ประสบการ ณ ์`) |
| `--no-collapse-spaces` | keep the token spacing on lines spaced between every word |
| `--no-space-numbers` | leave genuine numbers flush against Thai text (`ได้18ท่าน`) |

## How it works

Three tiers, in order of trust. Every tier logs to the report; nothing is
rewritten silently.

1. **`resource/word.csv` overrides** — exact `wrong -> correct` replacements.
   Confidence `override`.
2. **Digit expansion + lexicon validation** — for each digit run with Thai
   letters on both sides (spaces allowed between), every plausible Thai reading
   is substituted (`4` -> `สี่`; `45` -> `สี่สิบห้า` or `สี่ห้า`) and has to
   clear two independent bars:

   - it must land inside a real word from the 62k-entry PyThaiNLP lexicon, and
     one *longer than the reading itself* — otherwise it says nothing about the
     surrounding characters;
   - it must **reduce the out-of-vocabulary token count** in a 40-character
     window, i.e. actually repair a segmentation defect.

   Confidence `high` when exactly one reading clears both, `ambiguous` when
   several do (`1` is หนึ่ง *or* เอ็ด, `2` is สอง *or* ยี่) — best score wins
   and the case is flagged.
3. **Unresolved** — the digit is glued between Thai letters but no reading
   yields a word. The text is left exactly as-is and reported for review.

After the digit tier come the two space repairs. First, **words split across
spaces**: this ASR puts a space between nearly every token, and some of those
splits land mid-word — `ประสบการ ณ ์`, `บริษั ท`, `ขอข ยาย`. The tell is the
usual one: a split word leaves an orphan behind, a fragment that cannot be read
as words at all. That is stricter than "not a single word" — `พอเรา` and `ดูว่า`
are two words run together and nothing about them is broken. Repair grows
outward from each orphan and takes the best window by three tests in order: a
real dictionary word beats a merely readable string, then the largest collapse,
then the smallest reach.

A join must also **reduce** the token count. `กกต` glued to `อย่างนั้นเนี่ย`
re-segments as กก|ตอ|ย่าง|นั้น|เนี่ย — every piece is a lexicon entry, but it
shredded `อย่าง`, which was fine before. Without that rule the acronyms in a
Thai transcript (`กกต`, `อบต`, `กปม`, `สจล`) all get glued to their neighbours.

Second, **over-spaced lines** are closed up into ordinary Thai prose, which runs
words together and reserves spaces for phrase breaks. A line qualifies when its
Thai runs average under 7 characters — on a real transcript the two populations
separate cleanly, over-spaced lines topping out at a mean of 5.7 and ordinary
lines starting at 13.1. The test is per line, so lines that were already fine
keep their phrase breaks, and double spaces are never closed anywhere.

Before the digit tier there is one more repair: **stranded magnitude words**.
The ITN step sometimes converted the tail of a numeral but not its head, leaving
`มากกว่าพัน 200 ทุน` where `มากกว่า 1,200 ทุน` belongs, or `ร้อย46` for `146`.
The two are folded together when the digits occupy the magnitude immediately
below — the canonical spoken form, พันสองร้อย or ร้อยสี่สิบหก.

Everything else is refused and queued for review rather than guessed at:

| case | why it is refused |
| --- | --- |
| `หมื่น 5`, `ล้าน 6`, `พัน 2` | colloquial shorthand — หมื่นห้า is 15,000, not 10,005; ล้านหก is 1,600,000, not 1,000,006; and ร้อยห้า is heard as both 105 and 150 |
| `สองพัน 200` | the magnitude already has a coefficient, so its head is not an implicit one |
| `ล้าน93 ล้าน` | a magnitude *after* the digits makes them its coefficient — two separate quantities, not 1,000,093 (skipped silently, since nothing is wrong with it) |

Getting a figure wrong in a budget transcript is worse than leaving it as the
speaker said it, so no ambiguous reading is ever applied.

Then, last, **spacing**: every digit still standing is by now a genuine number,
so any that is flush against Thai text gets separated from it — `ได้18ท่าน` ->
`ได้ 18 ท่าน`, `จาก49เหลือ42` -> `จาก 49 เหลือ 42`. Only Thai-letter neighbours
count, so timestamps (`00:04:45`), decimals (`5.2`), ratios (`1/12`),
percentages (`18%10`) and Latin tokens (`part2.m4a`) are untouched. Order
matters: spacing runs *after* repair, or it would prise `เ4ยง` apart instead of
mending it. Disable with `--no-space-numbers`.

Spacing is cosmetic and does not settle whether a glued digit was damage, so a
site can carry both an `unresolved` row and a `spacing` row.

Normalization edits are reported too, under rule `normalize`. Nothing the tool
changes is absent from the report; the test suite asserts that directly.

### Why the segmentation check carries the weight

A lexicon hit alone is not enough. In a real 1,910-line transcript, 148 lines
contain spaced digits and nearly all are ordinary numbers; accepting any lexicon
hit turns `มูลค่าอีก 3 ล้าน` into `สามล`, `มีอยู่ 2 งบ` into `ยู่ยี่`, and
`ประเด็นที่ 2` into `ประเด็นที่สอง`.

The asymmetry that separates them: corruption leaves an **orphan fragment**
behind — `ารถ` and `เ` are not words — whereas a genuine number sits between two
perfectly well-formed words. So a repair is only accepted when it lowers the
count of unknown tokens nearby. On that transcript the rule found 13 corruptions
and refused 14 genuine numbers (`ได้18ท่าน`, `ไป4ทุ่ม`, `วันที่21มีนา`,
`ปี 67 ถึง 69`) with no misses in either direction.

Spaced digits that resolve to nothing are *not* added to the review queue —
there are hundreds of them and they are all real numbers. Only the tight form,
where a digit is glued directly between Thai letters, is worth a human's time.

Note that tier 2 does **not** tokenize first. PyThaiNLP shatters corrupted
words (`ผมเ4ยงมาก` -> `ผม | เ | 4 | ยง | มาก`), so a corrupted word never
arrives as a token. Instead the repairer searches outward from each digit over
every word boundary within 24 characters and asks the lexicon which span is a
word. A candidate whose only "word" is the substituted reading itself is
rejected — it proves nothing about the surrounding characters.

Layout is preserved byte-for-byte apart from the repairs: normalization is
scoped to word runs because `pythainlp.util.normalize` otherwise strips and
collapses whitespace, which would destroy line structure. Runs with no base
consonant are skipped entirely — ASR output strands diacritics between spaces
(`สมบูร ณ ์`), and normalizing a lone mark **deletes** it, since a diacritic
with nothing to attach to is not a valid syllable.

## Report

`--report` writes `line,col,before,after,confidence,rule`, one row per decision,
including the `unresolved` non-edits. Line numbers are exact; columns can drift
a few characters on lines with multiple repairs, since earlier replacements
change the offsets.

## Adding cases

Put newly reviewed pairs in `resource/word.csv`. The test suite is parametrized
over that file and asserts two things for every row: the corrupted form is
repaired *without* consulting the overrides (i.e. tier 2 covers it on its own),
and the correct form is left untouched. A row that only passes via the override
tier is a signal that tier 2 has a gap.

```bash
.venv/bin/python -m pytest -q
```

The code is annotated throughout and checked under `mypy --strict`, configured
in `pyproject.toml`. Both gates have to be green:

```bash
.venv/bin/python -m mypy
```

## Not done here

The root cause is upstream. If you own the ITN step, fix it there: segment with
`newmm` first and convert only number words that are standalone tokens. This
package is for transcripts that were already corrupted.
