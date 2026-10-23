# Greek law search: ΦΕΚ + case law + ΝΣΚ

Hybrid (BM25 + dense) search over Greek legislation and case law: every Government Gazette issue
(ΦΕΚ Α΄ 1833–2026, ΦΕΚ Β΄ 2000–2026), Areios Pagos, Court of Audit, Council of State and Legal Council of
the State (ΝΣΚ) opinions: **281,067 documents, 5.8 M passages, 7.1 M cross-references**, runs fully local on CPU.
On 36 real questions with verified answers the right document is in the top 5 for **86%** (hybrid) vs 69%
for keyword search alone; held-out English questions are reported separately (0.33) because they were not tuned on.

I built this as a solo project with AI coding agents (Claude Code) doing most of the typing: I set the design,
the rules every build agent had to follow (`DESIGN.md`), the evaluation set and the acceptance numbers, and
reviewed the results. The corpus (~230 GB of PDFs, OCR text and court data) and the 10 GB index are not in
this repo; point `GREEKLAW_ROOT` at your own copy.

Local search over the corpus in `$GREEKLAW_ROOT`. You ask a question in Greek or English and get the
top passages with an exact citation (ΦΕΚ issue / law / article, or court + decision number + date) and the
source file. It also gives cross-references ("law X was amended by Y", "who cites article N of law X").

Code, venv, model and index all live in `search/`. Everything runs on CPU.

```
cd $GREEKLAW_ROOT/search
venv/bin/python search.py "Πόσες μέρες άδεια πατρότητας δικαιούται ο εργαζόμενος;"
venv/bin/python search.py "minimum capital for a Greek IKE company"
venv/bin/python search.py "Τι προβλέπει το άρθρο 5 του ν. 4427/2016;"
venv/bin/python search.py -k 20 --source nsk,areios_pagos --years 2000-2026 "παραγραφή αξιώσεων Δημοσίου"
venv/bin/python search.py --law "ν. 4412/2016"            # ΦΕΚ, what changed it, who cites it (grouped per decision)
venv/bin/python search.py --law "άρθρο 73 ν. 4412/2016"   # same, one article
venv/bin/python search.py --json "..."                    # machine-readable (adds per-passage citations)
venv/bin/python search.py --mode bm25 "..."               # keyword only (default: hybrid)
```
`--source` takes `fek_a, fek_b, nsk, areios_pagos, elsyn, ste, eisap, coa`. Query time: see "Speed" below.

Each hit shows the citation, locator (article / § / σκέψη), date, source, OCR confidence (for scans), the
law or decision title, a snippet, the laws the passage cites (`↳ cites:`) and the file path. If a returned
law was later amended or repealed, a `⚠ … was later touched by` line follows.

