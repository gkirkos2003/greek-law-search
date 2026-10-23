"""Dense vectors for chunks: incremental embedding into float16 shards + lookup/search.

  index/vec/shard_00001.npy       float16 [n, 384]   (e5-small, L2-normalised)
  index/vec/shard_00001.ids.npy   int64   [n]        chunk ids (chunks.id, never reused)
Chunks deleted/re-chunked later simply leave dead rows (their ids no longer exist in chunks).
"""
import glob
import os
import signal
import time

import numpy as np

from .index import ROOT, connect, unpack

VEC_DIR = os.path.join(ROOT, "index", "vec")
SHARD_ROWS = 25000
# what gets embedded first (whole corpus ≈ 2 days on 3 threads)
PRIORITY = ["fek_a", "nsk", "ste", "eisap", "coa", "areios_pagos", "elsyn", "fek_b"]

_EMB = {}


def embedder(threads=3):
    if threads not in _EMB:
        from .embed import Embedder
        _EMB[threads] = Embedder(threads=threads)
    return _EMB[threads]


def _shards():
    return sorted(p for p in glob.glob(os.path.join(VEC_DIR, "shard_*.npy")) if not p.endswith(".ids.npy")
                  and ".tmp" not in p)


def embedded_ids():
    ids = [np.load(p[:-4] + ".ids.npy") for p in _shards() if os.path.exists(p[:-4] + ".ids.npy")]
    return np.concatenate(ids) if ids else np.zeros(0, np.int64)


def _save(vecs, ids):
    from .embed import save_shard
    os.makedirs(VEC_DIR, exist_ok=True)
    n = len(_shards()) + 1
    while os.path.exists(os.path.join(VEC_DIR, f"shard_{n:05d}.npy")):
        n += 1
    base = os.path.join(VEC_DIR, f"shard_{n:05d}")
    np.save(base + ".ids.tmp.npy", np.asarray(ids, np.int64))
    os.replace(base + ".ids.tmp.npy", base + ".ids.npy")
    save_shard(base + ".npy", np.vstack(vecs))      # vectors last: a shard counts once its .npy exists
    return base


def _ordered_todo(con, sources, done, log):
    """Chunk ids to embed, best value first:
       tier 0 = first passage of every document (decision summaries, ΦΕΚ issue openings),
       tier 1 = first passage of every ΦΕΚ article, tier 2 = everything else.
       Inside a tier: PRIORITY source order, newest year first."""
    srcs = [s for s in PRIORITY if not sources or s in sources]
    pri = {s: i for i, s in enumerate(srcs)}
    # Read in pages of docs and end the read transaction after each page: one long read over the
    # whole DB (rollback journal on NTFS) stalls a writer's commit, and the stalled writer then
    # locks out every new reader (search.py) until the read finishes.
    q = ("SELECT c.id, c.doc, c.article, d.source, d.year FROM chunks c JOIN docs d ON d.id=c.doc "
         "WHERE d.source IN (%s) AND c.doc >= ? AND c.doc < ? ORDER BY c.doc, c.seq" % ",".join("?" * len(srcs)))
    max_doc = con.execute("SELECT COALESCE(MAX(id), 0) FROM docs").fetchone()[0]
    con.commit()
    keys = []
    last_doc, seen_art = None, set()
    for lo in range(0, max_doc + 1, 5000):
        for cid, doc, art, src, year in con.execute(q, srcs + [lo, lo + 5000]):
            if doc != last_doc:
                last_doc, seen_art = doc, set()
                tier = 0
            elif art is not None and art not in seen_art:
                tier = 1
            else:
                tier = 2
            if art is not None:
                seen_art.add(art)
            if cid not in done:
                keys.append((tier, pri[src], -(year or 0), cid))
        con.commit()
    keys.sort()
    import collections
    log("  tiers: " + str(dict(collections.Counter(k[0] for k in keys))))
    return np.array([k[3] for k in keys], dtype=np.int64)


