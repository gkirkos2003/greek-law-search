# Corpus inventory: quality report (2026-10-21 ~03:30)

Machine-readable: `search/work/inventory.json`. Partial dumps: `work/inv_fek_nsk.json`,
`work/inv_caselaw.json`, `work/inv_seriesb.json`, `work/inv_ocrerr_fek.json`.
Scripts: `work/inv_*.py`. All of them only read the corpus.

## Three blockers for indexing (fix these first)

1. **2,926 ΦΕΚ Α΄ text files are OCR failures.** They contain the marker `[[OCR ERROR MemoryError]]`
   instead of text (29,330 pages). 2,895 docs failed on every page, so their `ocr_done` conf is `null`.
   31 docs failed on some pages only (184 pages). They were written on 08-29/30 by the 6-worker run,
   plus 10 files on 10-16/17. `ocr_corpus.py` writes the marker and still logs the doc as ok.
   Resume skips a doc once its `.txt` exists, so **greek-ocr will never redo these.**
   The list is in `search/work/fek_ocr_redo.txt`. Most are from 1910s–1940s (2,547).
   Indexers must skip any page with this marker. The fix: re-OCR these docs later (not done yet).
   NSK text has **0** of these markers.
2. **ΦΕΚ Α΄ 2000–2007 pdftotext output is mojibake.** The Greek comes out as cp1253 bytes read as
   Latin-1 (`ÅÖÇÌÅÑÉÓ` = ΕΦΗΜΕΡΙΣ). This hits 1,528 docs: all of 2000–2005, plus 72 mixed docs in
   2006 and 4 in 2007. All 1,528 are repaired by
   `re.sub(r'[\u00a0-\u00ff]+', lambda m: m.group(0).encode('latin-1').decode('cp1253','replace'), t)`
   (checked on 20050100001). **src_fek must apply this fix** to any text where Latin-1 letters
   outnumber Greek ones. ΦΕΚ Β΄ 2000–2005 has the same problem (29 of 30 samples). The old `rag/` index was built
   from these files, and that is where its 93% mojibake comes from.
3. **ΣτΕ is almost empty.** 4,797 of 4,966 rows have empty text (`has_pdf_only`). Only about 169
   real texts exist (1.8M chars). They come from 1929–1936, plus 51 from 2015.

## ΦΕΚ Α΄ (pdf/ 41,093 · text/ 41,036 · 46.6 GB · 1833–2026)

- **Text sources:** 35,387 docs from OCR (in `ocr_done`). 5,649 docs from the pdftotext layer:
  all of 2000+, plus 44 earlier docs.
- **Chars:** OCR 1,032M + pdftotext 482M = **1.51G chars**.
- **OCR pages:** 275,259.
- **ocr_done rows:** 39,538 rows, 35,387 unique, 4,151 duplicate rows. The last row wins.
- **OCR confidence (32,492 docs, null excluded):** mean 0.912, median 0.921, p10 0.860, p90 0.953.
  0.5% of docs are below 0.7 and 2.7% are below 0.8.
- **57 PDFs have no text:**
  - 55 from 2000 and 1 from 2002 (`20020100056`). These are scans (Acrobat Scan Plug-in / Fujitsu)
    with no text layer. They fell outside both passes: OCR only covers years before 2000, and
    pdftotext gave fewer than 200 chars.
  - `1981/19810100207.pdf` (315 MB, 648 pages). This is the phase-1 `fail=1`.
- **Tiny texts:** 2,739 are under 500 chars, almost all of them the MemoryError files.
- **Other checks:** no zero-byte PDFs, no orphan `.txt` files. Year 1832 is missing (README says 1832–2026).
- **Known issue:** `-layout` mixes the two columns together in the pdftotext years.