## Rebuild / update (incremental)
```
nice -n 19 ionice -c3 venv/bin/python build_index.py all     # text → refts → xref → embed, only what changed
nice -n 19 ionice -c3 venv/bin/python build_index.py text [--sources nsk]
nice -n 19 ionice -c3 venv/bin/python build_index.py xref
OMP_NUM_THREADS=3 nice -n 19 ionice -c3 venv/bin/python build_index.py embed --threads 3 [--sources fek_a,nsk] [--max N]
venv/bin/python build_index.py stats
```
- **text**: each source yields units with a fingerprint (file size+mtime, or a CRC of the jsonl line, plus the OCR
  text's chars:conf for ΝΣΚ). Only new or changed docs are re-parsed. Vanished docs are deleted.
  Bumping `PARSER_VERSION` in a `src_*.py` re-indexes that source.
- **refts**: re-tokenises FTS rows after a `lib/textnorm.py` change (`NORM_VERSION`), keeping chunk ids, so the
  vectors stay valid.
- **xref**: extracts citations / amendments for docs not scanned yet.
- **embed**: embeds chunks that have no vector yet, best value first: the first passage of every doc, then the
  first passage of every ΦΕΚ article, then the rest. Stop it any time with SIGTERM; it saves its shard and resumes next run.
- Run long steps in tmux.
- DB writers wait up to 15 min for a lock; readers 5 min. The DB is on NTFS (fuseblk), so it uses a rollback
  journal, not WAL. A long read blocks a writer's commit.

## What is in the index (2026-10-22)

| source | docs | chunks | text from | notes |
|---|---|---|---|---|
| ΦΕΚ Α΄ (`fek_a`) | 41,036 | 1,260,069 | PaddleOCR (1833–1999), re-extracted pdftotext (2000+) | article-level chunks, law + article metadata |
| ΦΕΚ Β΄ (`fek_b`) | 109,529 | 3,309,807 | pdftotext 2000–2026 | ministerial decisions; acts + articles split, act number as locator |
| Άρειος Πάγος (`areios_pagos`) | 32,872 | 660,429 | jsonl | σκέψη / § locators, chamber |
| Ελεγκτικό Συνέδριο (`elsyn`) | 39,440 | 445,482 | TNP jsonl | decisions, πράξεις, πρακτικά |
| ΝΣΚ (`nsk`) | 56,334 | 140,421 | metadata + OCR text (~26k opinions had text at build time) | all opinions have the subject/summary; full text as OCR lands |
| ΣτΕ (`ste`) | 167 | 1,663 | jsonl | only 167 texts exist in the corpus (1929–36 + 51 from 2015) |
| ΕισΑΠ (`eisap`) | 1,248 | 1,398 | posts + PDFs | |
| Ελ.Συν. PDFs (`coa`) | 441 | 14,897 | pdftotext | |
| **total** | **281,067** | **5,834,166** | | `index/law.db` 10.4 GB, 7.09 M cross-refs |

Inventory and quality report (counts per source and decade, OCR confidence, what is still OCRing):
`notes/inventory.md`, machine-readable in `work/inventory.json`.
- ΦΕΚ Α΄ OCR confidence: mean 0.912 (p10 0.860). ΝΣΚ OCR: mean 0.789, 14.6% below 0.7 (typewritten scans).
- greek-ocr (ΝΣΚ phase) was at 35,390 / 53,927 texts at 01:00 on 10-22 (~500 texts/h), ETA ≈ 10-23 midday.

## How it works
- **Cleaning + chunking** (`lib/src_fek.py`, `lib/fek_deint.py`, `lib/src_caselaw.py`, `lib/src_fekb.py`):
  - **ΦΕΚ 2000+:** re-extracted with plain `pdftotext` (reading order). This fixes the two-column interleave of
    `-layout` and de-hyphenates. 2000–2005 cp1253 mojibake is repaired.
  - **Pre-2000 OCR:** pages whose two columns PaddleOCR interleaved line by line are de-interleaved
    statistically (`fek_deint`, word/bigram tables from the corpus).
  - Issues are split into acts, and acts into articles (`Άρθρο N`, `Άρθρον N`, ordinals, inline headings,
    OCR variants). Long articles split on §. Each chunk has law, law_key (`ν:4412/2016`) and article.
    Pages with `[[OCR ERROR …]]` are skipped.
  - **Decisions** are split into εισαγωγή / σκέψεις / διατακτικό. Headers, footers and signatures are dropped.
  - Details: `notes/fek.md`, `notes/fekb.md`, `notes/caselaw.md`.
- **Normalisation** (`lib/textnorm.py`): NFD accent strip, lowercase, final ς→σ, Latin/Greek OCR look-alikes,
  omicron-for-zero in numbers, collapsed dotted abbreviations (Γ.Ε.ΜΗ. = ΓΕΜΗ, π.δ. = πδ), PyStemmer Greek
  stemming and stopwords. Index and query go through the same function.
- **BM25**: SQLite FTS5 (contentless, `unicode61` over the pre-normalised text). It has two columns: head
  (citation + locator + title, weight 3) and body. Very common terms are dropped. An OR pass, an AND pass and a
  rarest-3 AND pass are merged, plus a coordination factor (more query terms matched = higher).
- **Dense**: `intfloat/multilingual-e5-small` (ONNX, fused fp32, 384-d, max 256 tokens, CPU at ~16 chunks/s on
  3 threads) gives float16 shards in `index/vec/`. Search is a brute-force matmul with no ANN, ~0.4 s at 2.5 M.
  Candidates that have no stored vector yet get one computed at query time (top 40 BM25 candidates).
- **Hybrid** (`lib/searcher.py`): candidates = BM25 top 200 ∪ dense top 200 ∪ chunks of laws cited in the query.
  - score = 0.5·minmax(BM25) + 0.5·minmax(cosine), +0.15 for a chunk of a law named in the query,
    −0.2 for ΦΕΚ Β΄ chunks unless you picked sources with `--source` (`GL_FEKB_PEN`, see evaluation).
    Β΄ is 57% of all chunks and its long ministerial decisions match almost any query; without the
    penalty they took ~30% of all top-5 slots.
  - A chunk of an article named in the query ("άρθρο 46 του π.δ. 18/1989") always comes first, in both modes.
  - At most 2 passages per document.
  - English questions: BM25 searches the Greek terms from a ~570-entry English→Greek legal glossary
    (`lib/glossary.py`, incl. regions and cities) plus the question's numbers. English words alone only match
    the English text of ratified treaties.
- **Cross-references** (`lib/xref.py`, table `xrefs`): every chunk's citations are extracted with their relation:
  `amends / replaces / repeals / adds / cites`. The parser handles ν., π.δ., ν.δ., α.ν., β.δ. by date,
  codes (ΑΚ, ΚΠολΔ…), Σύνταγμα, EU acts, "του ίδιου νόμου", OCR variants, and law → ΦΕΚ resolution
  (99% of extracted act citations resolve). Details: `notes/xref.md`.

## Evaluation
36 real questions with known answers (`eval/questions.jsonl`): 15 ΦΕΚ Α΄, 9 ΝΣΚ, 6 ΑΠ, 4 ΕλΣυν, 2 ΣτΕ; 28 Greek,
8 English. Every gold document and its evidence sentence was found and verified by reading the source data, not
the index (`eval/README_eval.md`). There are also 12 **held-out English** questions
(`eval/questions_en_holdout.jsonl`): translations of Greek questions, written after the tuning and not used for
any tuning. And 4 **ΦΕΚ Β΄** questions (`eval/questions_fekb.jsonl`: 2002 smoking ban, 2016 drone rules, 2022 telecom
consumer code, 2024 minimum wage), checked against the source text, added so the Β΄ penalty could be measured.

A hit means a passage from the gold document is in the top 5 (hit@5).

**Final run, 2026-10-22 00:35** (`eval/results*.md`). Full index (5.83 M chunks incl. ΦΕΚ Β΄), 1,027,807 stored
vectors (18% of chunks), eval setting `--ondemand 200` (on-the-fly vectors for the top 200 BM25 candidates
without one). Hybrid = the default fusion (0.5/0.5, Β΄ penalty 0.2).

| set / group | n | BM25 hit@5 | BM25 MRR | hybrid hit@5 | hybrid MRR |
|---|---|---|---|---|---|
| **main, all** | 36 | 0.69 | 0.56 | **0.86** | 0.67 |
| main, Greek | 28 | 0.75 | 0.66 | **0.89** | 0.68 |
| main, English (tuned on) | 8 | 0.50 | 0.23 | 0.75 | 0.60 |
| ΦΕΚ Α΄ | 15 | 0.47 | 0.39 | **0.93** | 0.62 |
| ΝΣΚ | 9 | 1.00 | 0.78 | 1.00 | 0.80 |
| Άρειος Πάγος | 6 | 0.83 | 0.67 | 0.83 | 0.75 |
| Ελεγκτικό Συνέδριο | 4 | 0.75 | 0.75 | 0.75 | 0.75 |
| ΣτΕ | 2 | 0.50 | 0.25 | 0.00 | 0.00 |
| **held-out English** (`questions_en_holdout`) | 12 | 0.25 | 0.19 | **0.33** | 0.29 |
| **ΦΕΚ Β΄** (`questions_fekb`) | 4 | 0.50 | 0.50 | 0.50 | 0.31 |

ΦΕΚ Α΄ article-level hit@5 (right law **and** right article in the top 5): BM25 0.27 → hybrid 0.60.

How the fusion was chosen (`tools/fusion_sweep.py`, `eval/sweep_fekb.json`, all 52 questions, hit@5):

| setting | Greek (31) | English (21) | ΦΕΚ Β΄ (4) |
|---|---|---|---|
| BM25 only | 0.74 | 0.33 | 0.50 |
| hybrid, raw cosine (old fusion) | 0.74 | 0.33 | 0.50 |
| hybrid, min-max cosine, wd 0.5 | 0.74 | 0.43 | 0.50 |
| … + Β΄ penalty 0.1 | 0.81 | 0.43 | 0.50 |
| **… + Β΄ penalty 0.2 (default)** | **0.87** | **0.48** | **0.50** |
| … + Β΄ penalty 0.3 | 0.90 | 0.48 | 0.50 (Β΄ golds ranked lower) |
| dense only (wd 1.0) | 0.68 | 0.14 | 0.25 |

Earlier runs for comparison: 10-21 13:16 (old raw-cosine fusion, 440k vectors, no ΦΕΚ Β΄ in the index):
BM25 0.61 / hybrid 0.64 on the main set. 10-21 19:50 (new fusion, 820k vectors, Β΄ half loaded, no penalty):
0.72 / 0.75.

**With the CLI's own setting** (`search.py` default: on-the-fly vectors for the top 40 only, 3 threads;
`eval/cli_results*.md`): main set hybrid hit@5 **0.83** (Greek 0.86, English 0.75, ΦΕΚ Α΄ 0.93, ΝΣΚ 0.89),
held-out English 0.33, ΦΕΚ Β΄ 0.50. Slightly below the `--ondemand 200` run, which is ~2× slower per query.
Both gaps close as more passages get stored vectors.

