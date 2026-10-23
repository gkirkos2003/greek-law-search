"""SQLite store: docs + chunks + FTS5 (BM25) + cross-references. Incremental.

DB: search/index/law.db
  docs    one row per document (ΦΕΚ issue, decision, opinion); fingerprint drives incremental
  chunks  passages (zlib text) with locator/law/article; AUTOINCREMENT ids never reused, so
          vectors (search/index/vec/) can be keyed by chunk id and stale ones simply vanish
  fts     contentless FTS5 over textnorm.fts_text(): columns head (citation/title/locator), body
  xrefs   citations found in chunks (lib.xref), with relation amends/replaces/repeals/adds/cites
"""
import json
import os
import re
import sqlite3
import time
import zlib

from . import textnorm

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "index", "law.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id TEXT UNIQUE NOT NULL, source TEXT, citation TEXT, title TEXT, date TEXT,
  year INTEGER, number TEXT, court TEXT, path TEXT, ocr_conf REAL, meta TEXT,
  fingerprint TEXT, n_chunks INTEGER, xref_ver INTEGER DEFAULT 0, indexed_at REAL);
CREATE INDEX IF NOT EXISTS docs_source ON docs(source);
CREATE TABLE IF NOT EXISTS chunks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc INTEGER NOT NULL, seq INTEGER, locator TEXT, law TEXT, law_key TEXT, article TEXT,
  nchars INTEGER, text BLOB);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(doc);
CREATE INDEX IF NOT EXISTS chunks_lawkey ON chunks(law_key, article);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
  head, body, content='', contentless_delete=1,
  tokenize="unicode61 remove_diacritics 0");
CREATE VIRTUAL TABLE IF NOT EXISTS fts_v USING fts5vocab(fts, row);
CREATE TABLE IF NOT EXISTS xrefs(
  chunk INTEGER, doc INTEGER, src_key TEXT, key TEXT, article TEXT, relation TEXT);