| decade | pdf | text | ocr | pdftotext | Mchars | conf mean | median | p10 | <0.7 | <0.8 |
|---|---|---|---|---|---|---|---|---|---|---|
| 1830s | 271 | 271 | 242 | 29 | 6.0 | .898 | .906 | .850 | 0.0% | 2.1% |
| 1840s | 334 | 334 | 334 | 0 | 5.9 | .914 | .921 | .879 | 0.0% | 0.6% |
| 1850s | 515 | 515 | 515 | 0 | 10.0 | .895 | .907 | .839 | 0.2% | 3.5% |
| 1860s | 577 | 577 | 577 | 0 | 12.8 | .900 | .916 | .838 | 0.6% | 5.5% |
| 1870s | 631 | 631 | 631 | 0 | 14.3 | .896 | .910 | .832 | 1.1% | 4.8% |
| 1880s | 1978 | 1978 | 1978 | 0 | 32.3 | .924 | .935 | .892 | 0.3% | 2.1% |
| 1890s | 2017 | 2017 | 2017 | 0 | 25.8 | .922 | .932 | .886 | 0.4% | 1.4% |
| 1900s | 1926 | 1926 | 1926 | 0 | 29.1 | .872 | .885 | .812 | 1.6% | 8.1% |
| 1910s | 2915 | 2915 | 2915 | 0 | 56.9 | .880 | .892 | .827 | 1.0% | 5.2% |
| 1920s | 3381 | 3381 | 3380 | 1 | 85.5 | .874 | .890 | .804 | 1.8% | 9.3% |
| 1930s | 4796 | 4796 | 4796 | 0 | 104.9 | .911 | .920 | .869 | 0.3% | 1.7% |
| 1940s | 3327 | 3327 | 3327 | 0 | 58.0 | .901 | .906 | .867 | 0.1% | 1.2% |
| 1950s | 3031 | 3031 | 3030 | 1 | 106.6 | .923 | .930 | .895 | 0.1% | 1.4% |
| 1960s | 2365 | 2365 | 2365 | 0 | 87.2 | .923 | .929 | .889 | 0.1% | 0.6% |
| 1970s | 2876 | 2876 | 2876 | 0 | 124.9 | .930 | .935 | .903 | 0.1% | 0.5% |
| 1980s | 2254 | 2253 | 2249 | 4 | 127.9 | .940 | .946 | .909 | 0.4% | 0.8% |
| 1990s | 2238 | 2238 | 2229 | 9 | 145.2 | .959 | .967 | .935 | 0.1% | 0.7% |
| 2000s | 2454 | 2398 | 0 | 2398 | 129.2 | n/a | | | | |
| 2010s | 1946 | 1946 | 0 | 1946 | 176.2 | n/a | | | | |
| 2020s | 1261 | 1261 | 0 | 1261 | 174.8 | n/a | | | | |

## ΝΣΚ (pdf 53,927 in year dirs + 1 stray · 93 GB · OCR running)

- **Progress at 03:06:** 25,968 `.txt` done (1951 to ~1975). 27,959 left (1975–2026).
- **Rate:** 614 docs/h over the last hour and 486 docs/h over the last 3 h. The journal shows
  ~2,150 pages/h, and pages per doc are rising (1950s 2.9, 1960s 3.5, 1970s 4.1).
  **ETA is ~45–58 h, so around 10-23 morning to midday.** That is later than the 10-22 in memory.
- **OCR confidence:** mean 0.789, median 0.796, p10 0.681. **14.6% are below 0.7 and 51.5% below 0.8.**
  That is much worse than ΦΕΚ, because these are typewritten scans. 0 error markers, 17 "empty" docs (journal).
- **Metadata:** 56,334 records, all unique ids. 2,407 have no PDF (1950s 817, 1980s 970, 1970s 287,
  1960s 186, 1990s 146). Every PDF has metadata.
- **Stray file:** `pdf/test_6651576.pdf` sits in the pdf root. It explains the 53,928 vs 53,927 jobs.
- NSK PDFs are scans up to about 2021. From 2022, some are Word-generated with a text layer
  (4 of 6 samples).

| decade | metadata | pdf | text now | ocr pages | conf mean | median | p10 | <0.7 | <0.8 |
|---|---|---|---|---|---|---|---|---|---|
| 1950s | 11169 | 10352 | 10352 | 29682 | .817 | .830 | .708 | 8.8% | 35.9% |
| 1960s | 11741 | 11555 | 11554 | 40198 | .769 | .771 | .673 | 19.0% | 63.5% |
| 1970s | 7424 | 7137 | 4062 | 16487 | .776 | .785 | .669 | 16.7% | 57.2% |
| 1980s | 7566 | 6596 | 0 | | | | | | |
| 1990s | 7532 | 7386 | 0 | | | | | | |
| 2000s | 6187 | 6186 | 0 | | | | | | |
| 2010s | 3995 | 3995 | 0 | | | | | | |
| 2020s | 720 | 720 | 0 | | | | | | |

## Case law (jsonl)

| source | rows | unique ids | Mchars | empty / <500 | identical texts | years |
|---|---|---|---|---|---|---|
| Άρειος Πάγος | 32,872 | 32,872 | 828.7 | 0 / 0 | 0 | 1990s 21 · 2000s 3,398 · 2010s 17,708 · 2020s 11,745 |
| Ελ.Συν. (tnp) | 39,440 | 39,440 | 512.1 | 0 / 3 | 220 groups (+382 docs) | 2000s 17,126 · 2010s 20,814 · 2020s 1,500 |
| ΣτΕ (6 files) | 4,966 | 4,966 | 1.8 | **4,797** / 2 | (the empties) | 1929–1936 + 51 from 2015 |
| eisap | 1,637 | 1,637 | 0.7 | 1 / **1,435** | 9 groups (+146) | 1969–2026 |