### Speed (measured 2026-10-22 02:00, no other DB writer running, spinning disk)
| what | time |
|---|---|
| hybrid query, warm (`search.py "…"`) | 4–6 s in the search, ~7 s wall incl. Python start + model load |
| hybrid query, cold cache | up to ~16 s (disk seeks) |
| `--mode bm25` | ~1–2 s |
| `--source` / `--years` filtered query | ~2 s (filter runs in memory, `lib/chunkmap.py`) |
| `--law "ν. 4412/2016"` (~4,400 citing docs) | 0.8 s warm, ~40 s cold |
| eval medians (one loaded process) | 3–9 s per hybrid query |

Most of a hybrid query is computing vectors for candidates that have none yet; it gets faster as the
embedding job covers more chunks. While a **DB writer** runs (`build_index.py text` or `xref`), queries can
wait on its commits: the DB is on NTFS, so it uses a rollback journal and readers wait while a commit is
written. The embed job only reads, so it doesn't block queries.

Notes on the numbers:
- **Tuning used the main set** (fusion weights; glossary terms added after seeing the English misses; the Β΄
  penalty was picked on all 52 questions). The main set's numbers are optimistic. **The held-out English row
  is the honest English number.** With only 4 Β΄ questions, the penalty choice rests on little data.
- **Partial vectors.** The dense side only covered the chunks embedded so far (see status). The rest got
  on-the-fly vectors only if they were in BM25's top 200 (top 40 in the CLI). More stored vectors will help English and paraphrased
  questions most.
