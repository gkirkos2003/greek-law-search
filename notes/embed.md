# Dense embeddings — model choice + numbers (agent embed, 2026-10-21)

Machine: desktop i7-8700K (AVX2, **no AVX-512/VNNI**), CPU only, 3 intra-op threads, `nice 19`.
The box was busy the whole time (load avg 6–11: greek-ocr + the other build agents), so the
throughput numbers below are **what to expect while OCR runs**. They should go up once OCR is done.
Bench code + data: `search/work/embed_bench/` (`build_sample.py`, `bench.py`, `hybrid_check.py`,
`fusion_sweep.py`, `results.jsonl`, `hybrid_results.json`).

## Test data (real corpus, read-only)
- **Speed set**: 300 passages of 400–1500 chars (avg 971): 90 ΦΕΚ pre-2000 (OCR), 90 ΦΕΚ 2000+
  (pdftotext with mixed-up columns), 60 ΝΣΚ OCR, 60 Άρειος Πάγος.
- **Quality pool**: 4,219 passages (2,160 ΦΕΚ, 959 ΝΣΚ, 1,100 ΑΠ), with 344 queries whose correct passage is known:
  - 160 `fek_title`: law title from `index.jsonl` (lowercase, no accents) → a 1,200-char chunk starting at "Άρθρο 1" of that issue
  - 160 `nsk_subject`: ΝΣΚ `subject` (1951–74, the ones OCR has finished) → the opinion's question/answer window
  - 24 English queries I translated from 12+12 of the above (`pairs_en.jsonl`)
- Measures: MRR@10 and hit@5/10, scored over the full pool.

## Tokens per 1000 Greek chars (XLM-R sentencepiece, same for all e5/bge models)
| ΦΕΚ OCR | ΦΕΚ 2000+ | ΝΣΚ OCR | ΑΠ (clean) |
|---|---|---|---|
| 410 | 433–456 | 444 | 300 |

So a 1,000-char chunk is about 300–450 tokens. **max_len 256 covers about the first 600 chars** of a chunk.
Noisy OCR breaks into more pieces, which is why it costs more tokens.

## Speed (3 threads, chunks/s, batch 16, length-sorted)
| model | variant | max_len 128 | max_len 256 | max_len 512 | RSS | query encode |
|---|---|---|---|---|---|---|
| multilingual-e5-small (384d) | fp32 Xenova | – | 14.7 | – | 1.4 GB | 6 ms |
| multilingual-e5-small | **fp32 fused (`model_opt.onnx`)** | 43.3 | **17.1** | 10.0 | 1.1 GB | 5 ms |
| multilingual-e5-small | int8 Xenova (`model_quantized.onnx`) | 50.8 | 18.2 | – | 1.0 GB | 4–5 ms |
| multilingual-e5-small | int8 of fused, per-channel | – | 19.0 | – | 1.1 GB | 3.5 ms |
| paraphrase-multilingual-MiniLM-L12 | int8 | 51.1 | – | – | 0.9 GB | 4 ms |
| multilingual-e5-base (768d) | int8 | – | 6.5 | – | 0.9 GB | 10 ms |
| multilingual-e5-base | fp32 | – | 4.7 | – | 1.9 GB | 19 ms |
| bge-m3 (1024d) | int8 | – | – | **0.86** | 2.0 GB | 28 ms |

Without VNNI, int8 is only 1.06–1.2× faster than fused fp32, because AVX2 int8 GEMM gains little.
Fusing the graph (Attention/SkipLayerNorm/BiasGelu via `onnxruntime.transformers.optimizer`) gives
**exactly the same vectors** as plain fp32 (cosine 1.00000) and runs about 16% faster.
bge-m3 would take about 34 days for 2.5M chunks. e5-base would take about 4.5 days. Both are out.

