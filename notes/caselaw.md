# Case-law sources — `lib/src_caselaw.py`

Agent: caselaw. Implements the DESIGN.md Unit API for the six case-law sources. Counts below are
from the full load pass of 2026-10-21 (see "Measured" at the end).

## Sources, doc ids, citations

| key | input | doc_id | citation example | docs |
|---|---|---|---|---|
| `areios_pagos` | `caselaw/areios-pagos/decisions.jsonl` (1.49 GB) | `ap:1230/1991` | `ΑΠ 1230/1991 (Γ΄ Πολ.)`, `ΟλΑΠ 5/2020` | 32,872 |
| `elsyn` | `caselaw/elsyn-tnp/decisions.jsonl` (0.93 GB) | `elsyn:192603` (TNP id) | `ΕλΣυν 250/2026 (Έβδομο Τμήμα)`, `ΕλΣυν Πράξη 358/2025 (Ε΄ Κλιμάκιο)`, `ΕλΣυν Πρακτ. …`, `ΕλΣυν (Συμβ.) 203/2026 (Έκτο Τμήμα)` | 39,440 |
| `nsk` | `caselaw/nsk/opinions.jsonl` + OCR `caselaw/nsk/text/<y>/<n>_<y>__<consultId>.txt` | `nsk:252/1979` | `ΓνωμΝΣΚ 252/1979` | 56,334 (text for the OCR'd part) |
| `ste` | `caselaw/ste/decisions_937*.jsonl` | `ste:Α4691/2015` (keeps the site's `Α` prefix, as in DESIGN) | `ΣτΕ 4691/2015 (Ε΄ Τμ.)`, `ΣτΕ Ολ 34/1929` | 167 |
| `eisap` | `caselaw/eisap/opinions.jsonl` + `eisap/pdf/` | `eisap:9097`, `eisap:pdf:<stem>` | `Γνωμ. ΕισΑΠ 4/2025`, `Εγκ. ΕισΑΠ 12/1994` | 1,229 posts + 19 PDFs |
| `coa` | `caselaw/court-of-audit/*.pdf` | `coa:<stem of canonical file>` | `ΕλΣυν 19/2025 (Ελάσσονα Ολομέλεια)`, `ΕλΣυν Ολ. Πρακτ. 14ης Γεν. Συν./2019` | 441 |

`doc.court`: Άρειος Πάγος / Ελεγκτικό Συνέδριο / Νομικό Συμβούλιο του Κράτους / Συμβούλιο της
Επικρατείας / Εισαγγελία Αρείου Πάγου. `doc.path` = `<jsonl>#<record id>` for jsonl records, the OCR
`.txt` for NSK opinions that have text, the PDF for coa/eisap PDFs.

## Speed / caches (all inside `search/work/`)

- `work/caselaw_idx/<name>.tsv` — one row per jsonl line: byte offset, length, crc32 of the raw
  line, a few keys. Keyed on the jsonl's size+mtime_ns (+ parser version) → rebuilt only when the
  jsonl changes. First build reads the 2.6 GB once (AP ≈ 65 s, it is I/O bound on this NTFS mount);
  afterwards `iter_units` for all six sources takes ≈ 25 s (coa 17 s of that = stat()ing 528 PDFs on
  fuseblk; NSK 1.5 s).
- `load()` reads its own line with `os.pread` (fd cached per pid → fork-safe for a multiprocessing
  builder). Warm load ≈ 13-15 ms/doc for AP/ElSyn, ≈ 25 ms NSK, ≈ 80 ms coa (big PDFs).
- `work/coa_text/` and `work/eisap_text/`: `<md5>.txt` (pdftotext output) + `manifest.json`
  (`size, mtime_ns, md5, chars, mode, q`). First build ≈ 110 s (coa) / 70 s (eisap), under
  `nice -n 19 ionice -c3`. A new/changed PDF is extracted on the next `iter_units`.
- Max RSS of a full pass ≈ 120 MB.

## Fingerprints

`v<PARSER_VERSION[source]>:` + content id. jsonl sources: crc32 of the raw line. NSK: crc32 of the
line + `chars:conf` of the OCR text from `nsk/ocr_done.jsonl` (or `-` when there is no text yet), so
an opinion is re-indexed exactly when its OCR text appears. PDFs: md5 + extraction mode. Bump
`PARSER_VERSION[...]` after changing cleaning/chunking for a source → the builder re-indexes it.

## Chunking (shared)

`paragraphs()` → `clean_paragraphs()` → `chunk_decision()`:
- paragraph extraction: paragraph-per-line sources (AP, modern ElSyn, ΣτΕ) keep lines as
  paragraphs but re-join obvious continuation lines; hard-wrapped text (old ElSyn `.doc` exports,
  OCR) is re-flowed (line width p85, terminal punctuation, numbering, blank-line statistics,
  de-hyphenation); pdftotext `-layout` output additionally uses indentation.
- dropped: running headers/footers (short lines repeated ≥2× with digits wildcarded), page numbers,
  typist codes (`Σ.Ν./apf/tm.ΕΒΔΟΜΟ 2026/…`, `prk/k12.11/EYN. : 0018 — Θ΄`), signature blocks
  (`Ο ΠΡΟΕΔΡΟΣ Η ΓΡΑΜΜΑΤΕΑΣ` and the name lines after the operative part), bare court headers
  (`ΕΛΛΗΝΙΚΗ ΔΗΜΟΚΡΑΤΙΑ`, `ΤΟ ΕΛΕΓΚΤΙΚΟ ΣΥΝΕΔΡΙΟ`, …). Letter-spaced words (`Α φ ο ύ μ ε λ έ τ η σ ε`)
  are collapsed; PDF look-alikes µ→μ, ∆→Δ, Ω(ohm)→Ω.
- zones: `εισαγωγή` (composition, parties, procedural history) → body from `ΣΚΕΦΘΗΚΕ…`/`Αφού
  μελέτησε…`/first `1.`/first `Επειδή` → `διατακτικό` from `ΓΙΑ ΤΟΥΣ ΛΟΓΟΥΣ ΑΥΤΟΥΣ`/`Δια ταύτα`
  (kept, with the Κρίθηκε/Δημοσιεύθηκε lines).
- locators: `σκέψη 3` / `σκέψεις 3-5` (numbered σκέψεις, else counted `Επειδή` paragraphs),
  `ΙΙ. σκέψη 4` when a Roman section is open, `§ 4-6` for unnumbered body paragraphs, `εισαγωγή`,
  `διατακτικό`, `περίληψη` (metadata chunk), `στοιχεία` (ElSyn metadata chunk), `κείμενο i/n` when a
  text has no recognisable structure (most NSK OCR, eisap PDFs).
- sizes: greedy merge to ≥400 / ≤1500 chars, new chunk at each numbered σκέψη once ≥400, zone change
  flushes once ≥150; paragraphs >2000 chars are cut at sentence ends into ≤1500 pieces (whitespace
  fallback, never mid-word); hard max 2500 (0 violations in the full pass). Tiny tails are merged.
- `law`/`article` are always None (case law); citations inside the text are left to xref.py.

## Per-source notes

### Άρειος Πάγος
- Site chrome before the line `Συνέχεια` (Google Translate / ChatGPT / Claude / DeepSeek / Grok
  summary buttons + AI disclaimer) is cut; all 32,872 have it at the same offset.
- Header `Απόφαση 1230 / 1991 (Γ, ΠΟΛΙΤΙΚΕΣ)` → `meta.chamber` "Γ΄ Πολιτικό Τμήμα", `meta.division`
  civil|criminal, `chamber_raw`. Values seen: Α/Α1/Α2/Α3/Β/Β1/Β2/Γ/Δ civil, Α/Β/Δ/Ε/ΣΤ/Ζ criminal,
  ΟΛΟΜΕΛΕΙΑ (→ citation `ΟλΑΠ n/y`), `Α|Β Ποιν. Διακ.` (vacation chambers), `Νόμου 3068` (68
  compliance cases, kept raw), `Αγνωστο πινάκιο` (→ None).
- ≈3,079 decisions (mostly 2010-2013) carry the court's own **Θέμα** (keywords) and **Περίληψη**
  block after the header → `meta.subject`, `meta.summary`, `title` = subject, and a seq-0
  `περίληψη` chunk. Other AP docs have no subject (title = "Άρειος Πάγος n/y — chamber").
- Date = `Δημοσιεύθηκε … στις <date>` (publication) → else `Κρίθηκε και αποφασίστηκε` → else hearing;
  `meta.date_kind` says which. ≈100 % dated.

### Ελεγκτικό Συνέδριο (TNP)
- Keys: id, docDate (= upload timestamp, NOT the decision date → `meta.uploaded`), filename,
  aaApof (number; zero-stripped; 2,027 are non-numeric like `0018-ΓΕΝ-Θ` for Πρακτικά), yrApof,
  docType (Πράξη 19,474 / Απόφαση 17,528 / Πρακτικό 1,939 / Απόφαση σε Συμβούλιο 497 / None 2),
  judicialFormation (27 values: I–VII Τμήμα, Α΄–ΣΤ΄ Κλιμάκιο, Κλιμάκιο Προληπτικού Ελέγχου…, Ολομέλεια…),
  and only for ~4.4k recent docs: docAuthor, president, eisigitis (→ `meta.rapporteur`), result,
  introductoryType; abdCaseNumbers / composition for <100.
- `title` = `<docType> n/y <formation> — <subject from filename>` when the .doc filename carries one
  (e.g. `0250-ΥΠΟΥΡΓΕΙΟ ΥΠΟΔΟΜΩΝ ΠΙΝΑΚΙΔΕΣ.doc`).
- seq-0 `στοιχεία` chunk (result / introductory type / composition / ΑΒΔ) only when those exist.
- Older docs are hard-wrapped .doc exports with running headers → re-flowed + header removal.
- Date from the text (Δημοσιεύθηκε / Κρίθηκε / Συνεδρίασε / "της 7ης Νοεμβρίου 2011" in Πρακτικά): ≈96 %.

### ΝΣΚ
- `provisions` is scraped page soup; it is re-joined with ", " and parsed by `parse_nsk_provisions()`:
  real provisions before `Πρόεδρος/Προεδρεύων :`, then president, rapporteur, `Κατάσταση :` status
  (`Αποδεκτή`→accepted, `Εκκρεμεί αποδοχή`→pending, `Μη αποδεκτή`→rejected, `Μερικώς αποδεκτή`→
  partly_accepted, the "Για την αποδοχή ή μη επικοινωνήστε…" boiler → unknown), then the opinion
  summary; everything after `-->ΛΗΨΗ ΑΡΧΕΙΟΥ`/`$(document)` (jQuery, site footer) is dropped. Older
  records embed `Προεδρεύων: X, Αντιπρόεδρος Εισηγητής: Y, Νομικός Σύμβουλος` (or `(Τμ.Β) Προεδρεύων,
  …`) at the start of the summary → moved to president/rapporteur/`formation`.
- `meta.provisions` = clean strings (`Ν 3463/2006Α146`, `ΠΔ 410/1995Α187`, `ΑΚΑ440`, `ΥΑ …`);
  `meta.provisions_parsed` = best-effort `{raw, kind, number, year, article, par}` (Α=άρθρο, Π=παρ.);
  xref.py should own canonical law keys.
- Chunks: seq-0 `περίληψη` = citation + Θέμα + Λήμματα + Περίληψη + Διατάξεις (split if >2500),
  then the OCR text (`κείμενο i/n`, or σκέψεις when numbered). Opinions with no subject, no summary
  and no text yet (pre-1986 mostly) get a one-line placeholder chunk and `meta.placeholder=True` —
  the builder may skip embedding those; they exist so the citation resolves and so the doc is
  upgraded when its OCR lands.
- OCR text: only files listed in `ocr_done.jsonl` are used (the runner writes .txt then logs; a
  .txt younger than 6 h without a log line is treated as not-yet-done; `.part` never matched).
  `ocr_conf` = that log's mean confidence. OCR junk lines (mixed-script tokens, symbol soup) are
  dropped, but the 1950s-70s typewriter OCR is still noisy (conf 0.7-0.85) — expect lexical recall
  to be partial on those. 2,407 opinions have no PDF at all (metadata only, forever).
- Date: hearing date `Συνεδρίασις της 24 Ν/ρίου 1960` (abbreviated months handled) or the header
  date; only for OCR'd docs (~28 % of a mixed sample; most metadata-only docs have no date).

### ΣτΕ
- 4,966 records in 6 harvest files, 0 duplicate ids (dedupe by id kept anyway, longest text wins).
- **Only 167 have text**; 4,783 are `has_pdf_only` with empty text and 16 more are empty → skipped
  (nothing to search). Coverage: 1929-1942 Ολομέλεια/Β΄/Α΄ (≈20/yr after 1936) + 51 from 2015.
  When the ΣτΕ harvester is resumed, new records appear automatically.
- `date` from the listing field `dd/mm/yyyy` (`date_kind: listing`), else from the text
  (`Εκρίθη και απεφασίσθη … τη 4η Μαρτίου 1932`). `meta.ecli`, `meta.number_raw`.

### Εισαγγελία ΑΠ (eisap)
- Kept: kind `gnomodotisi` (772) + `egkyklios` (454) + 3 `other` posts whose title is
  Γνωμοδότηση/Εγκύκλιος = 1,229. **Skipped**: the other 408 `other` posts — news, obituaries,
  visits/conferences, 49 old Drupal "about" pages and prosecutor biographies: not legal content.
- The posts are almost all **title + one subject line + author** (the full text is an embedded PDF
  viewer, stripped: "Loading… Reload document… Αποθήκευση αρχείου", Word-HTML junk). So each post is
  one short chunk (median ≈200 chars) = searchable subject; `fulltext_in_post` flags the few longer.
- `date` = None (the WordPress post date is the upload date → `meta.posted`); `year` from the title.
- PDFs: 436 files, **381 are scans with no text layer** (not OCR'd: no tesseract, Paddle busy).
  Of the 55 with text, press releases/speeches/programmes are skipped by filename; 19 kept
  (`eisap:pdf:<stem>`, e.g. EGKIKLIOS_AP_2023_04_26, election instructions, fek529b2023). PDFs whose
  `-layout` text is letter-spaced are re-extracted with `-raw`.
- `meta.pdf_candidates` links a post to scanned PDFs when the filename pattern matches
  (`10-20-ΓΝΩΜΟΔΟΤΗΣΗ.pdf` ↔ Γνωμοδότηση 10/2020) — for a future OCR pass. The harvester did not
  keep media→post ids, so there is no reliable link for the rest.

### Court-of-audit curated PDFs (coa)
- 528 PDFs → 491 unique by md5 (the `_EN` copies are byte-identical; canonical = non-`_EN`,
  shorter name; others in `meta.duplicates`).
- Skipped: 2 scans (no text) and **48 with a broken font encoding** (pdftotext emits symbols, 0 %
  Greek letters; measured by `q` in the manifest) — mostly the plenary minutes
  `Nη ΓΕΝΙΚΗ/ΕΙΔΙΚΗ ΣΥΝΕΔΡΙΑΣΗ yyyy` of 2010-2015 and some 2020-2021 Πρώτο Τμήμα decisions. They
  need OCR later. → 441 docs.
- Number/year/kind from the first lines (`Απόφαση 19/2025`), else the filename; formation from the
  header line (`ΣΕ ΕΛΑΣΣΟΝΑ ΟΛΟΜΕΛΕΙΑ`, `ΕΒΔΟΜΟ ΤΜΗΜΑ`, `ΤΜΗΜΑ VII`) or filename; plenary minutes →
  `ΕλΣυν Ολ. Πρακτ. 14ης Γεν. Συν./2019`. Date ≈ 99 %.
- These overlap with `elsyn` (the TNP database holds most of the same decisions). Not cross-deduped:
  different ids, and the PDFs are the better-formatted copy for the recent big plenary decisions.

## Known gaps / for the main builder
- NSK OCR is still running (≈25.8k of 53.9k texts at writing, oldest years first); just re-run the
  incremental build — fingerprints flip as texts land. OCR of 1950s-70s is noisy.
- ΣτΕ is basically missing (167 docs) until its harvester is resumed.
- 381 eisap PDF scans and 50 coa PDFs need OCR.
- Elsyn/coa dates are decision dates detected from text, not metadata (docDate is the upload time).
- `meta` is JSON-serialisable; big fields: NSK `provisions_parsed`, AP `summary`.
- Suggested: skip embedding chunks of docs with `meta.placeholder` (NSK metadata-free opinions).

## Measured (full load pass of every doc, 2026-10-21, final code, 1 thread, nice 19 / ionice idle)

| source | docs | chunks | chars | dated | load time (I/O-starved box) | errors |
|---|---|---|---|---|---|---|
| areios_pagos | 32,872 | 660,429 | 785 M | 32,860 (99.96 %) | 440 s | 0 |
| elsyn | 39,440 | 445,482 | 494 M | 38,807 (98.4 %) | 541 s | 0 |
| nsk | 56,334 (25,860 with OCR text, 1,036 placeholders) | 131,542 | 138 M | 16,788 | 1,560 s | 0 |
| ste | 167 | 1,663 | 1.8 M | 141 | 4 s | 0 |
| eisap | 1,248 | 1,398 | 0.4 M | 14 | 13 s | 0 |
| coa | 441 | 14,897 | 17 M | 438 | 45 s | 0 |
| **total** | **130,502** | **1,255,411** | **1.44 G** | | | |

Chunk sizes: ~91 % between 400 and 2500, 0 over 2500; <150 chars: 9.5k (mostly ElSyn one-line
διατακτικά and `στοιχεία` chunks, eisap one-line posts, NSK placeholders). `iter_units` (warm) over
all six sources ≈ 4 s; max RSS 94 MB.
One AP record (`ap:1349/2023`) has an empty text → 0 chunks. A few AP records are court notices
("Δεν υφίσταται η 659/2013 απόφαση", duplicate-publication notices) — kept, they are 1 short chunk.