- **Single gold.** A "miss" can be another document with the same answer, e.g. a later civil-service code with
  the same probation rule (q09), or a later law that changed a deadline (q04).
- **Misses in the final hybrid run** (main set): q07 (EFKA law, at rank 7), q29 (ΑΠ, extortion by a tax
  officer), q32 (ΕλΣυν, pandemic advance payment), q35 / q36 (ΣτΕ: only 167 texts in total).
  q11 / q13 (1954–55 ν.δ. with heavy OCR noise, "Ιδαγένειαν") and q05 (colloquial "μπαμπάς" vs statutory
  "πατέρας / πατρότητα") now hit, at ranks 2 / 4 / 5. ΦΕΚ Β΄ misses: b03 (telecom consumer code; ΑΠ criminal
  decisions fill the top 5) and b04 (English, minimum wage; ν. 5163/2024 of ΦΕΚ Α΄ 199/2024 ranks first).
- Reproduce (stop the embed job first to stay within 4 threads):
  `OMP_NUM_THREADS=2 nice -n 19 venv/bin/python eval/run_eval.py --threads 2 [--qfile questions_en_holdout.jsonl --out results_holdout] [--ondemand 40]`
  writes `eval/results*.md|json`. The fusion sweep (`tools/fusion_sweep.py`) builds candidates once per question
  and scores 20 fusion settings offline (`eval/sweep_*.json`).

## Status / what is left after the OCR finishes
State on 2026-10-22 ~02:00:

