# ΦΕΚ Τεύχος Α΄ source (`lib/src_fek.py`) — notes

Agent "fek", 2026-10-21. Unit API as in DESIGN.md: `iter_units()` yields
`Unit(doc_id="fekA:<stem>", source="fek_a", fingerprint, load)`. `load()` returns `(doc, chunks)`.

## Files
| file | what |
|---|---|
| `lib/src_fek.py` | the source module (cleaning, segmentation, chunking, metadata) |
| `lib/fek_deint.py` | de-interleaver for two-column PaddleOCR pages (pre-2000) |
| `tools/extract_fekA.py` | column-correct re-extraction of the 2000+ PDFs → `work/fekA_text/` |
| `tools/build_deint_tables.py` | builds `work/fek_deint_tables.npz` (hashed word/bigram counts from the OCR corpus) |
| `tools/deinterleave_eval.py` | synthetic evaluation of the de-interleaver (born-digital pages re-interleaved like PaddleOCR) |
| `tools/deint_real_check.py`, `tools/deint_real_check2.py` | checks on real OCR pages (held-out signals) |
| `tests/test_src_fek.py` | self-test on 200 random issues, 10 per decade |
| `work/fekA_text/<year>/<stem>.txt` + `manifest.jsonl` | 5,605 re-extracted 2000+ texts (613 MB) |

## 2000+ issues: column-correct text
`pdftotext -layout` (what `../text/` holds for 2000+) puts the two page columns side by side on
one line. Plain `pdftotext` in reading-order mode, with no `-layout`, detects the columns, outputs the left column and then the right one,
and **de-hyphenates** line ends. Measured on 40 random issues: the only words that differ from `-layout` are the
joined hyphen halves. Speed: the whole 2000–2026 set (5,661 PDFs, 13.4 GB) took **2,899 s**
with one process at nice 19 / ionice idle. The worst file was Α 94/2016 (843 MB, 3,768 pages) at 72 s and
~31 MB RSS. Result: 5,605 ok; **56 "empty" = scanned PDFs with no text layer**
(55 from 2000, 1 from 2002). These issues have no `../text` file either, so they have **no unit** until
someone OCRs them.

