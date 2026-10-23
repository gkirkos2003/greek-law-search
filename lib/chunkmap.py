"""chunk id -> doc id -> source, as small numpy arrays (no chunk rows read at query time).

The data disk is a spinning disk: fetching a chunk row (text blob inside) is a random seek, so asking
SQLite for the source of 400 hybrid candidates took ~10 s. These maps are built from the covering
indexes chunks_doc(doc) and docs_source(source), which are small, and extended incrementally
(chunk ids are never reused; new chunks always get higher ids).

  index/chunk_doc.npy   int32 [max_chunk_id+1]  doc id, -1 = no chunk
  index/doc_src.npy     int8  [max_doc_id+1]    index into SOURCES, -1 = unknown
  index/doc_year.npy    int16 [max_doc_id+1]    docs.year, 0 = unknown
"""
import os

import numpy as np

from .index import ROOT

SOURCES = ["fek_a", "fek_b", "nsk", "areios_pagos", "elsyn", "ste", "eisap", "coa"]
CODE = {s: i for i, s in enumerate(SOURCES)}
DIR = os.path.join(ROOT, "index")
P_CHUNK = os.path.join(DIR, "chunk_doc.npy")
P_DOC = os.path.join(DIR, "doc_src.npy")
P_YEAR = os.path.join(DIR, "doc_year.npy")


def _save(path, arr):
    tmp = path[:-4] + f".tmp{os.getpid()}.npy"
    np.save(tmp, arr)
    os.replace(tmp, path)


def _load(path):
    try:
        return np.load(path)
    except Exception:
        return None


def build(con, full=False, log=None):
    """Create or extend both maps. full=True rescans everything (after deletions / re-parses)."""
    max_cid = con.execute("SELECT COALESCE(MAX(id), 0) FROM chunks").fetchone()[0]
    max_did = con.execute("SELECT COALESCE(MAX(id), 0) FROM docs").fetchone()[0]
    cd = None if full else _load(P_CHUNK)
    ds = None if full else _load(P_DOC)
    dy = None if full else _load(P_YEAR)
    if dy is None or ds is None or len(dy) != len(ds):
        ds = None
    changed = False
    if cd is None or len(cd) <= max_cid:
        start = 0 if cd is None else len(cd)
        new = np.full(max_cid + 1, -1, np.int32)
        if cd is not None:
            new[:len(cd)] = cd
        # first build: one sequential pass over the small covering index; extension: rowid range
        # (only the new rows; the index has no rowid order, so it would be scanned whole again)
        sql = ("SELECT id, doc FROM chunks INDEXED BY chunks_doc" if start == 0 else
               "SELECT id, doc FROM chunks WHERE id >= ?")
        for cid, doc in con.execute(sql, () if start == 0 else (start,)):
            new[cid] = doc
        cd, changed = new, True
    if ds is None or len(ds) <= max_did:
        new = np.full(max_did + 1, -1, np.int8)
        yr = np.zeros(max_did + 1, np.int16)
        for did, src, year in con.execute("SELECT id, source, year FROM docs"):
            new[did] = CODE.get(src, -1)
            yr[did] = int(year or 0)
        ds, dy, changed = new, yr, True
    con.commit()
    if changed:
        _save(P_CHUNK, cd)
        _save(P_DOC, ds)
        _save(P_YEAR, dy)
        if log:
            log(f"chunkmap: {int((cd >= 0).sum())} chunks, {int((ds >= 0).sum())} docs")
    return cd, ds, dy


class ChunkMap:
    def __init__(self, con):
        self.cd, self.ds, self.dy = build(con)
        self._flt = None

    def set_filter(self, sources=None, year_from=None, year_to=None):
        """Allowed docs as a bool array (None = no filter). Used by the SQL function _ok(rowid)."""
        if not sources and not year_from and not year_to:
            self._flt = None
            return
        ok = np.ones(len(self.ds), bool)
        if sources:
            ok &= np.isin(self.ds, [CODE[s] for s in sources if s in CODE])
        if year_from:
            ok &= self.dy >= int(year_from)
        if year_to:
            ok &= (self.dy <= int(year_to)) & (self.dy > 0)
        self._flt = ok

    def ok(self, cid):
        if self._flt is None:
            return 1
        d = self.doc(cid)
        return 1 if 0 <= d < len(self._flt) and self._flt[d] else 0

    def doc(self, cid):
        return int(self.cd[cid]) if 0 <= cid < len(self.cd) else -1

    def source(self, cid):
        d = self.doc(cid)
        if d < 0 or d >= len(self.ds) or self.ds[d] < 0:
            return None
        return SOURCES[self.ds[d]]
