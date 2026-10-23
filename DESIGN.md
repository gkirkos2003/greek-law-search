# Greek law search — build contract (shared by all build agents)

Root: `$GREEKLAW_ROOT/search/`  (corpus = parent dir `$GREEKLAW_ROOT/`)

## Hard rules (set by me for every build agent)
- OCR service `greek-ocr` (systemd --user) is RUNNING. Never stop/restart/slow it. Never write to its
  files: `../ocr_done.jsonl`, `../text/`, `../caselaw/nsk/text/`, `../caselaw/nsk/ocr_done.jsonl`. Read-only.
- Write ONLY inside `search/`. No services/timers/systemd-run, no apt, no system pip. Use `search/venv`.
- Heavy commands: `nice -n 19 ionice -c3`, ≤1 thread per agent job (the whole build shares ≤4 threads;
  set `OMP_NUM_THREADS=1` etc.). RAM: keep each process < 2 GB. Box has ~13 GB available, swap is in use.
- NO tesseract ever. Models/caches → `search/models` (`HF_HOME=$GREEKLAW_ROOT/search/models`).
- The data disk is NTFS (fuseblk): many small writes are slow; prefer few big files.
- Never run `pkill -f <pattern>` (kills your own shell). Use exact PIDs.

## Python
`search/venv/bin/python` (3.12). Installed: numpy, onnxruntime, tokenizers, huggingface_hub,
PyStemmer (`Stemmer.Stemmer('greek')` — stems AND folds accents/case), snowballstemmer.
Install more into the venv only if really needed (`search/venv/bin/pip install ...`).

## Module layout (`search/lib/`, import as `lib.xxx` with cwd = search/)
- `textnorm.py` (main builder) — fold(): NFD, strip accents, lowercase, ς→σ; stem tokens for FTS.
- `src_fek.py` (agent fek) — ΦΕΚ Α΄ issues → docs + article chunks.
- `src_caselaw.py` (agent caselaw) — Areios Pagos, Ελ.Συν., ΝΣΚ, ΣτΕ, eisap, court-of-audit PDFs.
- `xref.py` (agent xref) — citation / amendment extraction.
- `embed.py` (agent embed) — CPU embedding model wrapper.
- `index.py`, `../search.py`, `../eval/` (main builder).

## Source module API (src_*.py)
```python
def iter_units(sources=None):
    """Yield Unit for every document available NOW. Must be cheap: no full parsing here
    (stat a file, or read a jsonl line and hash it). sources: optional set of source keys."""
class Unit:            # simple dataclass / namedtuple
    doc_id: str        # globally unique, prefixed: "fekA:20100100001", "ap:1230/1991", "elsyn:192603",
                       # "nsk:252/1979", "ste:Α4691/2015", "eisap:9131", "coa:<filename stem>"
    source: str        # fek_a | areios_pagos | elsyn | nsk | ste | eisap | coa   (fek_b later)
    fingerprint: str   # changes iff the content changes (size+mtime_ns for files; crc32 of line for jsonl)
    load: callable     # -> (doc: dict, chunks: list[dict])
```
doc dict keys (missing = None):
`doc_id, source, citation` (short human cite, e.g. "ΦΕΚ Α΄ 1/07.01.2010", "ΑΠ 1230/1991",
"ΓνωμΝΣΚ 252/1979", "ΣτΕ 4691/2015", "ΕλΣυν 250/2026 (Έβδομο Τμήμα)"), `title, date` (ISO yyyy-mm-dd),
`year` (int), `number` (str), `court` (str or None), `path` (absolute file path; for jsonl records:
"<jsonl path>#<id>"), `ocr_conf` (float or None), `meta` (dict, JSON-serialisable: e.g. list of laws
in the issue, lemmata, provisions, ecli, chamber...).

chunk dict keys: `seq` (int, 0..n), `locator` (human: "ν. 3852/2010 — άρθρο 5" or "σκέψη 3" or "§2"),
`law` (e.g. "ν. 3852/2010", only for legislation chunks, else None), `article` (str like "5", "5Α", None),
`text` (clean text, original accents kept). Target 400–1500 chars per chunk, hard max 2500;
split on article/paragraph boundaries; never cut mid-word. Drop pure boilerplate (page headers,
"ΕΦΗΜΕΡΙΣ ΤΗΣ ΚΥΒΕΡΝΗΣΕΩΣ", signatures, website menus).

## Citation identity keys (xref.py; used everywhere)
`law_key(kind, number, year)` → canonical string, e.g. "ν:4412/2016", "πδ:410/1995", "νδ:276/1974",
"αν:1846/1951", "βδ:...", "κν:...", "ΦΕΚ key" "fekA:2016/188". kind codes: ν (Νόμος), πδ, νδ, αν, βδ,
πνπ (ΠΝΠ), νπδ, κν (κωδικοποιητικός?), ΑΚ etc. for codes (Αστικός Κώδικας = "κωδ:ΑΚ").