Rerun / update (resumable; it skips a stem when its manifest row matches the PDF's size+mtime). Run it from `search/`:
```
OMP_NUM_THREADS=1 nice -n 19 ionice -c3 venv/bin/python tools/extract_fekA.py   [--years 2025-2026] [--limit N]
```
Fallbacks: if the whole file times out (300 s), it extracts 100-page ranges. If that fails, the status is "fail" and
src_fek uses the old `-layout` text with a gutter-splitting heuristic (`_layout_to_columns`).
Note: Claude Code kills `&`/nohup children when a tool call returns, so run it with the
Bash tool's `run_in_background` (or a terminal).

**2000–2005 legacy font encoding**: many of these PDFs map Greek glyphs through WinAnsi, so
pdftotext gives `¸÷ïíôáò õðüøç`. The fix is per token: cp1252 bytes → cp1253 (`Έχοντας υπόψη`). It is applied only
when ≥10 tokens and ≥2 % of the document look like this. The old `../text/2000-2005` files have the same
garbling, so this alone makes those years searchable.

## Cleaning (all years)
- Glyph fixes: `∆`(U+2206)→Δ, `µ`→μ, `Ω`(U+2126)→Ω, U+2212/U+2010→`-`, soft hyphen removed, NFC. Accents kept.
- Latin look-alikes inside Greek words → Greek (`NOMOΣ`, `TΗΣ`). OCR text only: all-Latin
  look-alike tokens on lines that are mostly Greek (`IANOYAPIOY`). German lines (1830s) are untouched.
- Omicron used for zero inside numbers: `4ο12/2ο16` → `4012/2016` (only between digits, or next to `/` and `.`).
  A trailing ordinal `7ο` is left alone. OCR also gets: `1671)1951` → `1671/1951` and `το5 ` → `του `.
- Dropped: running headers and mastheads (`ΕΦΗΜΕΡΙΣ/ΕΦΗΜΕΡΙΔΑ ΤΗΣ ΚΥΒΕΡΝΗΣΕΩΣ`, only when the line is all caps),
  `ΤΕΥΧΟΣ ΠΡΩΤΟ(Ν)`, `Τεύχος Α’ 188/08.10.2016`, `Αρ. Φύλλου`, page numbers, lines that are only digits
  (so numeric table cells go too), barcodes `*0100…*`, the digital-signature block, and the ΕΘΝΙΚΟ ΤΥΠΟΓΡΑΦΕΙΟ
  trailer on the last 2 pages. At the end of each act, the closing `Αθήνα, <date> … signatures` tail is dropped if it is under 1,500 chars.
  `[[OCR ERROR …]]` lines are dropped and counted in `meta.ocr_error_pages`.
- Line joining: hyphenated line ends are joined when the next line starts lowercase (or both halves are caps).
  Paragraph breaks come at numbering (`1.`, `α)`, `(2)`), headings, and sentence end + capital.

## Segmentation and chunking
1. **Acts** in an issue are found from headers on all-caps lines: `ΝΟΜΟΣ ΥΠ' ΑΡΙΘ(Μ). N`, `Νόμος N` (1920s),
   `ΠΡΟΕΔΡΙΚΟ(Ν) ΔΙΑΤΑΓΜΑ`, `ΝΟΜΟΘΕΤΙΚΟ(Ν) (ΠΡΟΕΔΡΙΚΟΝ) ΔΙΑΤΑΓΜΑ`, `ΑΝΑΓΚΑΣΤΙΚΟΣ ΝΟΜΟΣ`, `ΒΑΣΙΛΙΚΟ(Ν) ΔΙΑΤΑΓΜΑ`,
   `ΠΡΑΞΗ ΝΟΜΟΘΕΤΙΚΟΥ ΠΕΡΙΕΧΟΜΕΝΟΥ`, and a bare `ΔΙΑΤΑΓΜΑ`. Greek-numeral law numbers are parsed (`ΡΜΗ΄` = 148,
   which is how 1850s laws are numbered; the index gives Arabic numbers). Headers are matched to `index.jsonl` rows by number,
   or by kind plus title-word overlap. When there are fewer than 2 headers but the index lists ≥2 acts, two fallbacks are tried:
   (a) the index titles are located in the text (3-line window, dot-leader TOC lines skipped, last good occurrence wins);
   (b) the issue's own `(1)`, `(2)`, … act markers. An issue with a single act gets everything assigned to that act.
2. **Articles**: `Άρθρο 5`, `Αρθρον 5`, `ΑΡΘΡΟ 12`, `Άρθρο 5Α`/`5 Α`, `Άρθρο 12: Τίτλος`, `Άρθρ. 1.`, Roman numerals
   `Άρθρ. IV.` (1830s–80s), ordinals `Άρθρο πρώτο` … `τριακοστό πέμπτο` → "1" … "35" (the locator keeps the word),
   and `Άρθρον μόνον` → "μόνο". Rejected: `Άρθρο 5 του ν. …` (a sentence), and lines starting with `«`
   (quoted amendments). A sequence filter accepts n = 1 (restart), cur < n ≤ cur+3, or a suffix variant.
3. **TOC** (`ΠΙΝΑΚΑΣ ΠΕΡΙΕΧΟΜΕΝΩΝ`): ≥3 consecutive article entries whose body, ignoring all-caps chapter lines, is
   under 150 chars, or a numbering run that restarts at 1 with a median body under 250 chars. It becomes **one** chunk
   cut at about 1,500 chars, `… — περιεχόμενα`.
4. **Chunks**: the preamble (`… — προοίμιο`), one chunk per article, and paragraph packing for long articles
   (target 1,500, hard max 2,500, tail < 400 merged; sentence splitting, then word splitting, for giant paragraphs).
   Locators: `ν. 4427/2016 — άρθρο 5`, `… — άρθρο 1 §6–10`. The § numbers count only top-level paragraphs
   numbered k = previous + 1, so restarting lists such as the Kallikratis municipality lists don't produce "§4–3".
   Otherwise the locator is `(μέρος i/n)`. Text before the first act is `αρχή φύλλου` (dropped if under 200 chars). Issues with no usable
   text (OCR failed) get one chunk `τίτλοι πράξεων (χωρίς κείμενο)` with the index titles.
5. **Law labels**: `ν. 4427/2016`, `π.δ. 15/1975`, `β.δ.`, `ν.δ.`, `α.ν.`, `ΠΝΠ`, `ν.π.δ.`. For acts without a number
   (most royal decrees before 1960; there is no protocol number in the index), the label is
   `β.δ. ΦΕΚ Α΄ 10/1885 (2)` = position in the issue's index rows. `meta.laws[]` holds `{label, key, kind, number, year,
   title}`, and `key` follows xref `law_key` style (`ν:4427/2016`), or None when unnumbered. The law year is taken as the issue year.

doc: `citation` "ΦΕΚ Α΄ 188/08.10.2016", `title` = act label + index title (several acts are joined by "; "), `date` = issue
date from the index, `path` = the PDF, `meta.text_path` = the text actually parsed, `ocr_conf` from `../ocr_done.jsonl` (last row).

## De-interleaving pre-2000 OCR (`lib/fek_deint.py`) — ON by default (`FEK_DEINT=0` turns it off)
PaddleOCR sorts boxes top to bottom, so two-column pages come out L,R,L,R… No box coordinates were saved.
**Evidence that the interleaving is real**: for real OCR lines that end in a hyphen, the continuation is found at line i+2 in 16,088 cases
and at i+1 in 7,165. Method: beam search (width 8) assigns each line to column L or R while keeping the order inside each column.
The score per (previous line of the column, line) uses: hyphen half + next word is a corpus word (+4); a frequent within-line
bigram across the break; punctuation/case agreement; and −1.0 for two lines in a row in one column, which stops identity swaps.
Tables: hashed counts from 11,862 OCR texts (1/3 of the corpus), 12.7 MB npz. A page is re-ordered only if
switch-rate ≥ 0.45, gain/line ≥ 0.5 and the result beats the single-column reading by more than 3.0.

| test | off | on |
|---|---|---|
| synthetic: 80 two-column pages (2006–25 born-digital, re-ordered like Paddle at 200 DPI) — successor accuracy | 0.059 | **0.875** |
| synthetic: 80 single-column controls — successor accuracy | 1.000 | **1.000** (fired 0/80) |
| real OCR, 200 issues 1880–1999 — hyphen joins forming a word in the *modern* 2000+ corpus (tables it never uses) | 13.2 % | **41.2 %** |
| same — cross-line bigram frequent in the modern corpus | 6.7 % | **11.8 %** |
| same — article headings in order (k → k+1) | **36.7 %** | 31.3 % |

It fires on about 42 % of real OCR pages. Trade-off: body text becomes much more continuous, but short heading lines
(`Άρθρον 5.`, `Ο ΠΡΟΕΔΡΟΣ`) carry no continuity cue and sometimes land in the wrong column. Article order therefore suffers a little,
and article locators in OCR issues are approximate either way. Cost: about 15–30 ms per OCR issue.
The OCR-unit fingerprints carry `d1`/`d0`, so toggling `FEK_DEINT` triggers a reindex of those units.
(An earlier version without the double-line penalty scored 0.71 on synthetic pages and was worse on real headings, 27 %.)

## Measured (tests/test_src_fek.py, 200 issues, 10 per decade)
See the final block in `logs/test_src_fek.log`. Summary at parser v9:
- iter_units: 41,036 units (35,431 OCR + 5,605 pdf reading-order) in 1–10 s; the time depends on cold or warm NTFS listing.
  There is no per-file stat(): fingerprints come from the `ocr_done.jsonl` / `manifest.jsonl` rows, because stat() costs ~10 ms each on this mount.
- chunks/issue ≈ 25; chars/chunk p5 ≈ 220, p50 ≈ 1,210, p95 ≈ 1,500, p99 ≈ 1,800; 0 chunks over 2,500; about 10 % under 400.
- % chunks with an article: 73 % overall (pre-1900 50 %, 1900–49 64 %, 1950–99 80 %, 2000+ 93 %).
- % chunks with a law: 95 % (pre-1900 93 %, 1900–49 90 %, 1950–99 97 %, 2000+ 99.5 %).
- load: median about 0.1 s per issue, mean about 0.17 s. Α 94/2016 (3,768 pages) takes 6.6 s and 210 MB RSS. Metadata (index.jsonl) takes 2–5 s once per process.

## Known gaps
- **2,895 OCR issues (conf = null in ocr_done.jsonl) are entirely `[[OCR ERROR MemoryError]]`.** They are mostly
  1910–1950: 1910s 215, 1920s 764, 1930s 870, 1940s 673. More issues have *some* error pages (`meta.ocr_error_pages`).
  These need a re-OCR by the OCR owner (we don't touch `../text`). Until then they index only as the titles chunk.
- 56 scanned 2000/2002 PDFs have no text at all, so there is no unit for them.
- Pre-1950 law attribution in issues with several acts relies on title anchoring or `(n)` markers on noisy,
  interleaved OCR, so it is approximate. "% with law" counts assignments, not verified correctness. Unnumbered acts
  get positional labels, not their decree dates.
- OCR article detection misses headings that the OCR garbled (`Αρθρυν`, `Άθρ.`), and some quoted amendment articles
  slip through the sequence filter. In OCR issues, an act's segment can contain lines of a neighbouring act on the same page.
- Tables: numeric-only lines are dropped, and other table text is flattened.
- Bilingual 1833–49 issues: Greek and German stay mixed (`meta.lang = "el+de"`). There is no language split.