## Quality (MRR@10 / hit@5 / hit@10 over a 4,219-passage pool)
| system | GR all (320) | fek_title | nsk_subject | EN (24) |
|---|---|---|---|---|
| **BM25** (PyStemmer greek, accent-folded) | **0.491** / 0.578 / 0.625 | 0.584 / 0.662 / 0.688 | 0.397 / 0.494 / 0.562 | 0.013 / 0.000 / 0.083 |
| e5-small fp32 @256 | 0.333 / 0.403 / 0.469 | 0.422 / 0.463 / 0.512 | 0.244 / 0.344 / 0.425 | **0.208** / 0.208 / 0.375 |
| e5-small int8 @256 | 0.304 / 0.400 / 0.444 | 0.389 / 0.450 / 0.487 | 0.220 / 0.350 / 0.400 | 0.144 / 0.250 / 0.292 |
| e5-small int8 @128 | 0.245 / 0.294 / 0.347 | 0.331 / 0.375 / 0.425 | 0.160 / 0.212 / 0.269 | 0.111 / 0.125 / 0.167 |
| MiniLM-L12 int8 @128 | 0.119 / 0.169 / 0.184 | 0.198 / 0.275 / 0.300 | 0.039 / 0.062 / 0.069 | 0.200 / 0.208 / 0.292 |
| BM25 + e5 RRF(60), equal weight | 0.449 / 0.550 / 0.619 | 0.538 | 0.360 | 0.037 |
| BM25 + e5 RRF, dense weight 0.25 | 0.484 / 0.594 / 0.647 | | | |
| **BM25 top-200 rescored, 0.5·bm25norm + 0.5·cos** | **0.511 / 0.603 / 0.659** | | | |

What the table shows:
- **int8 costs about 9% MRR** (0.333 → 0.304) and saves only about 6% time, so it is not worth it. **Truncating to 128 tokens costs about 26%** (0.333 → 0.245).
- MiniLM is bad on Greek. e5-base quality was not measured: at 6.5 chunks/s it cannot embed the corpus anyway.
- **BM25 beats dense on Greek queries, and naive equal-weight RRF makes results worse** (0.491 → 0.449).
  The dense signal helps only when weighted low, or used to **rescore BM25's top-200** (+4% MRR, +2.5 pt hit@5).
  This test set is lexically friendly to BM25: titles and subjects share words with the target text.
  Paraphrased real questions will favour dense more.
- **Dense is the only thing that works for English queries.** BM25 gets 0.01 there (EN hit@10: dense 0.375). A query
  that is mostly Latin script should go **dense-only**, or be translated to Greek first.
- OCR noise ("ζήτήμα", "έρωτήματος", broken words) hurts both signals. ΝΣΚ is the weakest set.

## Recommendation
- **Model: `intfloat/multilingual-e5-small`, fused fp32 ONNX (`models/e5-small/onnx/model_opt.onnx`), 384-d,
  max_len 256, batch 16.** This is the `Embedder()` default. Prefixes `query: ` / `passage: ` are added internally.
  Keep `model_quantized.onnx` (int8) only as a fallback when time matters more than quality.
- **Text to embed per chunk:** `lib.embed.embed_text(locator, text, title)` gives `"<title ≤100 chars> — <locator>. <body>"`,
  e.g. `"Νέα Αρχιτεκτονική της Αυτοδιοίκησης — ν. 3852/2010 — άρθρο 5. Οι δήμοι …"`. The tokenizer then
  truncates to 256 tokens, which is the header plus about the first 550 chars of the body. The law or decision title adds
  topic words the article body often lacks. **Keep the title short.** Every header token is one fewer body token.
  For 2000+ ΦΕΚ, use the de-columned text (`fek_deint`): interleaved columns make the embeddings meaningless.
- **One vector per chunk.** Multi-window embedding would double the cost.
- **How to use it in search:** BM25 does the first pass. Dense rescoring of the BM25 top-200 to top-500 is a cheap random read of 200
  rows (`rescore()`) and needs no matrix in RAM. Combine with min-max normalised BM25 × 0.5 + cosine × 0.5,
  or weighted RRF with dense weight about 0.25. **Full-corpus dense search** (`DenseIndex`) is needed only for English
  or "no BM25 hits" queries, and optionally merged in with low weight for semantic recall.

