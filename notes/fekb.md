# ΦΕΚ Τεύχος Β΄ source (`lib/src_fekb.py`, source key `fek_b`) — notes

Agent "fek", 2026-10-21. Scope: 2000–2026 (born-digital, 109,617 PDFs). 1930–1999 Β΄ issues are scans with
no OCR, so they are skipped.

## Extraction — `tools/extract_fekB.py`
- Plain `pdftotext` (reading order, so column-correct and de-hyphenated; same approach as Α΄, see notes/fek.md).
  The 2000–2005 WinAnsi→cp1253 mojibake is fixed **at extraction** with `src_fek.fix_legacy_encoding`.
  Each record carries `fixed_mojibake`.
- Output is a few big files, not 110k small ones:
  `work/fekB_text/<year>.jsonl.gz` holds **one gzip member per record** (still a valid multi-member .jsonl.gz, so
  `zcat` works). Record = `{stem, year, pages, text, fixed_mojibake}`. `work/fekB_text/manifest.jsonl` has one row per
  stem: `status` (ok / ranges / partial / empty / fail), `src_size`, `src_mtime_ns`, `pages`, `chars`, `shard`, `off`, `len`, `secs`.
- `load()` seeks to `off` and decompresses `len` bytes, i.e. one issue. It never touches the rest of the shard.
- Resumable: stems already in the manifest are skipped. On start, every shard is truncated back to the end of its last
  manifest-recorded member, so a half-written tail from a kill is dropped. The manifest row is written only after
  the member is flushed, so readers never see a half record.
- Time cap: 300 s for the whole file, then 100-page ranges of 120 s each, with an overall cap of 1,200 s per PDF.
- Order: 2026 → 2000 (newest first); inside a year, issues ascending.
- **Full run DONE 2026-10-21: 109,617 PDFs in 10,604 s (2 h 57 min), ~10.3 PDF/s, ~128 pages/s**, 0 failures;
  109,529 ok + 88 `empty` (scans without a text layer: 87 from 2000, 1 from 2001). 1,354,466 pages,
  4.37 G chars, 11,765 issues mojibake-fixed, 1.6 GB of shards. One process at nice 19 / ionice idle, CPU ~5–10 %.
- Run / resume from `search/` (use the Bash tool's run_in_background; nohup/& dies with the tool call):
  `OMP_NUM_THREADS=1 nice -n 19 ionice -c3 venv/bin/python -u tools/extract_fekB.py >> logs/extract_fekB.log 2>&1`
  Progress: `wc -l work/fekB_text/manifest.jsonl` or `tail logs/extract_fekB.log`. Options: `--years 2010-2012`, `--limit N`.

## Units
`iter_units()` reads only the manifest: no stat(), no shard reads. It yields just the stems already extracted with
a text status, so the index grows as the extraction progresses. Fingerprint =
`v<PARSER_VERSION>|src_size|src_mtime_ns|chars|shard:off`.

## Parsing (no index for Β΄, everything is derived from the text)
- Cleaning reuses `src_fek` (glyph fixes, look-alikes, boilerplate, de-hyphenation). Β΄ extras: `ΤΕΥΧΟΣ ΔΕΥΤΕΡΟ`,
  `Τεύχος Β’ 1101/15.02.2024` running headers, `ΠΕΡΙΕΧΟΜΕΝΑ` / `Π Ε ΡΙ Ε ΧΟ ΜΕ ΝΑ`, and 1-char dingbat separator lines (`Ö`, `F`).
- **Date** comes from the running header `Τεύχος Β΄ N/dd.mm.yyyy` (2006+), or else from the page-1 masthead
  `27 Ιανουαρίου 2016` (2000–2005 have no date in the running header). A date from a different year is rejected.
- **Acts**: multi-act issues number their acts with `(1)`, `(2)`, … on a line of their own. These are taken as an increasing
  run starting at 1, which skips stray TOC numbers. Each act's head is widened backwards by up to 5 lines (number line,
  title lines) because the marker can come before, inside or after the head in PDF reading order. The widening stops at all-caps signature names,
  section headings, body lines and `Αθήνα, …`. A single-act issue (no markers) starts at the first section heading
  (`ΑΠΟΦΑΣΕΙΣ`, `ΔΙΟΡΘΩΣΕΙΣ ΣΦΑΛΜΑΤΩΝ`, `ΕΓΚΥΚΛΙΟΙ`, …) or `Αριθμ.` line. The masthead/TOC before the first act is dropped.
- Per act: `number` comes from `Αριθμ./Αριθ./Αρ. πρωτ. …`, or for summary-style acts from `υπ’ αριθμ. X` / `υπό στοιχεία X`
  in the first lines. `title` = the lines up to the authority / `Έχοντας υπόψη` / `Με την …` (max 6 lines). `authority` = the following
  all-caps lines (`Ο ΥΠΟΥΡΓΟΣ …`, `ΤΟ ΔΙΟΙΚΗΤΙΚΟ ΣΥΜΒΟΥΛΙΟ …`). `kind` = the section heading in effect.
- **Chunks**: per act, then articles (src_fek's article detector: `Άρθρο 5`, ordinals …), then paragraph packing
  (target 1,500, hard max 2,500). `law` is None. Locator = `Αριθμ. 12345/2016 — άρθρο 2` or `Αριθμ. … — μέρος 2/5`,
  or `πράξη (3) — …` when the act has no number. Extra key **`head`** = `citation — Αριθμ. N — title` on every
  chunk, so the act number and title are searchable. An act with no body gets one `τίτλος` chunk.
- doc: citation `ΦΕΚ Β΄ 100/27.01.2016`. `title` = the first act's title, plus `(+N πράξεις)` when there are several (or `N πράξεις`).
  `meta.acts` = `[{number, title, authority, kind}]`, plus `meta.pages`, `meta.fixed_mojibake`, and `meta.text_path` = `<shard>#<off>`.
  `path` = the PDF.

## Measured — `tests/test_src_fekb.py 270` (10 issues per year, 2000–2026, after the full extraction)
- iter_units: 109,529 units in 1.9 s (manifest only).
- 23.8 chunks/issue; chars/chunk p5 291 / p50 1,283 / p95 1,601 / p99 1,924; 0 chunks over 2,500.
- acts/issue: median 3, p90 11, p99 16. Acts with a number 77.4 % (older summary-style acts often carry none),
  a title 98.4 %, an authority 71.4 %.
- issues with a date 100 %; chunks with an act number in the locator 90.6 %; chunks with an article 39.8 %.
- load: median 0.06 s, mean 0.075 s, max 0.29 s. A full pass over all units ≈ 2.3 h on one thread
  (before embedding).
- Estimated chunk count for the whole source: ~2.6 M chunks (23.8 × 109.5k).

## Known gaps
- `authority` is sometimes missing (signer-only acts) or takes in an all-caps part of the title (company names).
- Tables (personnel lists, budgets) are flattened text; numeric-only lines are dropped (src_fek rule).
- Acts inside one issue that are *not* marked with `(n)` (rare) are merged into one act.
- Pre-2000 Β΄ (scans) is not covered until it is OCR'd.