- **Done:** all text sources indexed (table above), cross-references for all of them, eval, README.
- **Still running when DONE was written** (all `nice 19 / ionice idle`, ≤ 4 threads, greek-ocr untouched):
  - `build_index.py embed --threads 3` (log `logs/embed9.out`): 4.89 M chunks without a vector at ~9–15
    chunks/s (Β΄ passages are long), so **~4–6 days**. Order: first passage of every ΦΕΚ Β΄ issue, then the first passage of every Β΄ article,
    then the remaining passages of all sources. Stop it any time (`kill -TERM <pid>`), restart with the same command:
    `OMP_NUM_THREADS=3 nice -n 19 ionice -c3 venv/bin/python build_index.py embed --threads 3`.
    Search works the whole time; each query computes vectors for its top BM25 candidates that have none yet.
- **After greek-ocr finishes (ΝΣΚ, ≈ 10-23):** run once
  `cd $GREEKLAW_ROOT/search && nice -n 19 ionice -c3 venv/bin/python build_index.py all`
  (stop the embed job first, `all` ends with the embed step). It re-indexes every ΝΣΚ opinion whose OCR text
  appeared or changed (≈ 28k more with full text), rebuilds the chunk map, scans the new docs for cross-refs and
  embeds what is new. Expect ~1–2 h before the embed step. Then rerun the eval if you want new numbers.
- **Known gaps (need a decision or more data, not just a rerun):**
  - 2,926 ΦΕΚ Α΄ text files hold `[[OCR ERROR MemoryError]]` instead of text (mostly 1910s–1940s).
    greek-ocr will never redo them because the `.txt` exists. List: `work/fek_ocr_redo.txt`. Re-OCR, then `build_index.py all`.
  - 57 ΦΕΚ Α΄ + 88 ΦΕΚ Β΄ (2000–01) scans have no text layer and are not in any OCR pass.
  - ΦΕΚ Β΄ 1930–1999: 48,347 scans (~420k pages) not OCR'd (≈ 5–8 days of OCR). Only 2000–2026 is searchable.
  - ΣτΕ: only 167 decision texts exist in the corpus. ΣτΕ questions mostly fail; a ΣτΕ harvester would be needed.
  - ΕισΑΠ: 381 of 436 PDFs have no text layer (the posts are indexed).
- **Ideas, not built:**
  - English questions are the weak spot (held-out 0.33). Translating the question to Greek first (e.g. the
    a local LLM) would likely help most; the glossary only covers known terms.
  - Article headings as their own FTS field: measured small gains in `tools/heading_probe.py` (q05 16→9, q09 24→11).
  - The DB sits on a spinning disk, so cold queries pay for disk seeks. Moving `index/` (11 GB) to an SSD
    and symlinking it back would cut query time a lot.

## Files
| path | what |
|---|---|
| `search.py` | query CLI |
| `build_index.py` | incremental build (text / refts / xref / embed / stats) |
| `lib/index.py` | SQLite schema, build, refts, xref table |
| `lib/searcher.py` | BM25, hybrid fusion, citation pinning, cross-ref report |
| `lib/chunkmap.py` | chunk → doc → source/year arrays (`index/chunk_doc.npy` …) for in-memory filters + penalty |
| `lib/textnorm.py`, `lib/glossary.py` | normalisation, English→Greek glossary |
| `lib/src_fek.py`, `lib/fek_deint.py`, `lib/src_fekb.py`, `lib/src_caselaw.py` | source parsers |
| `lib/xref.py` | citation / amendment extraction + law→ΦΕΚ resolution |
| `lib/embed.py`, `lib/dense.py` | e5-small ONNX embedder, vector shards, dense search |
| `eval/` | questions, `run_eval.py`, results, sweeps |
| `tools/` | extraction (`extract_fekA.py`, `extract_fekB.py`), probes (`debug_q.py`, `heading_probe.py`, `artfix_eval.py`), build chains |
| `tests/` | parser self-tests + xref gold samples |
| `notes/` | per-module notes: inventory, fek, fekb, caselaw, xref, embed |
| `index/law.db`, `index/vec/` | the index (SQLite FTS5) and vector shards |
| `work/` | extraction caches (fekA_text, fekB_text, caselaw_idx…), law→ΦΕΚ table, inventory |
| `models/e5-small` | the embedding model |
| `DESIGN.md` | build contract / module API |