## Cost of the full corpus (2.5M chunks, 3 threads)
| config | chunks/s | wall time |
|---|---|---|
| **e5-small fused fp32 @256 (recommended)** | 17 | **≈ 41 h (1.7 days)** |
| e5-small int8 @256 | 18–19 | ≈ 37 h |
| e5-small fp32 @128 | 43 | ≈ 16 h (−26% quality) |
| e5-small fp32 @512 | 10 | ≈ 70 h |

These were measured while OCR was running. After OCR finishes (ETA about 10-22) the per-thread rate should improve.
Six threads would be roughly 1.7× faster (estimate, not measured). Embedding is incremental: chunks are keyed by
fingerprint, so only new or changed chunks get re-embedded. Run it as a resumable job that writes one shard per
about 100k chunks. Peak RSS is about 1.1 GB.

## Vector storage + query latency (measured on a random 1.25M×384 matrix, single query; ×2 for 2.5M)
| storage of 2.5M×384 | RAM / disk | brute-force top-100, 3 threads |
|---|---|---|
| float32 | 3.84 GB | (not kept: too big) |
| float16 shards, numpy (upcast in blocks) | 1.92 GB | ≈ 2.8–3.2 s (numpy f16→f32 is slow, ~3 ns/elem) |
| **float16 shards, ORT Cast+MatMul (`DenseIndex`)** | 1.92 GB | **≈ 0.41 s** |
| **int8 copy in RAM, ORT MatMulInteger (`DenseIndex(int8=True)`)** | **0.96 GB** | **≈ 0.12 s** |
| numpy int8→f32 blocked | 0.96 GB | ≈ 0.45 s |

int8 vector quantisation uses a fixed global scale of 127/0.2 (real e5 components stay under 0.18). It keeps
recall@100 = 0.97 against fp32 and MRR 0.319 vs 0.324. **No ANN index is needed.** An int8 brute-force pass is about
0.12 s at 2.5M rows, and even 5M rows would be about 0.25 s. Exact search has no recall loss and nothing to build or
update. Revisit only if the corpus grows past about 10M chunks: faiss IVF-SQ8 would then be the cheapest step.
Keep the f16 `.npy` shards as the source of truth on disk. The search process loads them once (mmap) and either
keeps f16 pages, or builds the int8 copy at start-up (0.96 GB; plus about 0.5 GB for the query encoder).

## API (`search/lib/embed.py`)
```python
from lib.embed import Embedder, embed_text, save_shard, load_shards, DenseIndex, rescore, to_int8
emb = Embedder()                                   # e5-small fused fp32, 3 threads, max_len 256
V = emb.encode([embed_text(c["locator"], c["text"], doc["title"]) for c in chunks])   # float32 [n,384], L2-normed
save_shard("work/vec/shard_0000.npy", V)           # float16 .npy, atomic replace
mats = load_shards(sorted(glob("work/vec/shard_*.npy")))     # mmap'd float16
q = emb.encode("ακύρωση απόλυσης", kind="query")[0]
ids, cos = DenseIndex(mats, int8=True).search(q, k=200)      # global row ids across shards
cos_c = rescore(mats, q, bm25_candidate_rows)                 # dense score for BM25 candidates
```
`Embedder(name_or_dir, threads=3, max_len=None, quantized=False, onnx_file=None)`, plus `.dim`, `.name`, `.max_len`,
and `.tokenize()`. Registry keys: `e5-small` (on disk), `e5-base` (int8 only kept on disk). `minilm-l12` and `bge-m3` were
deleted after benchmarking. Needs `onnx` + `sympy` (installed in the venv) only for the ORT scorer
and re-fusing. `DenseIndex` falls back to numpy if `onnx` is missing.