def embed_pending(threads=3, sources=None, max_chunks=None, log=print, batch=256):
    from .embed import embed_text
    done = set(embedded_ids().tolist())
    con = connect()
    con.execute("PRAGMA busy_timeout=900000")
    stop = {"flag": False}

    def _term(*_):
        stop["flag"] = True
    old = signal.signal(signal.SIGTERM, _term)
    emb = embedder(threads)
    total = 0
    t0 = time.time()
    buf_v, buf_i = [], []
    try:
        todo = _ordered_todo(con, sources, done, log)
        log(f"embed: {len(todo)} chunks pending (tiered order)")
        for s in range(0, len(todo), batch):
            part = [int(x) for x in todo[s:s + batch]]
            q = ",".join("?" * len(part))
            rows = con.execute(
                f"SELECT c.id, c.locator, c.text, d.title FROM chunks c JOIN docs d ON d.id=c.doc "
                f"WHERE c.id IN ({q})", part).fetchall()
            con.commit()
            if not rows:
                continue
            texts = [embed_text(loc, unpack(blob), title) for _, loc, blob, title in rows]
            vecs = emb.encode(texts, kind="passage")
            buf_v.append(vecs.astype(np.float16))
            buf_i += [r[0] for r in rows]
            total += len(rows)
            if len(buf_i) >= SHARD_ROWS:
                _save(buf_v, buf_i)
                buf_v, buf_i = [], []
                el = time.time() - t0
                log(f"  embedded {total} ({total/el:.1f}/s) — {s+len(part)}/{len(todo)}")
            if stop["flag"] or (max_chunks and total >= max_chunks):
                break
    finally:
        if buf_i:
            _save(buf_v, buf_i)
        signal.signal(signal.SIGTERM, old)
    el = time.time() - t0
    log(f"embed done: {total} chunks in {el/60:.1f} min" + (" (stopped)" if stop["flag"] else ""))
    return total


class DenseIndex:
    """All shards, memory-mapped. search() = full brute-force; vectors() = lookup by chunk id."""

    def __init__(self, threads=3):
        from .embed import load_shards, DenseIndex as _DI
        paths = [p for p in _shards() if os.path.exists(p[:-4] + ".ids.npy")]
        self.size = 0
        self.threads = threads
        self._qcache = {}
        if not paths:
            return
        self.mats = load_shards(paths)
        self.ids = np.concatenate([np.load(p[:-4] + ".ids.npy") for p in paths])
        self.order = np.argsort(self.ids, kind="stable")
        self.sorted_ids = self.ids[self.order]
        self.offs = np.cumsum([0] + [m.shape[0] for m in self.mats])
        self.size = int(self.offs[-1])
        self._di = _DI(self.mats, threads=threads)
        self.threads = threads
        self._qcache = {}

    def encode_query(self, q):
        if q not in self._qcache:
            self._qcache[q] = embedder(self.threads).encode([q], kind="query")[0]
        return self._qcache[q]

    def search(self, q, k=200):
        """-> [(chunk_id, cosine)] best first."""
        rows, sc = self._di.search(self.encode_query(q), k)
        return [(int(self.ids[r]), float(s)) for r, s in zip(rows, sc)]

    def _row(self, cid):
        j = np.searchsorted(self.sorted_ids, cid)
        if j < len(self.sorted_ids) and self.sorted_ids[j] == cid:
            return int(self.order[j])
        return None

    def vector(self, cid):
        r = self._row(cid)
        if r is None:
            return None
        si = int(np.searchsorted(self.offs, r, side="right") - 1)
        return np.asarray(self.mats[si][r - self.offs[si]], dtype=np.float32)


def encode_passages(rows, threads=3):
    """On-the-fly vectors for chunks not embedded yet. rows: [(locator, text, title)]."""
    from .embed import embed_text
    return embedder(threads).encode([embed_text(l, t, ti) for l, t, ti in rows], kind="passage")