CREATE INDEX IF NOT EXISTS xrefs_key ON xrefs(key, article);
CREATE INDEX IF NOT EXISTS xrefs_doc ON xrefs(doc);
CREATE INDEX IF NOT EXISTS xrefs_src ON xrefs(src_key);
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
"""

# "ν. 3852/2010", "π.δ. 15/1975", "Ν.Δ. 276/1974", "α.ν. 1846/1951", "β.δ. ..."
_LAW_RE = re.compile(r"^\s*([^\d]+?)\s*(\d+)\s*/\s*(\d{4})")
_KIND = {"ν": "ν", "νομοσ": "ν", "πδ": "πδ", "νδ": "νδ", "αν": "αν", "βδ": "βδ",
         "πνπ": "πνπ", "νπδ": "νπδ", "κν": "κν", "δ": "δ", "ββδ": "βδ", "δν": "ν"}


_LABEL_CACHE = {}


def law_key_from_label(label):
    """'ν. 3852/2010' -> 'ν:3852/2010' (κ.ν. folded into ν. via xref.act_key)."""
    if not label:
        return None
    if label in _LABEL_CACHE:
        return _LABEL_CACHE[label]
    key = None
    try:
        from . import xref
        refs = xref.extract(label, include_self=True)
        if refs and refs[0].get("key"):
            key = xref.act_key(refs[0]["key"])
    except Exception:
        key = None
    if key is None:
        m = _LAW_RE.match(label)
        if m:
            kind = re.sub(r"[^\w]", "", textnorm.fold(m.group(1)))
            kind = _KIND.get(kind, kind)
            key = f"{kind}:{int(m.group(2))}/{m.group(3)}"
    if len(_LABEL_CACHE) < 200000:
        _LABEL_CACHE[label] = key
    return key


def connect(path=DB_PATH, readonly=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if readonly:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        con = sqlite3.connect(path)
        # the data disk is NTFS/fuseblk: rollback journal (no WAL shm mmap), big cache
        con.execute("PRAGMA journal_mode=TRUNCATE")
        con.execute("PRAGMA synchronous=NORMAL")
        con.executescript(SCHEMA)
    # writers wait up to 15 min for long readers (rollback journal on NTFS: a long SELECT blocks commit)
    con.execute("PRAGMA busy_timeout=%d" % (300000 if readonly else 900000))
    if not readonly:
        # keep a transaction's dirty pages in RAM until COMMIT: a cache spill takes the EXCLUSIVE
        # lock early and locks every reader out for the rest of the transaction
        con.execute("PRAGMA cache_spill=OFF")
        cols = {r[1] for r in con.execute("PRAGMA table_info(docs)")}
        if "norm_ver" not in cols:      # docs indexed before textnorm v2 count as v1
            con.execute("ALTER TABLE docs ADD COLUMN norm_ver INTEGER DEFAULT 1")
            con.commit()
    con.execute("PRAGMA cache_size=-262144")      # 256 MB
    con.execute("PRAGMA temp_store=MEMORY")
    return con


def pack(text):
    return zlib.compress(text.encode("utf-8"), 6)


def unpack(blob):
    return zlib.decompress(blob).decode("utf-8")


def _head(doc, ch):
    h = ch.get("head")
    if h:
        return h
    parts = [doc.get("citation") or "", ch.get("locator") or ""]
    title = (doc.get("title") or "")[:300]
    parts.append(title)
    return " · ".join(p for p in parts if p)


def delete_doc(con, rowid):
    ids = [r[0] for r in con.execute("SELECT id FROM chunks WHERE doc=?", (rowid,))]
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        q = ",".join("?" * len(part))
        con.execute(f"DELETE FROM fts WHERE rowid IN ({q})", part)
    con.execute("DELETE FROM chunks WHERE doc=?", (rowid,))
    con.execute("DELETE FROM xrefs WHERE doc=?", (rowid,))
    con.execute("DELETE FROM docs WHERE id=?", (rowid,))


def insert_doc(con, doc, chunks, fingerprint):
    meta = doc.get("meta")
    cur = con.execute(
        "INSERT INTO docs(doc_id,source,citation,title,date,year,number,court,path,ocr_conf,meta,"
        "fingerprint,n_chunks,xref_ver,indexed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)",
        (doc["doc_id"], doc.get("source"), doc.get("citation"), doc.get("title"), doc.get("date"),
         doc.get("year"), doc.get("number"), doc.get("court"), doc.get("path"), doc.get("ocr_conf"),
         json.dumps(meta, ensure_ascii=False) if meta else None, fingerprint, len(chunks),
         time.time()))
    rowid = cur.lastrowid
    con.execute("UPDATE docs SET norm_ver=? WHERE id=?", (textnorm.NORM_VERSION, rowid))
    for ch in chunks:
        text = ch.get("text") or ""
        if not text.strip():
            continue
        law = ch.get("law")
        c = con.execute(
            "INSERT INTO chunks(doc,seq,locator,law,law_key,article,nchars,text) VALUES(?,?,?,?,?,?,?,?)",
            (rowid, ch.get("seq"), ch.get("locator"), law,
             ch.get("law_key") or law_key_from_label(law), ch.get("article"), len(text), pack(text)))
        con.execute("INSERT INTO fts(rowid, head, body) VALUES(?,?,?)",
                    (c.lastrowid, textnorm.fts_text(_head(doc, ch)), textnorm.fts_text(text)))
    return rowid


def build(sources=None, limit=None, log=print, commit_every=100):
    """Incremental: (re)index units whose fingerprint changed; drop docs that disappeared."""
    import importlib
    mods = []
    for name in ("src_fek", "src_caselaw", "src_fekb"):
        try:
            mods.append(importlib.import_module("lib." + name))
        except Exception as e:
            log(f"  cannot import {name}: {type(e).__name__}: {e}")
    con = connect()
    known = {}
    for doc_id, rowid, fp, src in con.execute("SELECT doc_id,id,fingerprint,source FROM docs"):
        known[doc_id] = (rowid, fp, src)
    seen, iterated_ok = set(), set()
    stats = dict(new=0, changed=0, same=0, failed=0, chunks=0, deleted=0)
    t0 = time.time()
    n_since = 0
    for mod in mods:
        mod_sources = set()
        ok = True
        try:
            for u in mod.iter_units(sources):
                mod_sources.add(u.source)
                seen.add(u.doc_id)
                old = known.get(u.doc_id)
                if old and old[1] == u.fingerprint:
                    stats["same"] += 1
                    continue
                try:
                    doc, chunks = u.load()
                except Exception as e:
                    stats["failed"] += 1
                    log(f"  load fail {u.doc_id}: {type(e).__name__}: {e}"[:300])
                    continue
                if doc is None:
                    continue
                doc.setdefault("doc_id", u.doc_id)
                doc.setdefault("source", u.source)
                if old:
                    delete_doc(con, old[0])
                    stats["changed"] += 1
                else:
                    stats["new"] += 1
                insert_doc(con, doc, chunks, u.fingerprint)
                stats["chunks"] += len(chunks)
                n_since += 1
                if n_since >= commit_every:
                    con.commit()
                    n_since = 0
                    el = time.time() - t0
                    log(f"  [{el/60:.1f} min] {stats}")
                if limit and stats["new"] + stats["changed"] >= limit:
                    ok = False      # partial pass: never delete on a partial pass
                    break
        except Exception as e:
            ok = False
            log(f"  iter_units failed in {mod.__name__}: {type(e).__name__}: {e}")
        if ok:
            iterated_ok |= mod_sources
        if limit and stats["new"] + stats["changed"] >= limit:
            break
    # docs that vanished from a fully-iterated source
    for doc_id, (rowid, fp, src) in known.items():
        if src in iterated_ok and doc_id not in seen:
            delete_doc(con, rowid)
            stats["deleted"] += 1
    con.commit()
    con.execute("INSERT OR REPLACE INTO kv VALUES('last_build', ?)", (time.strftime("%Y-%m-%d %H:%M:%S"),))
    con.commit()
    log(f"build done in {(time.time()-t0)/60:.1f} min: {stats}")
    return stats


def refts(log=print, sources=None, commit_every=300):
    """Re-tokenise the FTS rows of docs indexed with an older textnorm (chunk ids are kept,
    so stored vectors stay valid). Chunks with a source-specific head (ΦΕΚ Β΄) are rebuilt
    by `text` instead, because the head is not stored."""
    con = connect()
    V = textnorm.NORM_VERSION
    q = "SELECT id, citation, title FROM docs WHERE norm_ver < ? AND source != 'fek_b'"
    args = [V]
    if sources:
        q += " AND source IN (%s)" % ",".join("?" * len(sources))
        args += list(sources)
    docs = con.execute(q, args).fetchall()
    log(f"refts: {len(docs)} docs to re-tokenise (textnorm v{V})")
    t0 = time.time()
    n = 0
    for i, (rowid, citation, title) in enumerate(docs, 1):
        doc = {"citation": citation, "title": title}
        rows = con.execute("SELECT id, locator, text FROM chunks WHERE doc=?", (rowid,)).fetchall()
        for cid, loc, blob in rows:
            con.execute("DELETE FROM fts WHERE rowid=?", (cid,))
            con.execute("INSERT INTO fts(rowid, head, body) VALUES(?,?,?)",
                        (cid, textnorm.fts_text(_head(doc, {"locator": loc})),
                         textnorm.fts_text(unpack(blob))))
        n += len(rows)
        con.execute("UPDATE docs SET norm_ver=? WHERE id=?", (V, rowid))
        if i % commit_every == 0:
            con.commit()
            if i % (commit_every * 10) == 0:
                log(f"  refts {i}/{len(docs)} docs, {n} chunks, {(time.time()-t0)/60:.1f} min")
    con.commit()
    log(f"refts done: {len(docs)} docs, {n} chunks in {(time.time()-t0)/60:.1f} min")


def build_xrefs(log=print, version=None, commit_every=300):
    """Run lib.xref over chunks of docs whose xref_ver is behind."""
    from . import xref
    ver = version or getattr(xref, "VERSION", 1)
    con = connect()
    docs = con.execute("SELECT id, source, year, meta FROM docs WHERE xref_ver < ?", (ver,)).fetchall()
    log(f"xref: {len(docs)} docs to scan (version {ver})")
    t0 = time.time()
    nref = 0
    for i, (rowid, source, year, meta) in enumerate(docs):
        con.execute("DELETE FROM xrefs WHERE doc=?", (rowid,))
        first_cid = None
        for cid, law_key, blob in con.execute(
                "SELECT id, law_key, text FROM chunks WHERE doc=? ORDER BY seq", (rowid,)).fetchall():
            first_cid = first_cid or cid
            try:
                refs = xref.extract(unpack(blob), self_law=law_key, doc_year=year)
            except Exception:
                continue
            rows = []
            for r in refs:
                k = r.get("key")
                if not k:
                    continue
                k = xref.act_key(k)
                if k == law_key:
                    continue
                rows.append((cid, rowid, law_key, k, r.get("article"), r.get("relation") or "cites"))
            if rows:
                con.executemany("INSERT INTO xrefs VALUES(?,?,?,?,?,?)", rows)
                nref += len(rows)
        # NSK metadata carries the cited provisions explicitly ("Ν 3463/2006Α146")
        if meta and first_cid:
            try:
                provs = (json.loads(meta) or {}).get("provisions") or []
            except Exception:
                provs = []
            if provs and isinstance(provs, list):
                try:
                    refs = xref.extract_nsk([p for p in provs if isinstance(p, str)], doc_year=year)
                except Exception:
                    refs = []
                rows = [(first_cid, rowid, None, xref.act_key(r["key"]), r.get("article"), "cites")
                        for r in refs if r.get("key")]
                if rows:
                    con.executemany("INSERT INTO xrefs VALUES(?,?,?,?,?,?)", rows)
                    nref += len(rows)
        con.execute("UPDATE docs SET xref_ver=? WHERE id=?", (ver, rowid))
        if (i + 1) % commit_every == 0:
            con.commit()
            el = time.time() - t0
            log(f"  xref {i+1}/{len(docs)} refs={nref} {el/60:.1f} min")
    con.commit()
    log(f"xref done: {nref} refs in {(time.time()-t0)/60:.1f} min")


def stats(con=None):
    con = con or connect(readonly=True)
    out = {}
    for src, n, ch in con.execute(
            "SELECT source, COUNT(*), SUM(n_chunks) FROM docs GROUP BY source ORDER BY source"):
        out[src] = {"docs": n, "chunks": ch}
    out["_total_chunks"] = con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    out["_xrefs"] = con.execute("SELECT COUNT(*) FROM xrefs").fetchone()[0]
    return out