- **Άρειος Πάγος:** every text starts with ~1.2k chars of site interface (Google-translate and AI-summary
  notice, ending in "Συνέχεια"). Strip everything up to `Απόφαση N / YYYY`.
- **Ελ.Συν.:**
  - docType: Πράξη 19,474 · Απόφαση 17,528 · Πρακτικό 1,939 · Απόφαση σε Συμβούλιο 497.
  - The identical texts are mostly runs of identical decisions under different numbers
    (e.g. 2003 0698–0700). Keep them. Only 42 groups share the same number and year.
  - The 2020s have only 1,500 rows.
- **eisap:**
  - kind: γνωμοδοτήσεις 772 · εγκύκλιοι 454 · other 411.
  - 411 rows have no year, so the year comes from `date`.
  - Most posts are short because they mostly link to PDFs.
- **Court-of-audit PDFs:** 529 files, 141 MB, 9,660 pages, 16.7M chars (whitespace removed).
  - 527 have a text layer. 2 do not: `1882-2022 Oλομ.pdf` and `ΠΡΑΚΤΙΚΑ 14Α 20 6 2022.pdf`.
  - 38 md5-duplicate groups (491 unique). The `*_EN.pdf` files are byte-identical to the Greek ones.
  - No unreadable or zero-byte files.
- **eisap PDFs:** 436 files, 184 MB, 1,857 pages, 392 unique md5. **381 have no text layer**, so they need OCR.

## ΦΕΚ Β΄ (seriesB: 157,964 PDFs · 106.0 GB · 1930–2026, no year gaps)

The text-layer split is clean: **every year 1930–1999 is scanned (0 of 350 samples have text) and every
year 2000–2026 is digital (135 of 135)**. No zero-byte files, no bad names, no unreadable samples.
Issue-number gaps: 1985 (2 missing) and 2009 (1 missing). Stray file: `seriesB_maxnum.json`.

| decade | files | GB | text layer (sample) | est. pages |
|---|---|---|---|---|
| 1930s | 2,118 | 0.9 | 0/50 | 11k |
| 1940s | 2,197 | 1.3 | 0/50 | 16k |
| 1950s | 2,999 | 2.5 | 0/50 | 22k |
| 1960s | 6,858 | 5.7 | 0/50 | 49k |
| 1970s | 12,875 | 11.7 | 0/50 | 110k |
| 1980s | 9,386 | 8.5 | 0/50 | 85k |
| 1990s | 11,914 | 9.4 | 0/50 | 128k |
| 2000s | 20,957 | 8.4 | 50/50 (2000–05 mojibake) | 288k |
| 2010s | 40,258 | 23.6 | 50/50 | 446k |
| 2020s | 48,402 | 34.0 | 35/35 | 385k |

**Text-layer part (2000–2026):** 109,617 files, 65.9 GB, **~1.1–1.2M pages**.
- The page estimate comes from two methods that agree: 5 samples per year (1.12M) and a random
  100-file sample at 11.1 pages/file (1.22M). The bytes-based figure of 2.58M is inflated by
  image-heavy files.
- pdftotext speed: 0.012 s/page with `-layout`, 0.031 s/page in raw mode.
- Raw output: 3,664 chars/page and 6.4 KB/page.
- **Full extraction ≈ 4–10 h on 1 thread. Output ≈ 4.4G chars ≈ 7.8 GB of UTF-8.** That is about 3×
  all of ΦΕΚ Α΄.
- 2000–2005 needs the cp1253 fix.

**Scan part (1930–1999):** 48,347 files, ~420k pages. At 2,150–3,600 pages/h that is **~5–8 days of OCR**.

**Verdict:** Series Β΄ 2000+ is cheap to extract (one night on 1 thread), but it is big. It would add
~1.2M pages, about 3× the current index text. Recommendation: leave it out of the first index. Add it as
its own source (`fek_b`) in a second pass, once the ΦΕΚ Α΄ and case-law index has been tested,
extracting to `search/work` as a few big jsonl files rather than 110k small files on NTFS.
Pre-2000 Series Β΄ needs OCR, so wait until after NSK.

## Old `rag/` index

225 MB: `chunks.jsonl` (54,208 chunks, 2000-era ΦΕΚ only) and `vectors.f16.npy`.
- The vector file has 51,200 × 768 rows, which does not match the 54,208 chunks.
- The chunks are 93% mojibake.
- It was built with nomic via LM Studio. **Do not use it.**

## Text available to index now

| source | chars |
|---|---|
| ΦΕΚ Α΄ | 1.51G (minus 2,926 broken docs) |
| ΑΠ | 0.83G |
| Ελ.Συν. | 0.51G |
| ΝΣΚ | ~0.2 GB of text so far, about 48% of docs |
| CoA PDFs | 17M |
| ΣτΕ | 1.8M |
| eisap | 0.7M |
| **Total** | **≈ 3.0G chars** |
