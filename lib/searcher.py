"""Query engine: BM25 (FTS5) + dense vectors, fused by min-max scaled score blending,
plus exact-citation boosts and cross-reference lookups."""
import json
import math
import os
import re
import sqlite3
import time

from . import textnorm
from .index import DB_PATH, connect, unpack

try:
    from . import glossary
except Exception:      # optional English->Greek legal glossary
    glossary = None

RRF_K = 60
# hybrid weights of the (min-max scaled) cosine, see eval/README_eval.md "fusion sweep"
W_DENSE_EL = 0.5
W_DENSE_EN = 0.5
CITE_BONUS = 0.15     # chunk belongs to a law named in the query
EXACT_BONUS = 1.0     # chunk IS the article named in the query -> always on top
# hybrid score penalty per source when the user did not pick sources (--source); see README eval
SOURCE_PENALTY = {"fek_b": float(os.environ.get("GL_FEKB_PEN", "0.2"))}
EN_BM25 = os.environ.get("GL_EN_BM25", "greek")   # greek | all


def _xref():
    try:
        from . import xref
        return xref
    except Exception:
        return None


class Searcher:
    def __init__(self, db=DB_PATH, dense=True, threads=3):
        self.con = connect(db, readonly=True)
        self.n_chunks = self.con.execute("SELECT MAX(id) FROM chunks").fetchone()[0] or 1
        self._df_cache = {}
        self.dense = None
        if dense:
            try:
                from .dense import DenseIndex
                self.dense = DenseIndex(threads=threads)   # size may be 0: on-the-fly vectors only
            except Exception as e:      # vectors not built yet
                self.dense_error = f"{type(e).__name__}: {e}"
        self.xref = _xref()
        self._cmap = None
        try:
            self.con.execute("CREATE TEMP TABLE IF NOT EXISTS _x(a)")   # probe
        except sqlite3.OperationalError:
            pass

    @property
    def cmap(self):
        """chunk -> doc -> source map (lib/chunkmap.py); None if it can't be built."""
        if self._cmap is None:
            try:
                from .chunkmap import ChunkMap
                self._cmap = ChunkMap(self.con)
                self.con.create_function("_ok", 1, self._cmap.ok, deterministic=True)
            except Exception as e:
                self._cmap = False
                self.cmap_error = f"{type(e).__name__}: {e}"
        return self._cmap or None

    def _pick(self, fused, k, per_doc=2, spare=5):
        """First k ids keeping <= per_doc chunks per document (doc from the chunk map), plus a few
        spares for ids whose rows vanished. Only these rows are then read from disk."""
        cm = self.cmap
        if cm is None:
            return fused[: k * 4]
        out, seen = [], {}
        for c in fused:
            d = cm.doc(c)
            if d >= 0:
                if seen.get(d, 0) >= per_doc:
                    continue
                seen[d] = seen.get(d, 0) + 1
            out.append(c)
            if len(out) >= k + spare:
                break
        return out

    # ---------------------------------------------------------------- BM25
    def df(self, term):
        if term not in self._df_cache:
            try:
                r = self.con.execute("SELECT doc FROM fts_v WHERE term=?", (term,)).fetchone()
            except sqlite3.OperationalError:
                r = None
            self._df_cache[term] = r[0] if r else 0
        return self._df_cache[term]

    def query_terms(self, q):
        terms = textnorm.fts_query_terms(q)
        if glossary and _looks_english(q):
            gr = textnorm.fts_query_terms(glossary.expand(q))
            if gr and EN_BM25 == "greek":
                # English words only match English treaty text in ΦΕΚ: search the Greek glossary
                # terms + numbers/abbreviations of the question instead
                terms = gr + [t for t in terms if any(ch.isdigit() for ch in t)]
            else:
                terms = terms + gr
        # dedupe, keep order
        seen, out = set(), []
        for t in terms:
            if t not in seen:
                seen.add(t)
                out.append(t)
        return out

    def bm25(self, q, k=200, where="", params=()):
        terms = self.query_terms(q)
        if not terms:
            return []
        # very common terms make OR queries score millions of rows: keep the rarer ones
        dfs = {t: self.df(t) for t in terms}
        cap = max(20000, int(self.n_chunks * 0.08))
        keep = [t for t in terms if 0 < dfs[t] <= cap]
        if not keep:
            keep = sorted((t for t in terms if dfs[t] > 0), key=lambda t: dfs[t])[:3]
        if not keep:
            return []
        mem = bool(where) and self.cmap is not None and self.cmap._flt is not None

        def run(match, lim):
            if mem:     # filter in memory (chunk map): no chunk/doc row reads per FTS match
                sql = ("SELECT rowid, bm25(fts, 3.0, 1.0) AS s FROM fts WHERE fts MATCH ? AND _ok(rowid) "
                       "ORDER BY s LIMIT ?")
                return self.con.execute(sql, (match, lim)).fetchall()
            sql = ("SELECT fts.rowid, bm25(fts, 3.0, 1.0) AS s FROM fts "
                   + (" JOIN chunks c ON c.id=fts.rowid JOIN docs d ON d.id=c.doc " if where else "")
                   + " WHERE fts MATCH ? " + (f" AND {where}" if where else "")
                   + " ORDER BY s LIMIT ?")
            return self.con.execute(sql, (match, *params, lim)).fetchall()

        quoted = ['"%s"' % t.replace('"', "") for t in keep]
        rows = run(" OR ".join(quoted), k)
        if len(keep) > 1:
            # chunks containing ALL terms (bm25 of a row is the same for AND/OR queries)
            rows += run(" AND ".join(quoted), k)
            if len(keep) > 3:   # and the rarest three together
                rare = sorted(keep, key=lambda t: dfs[t])[:3]
                rows += run(" AND ".join('"%s"' % t for t in rare), k)
        best = {}
        for cid, sc in rows:
            best[cid] = -sc
        if len(keep) > 1 and best:
            # coordination: reward chunks that match more of the query terms
            ids = list(best)
            qm = ",".join("?" * len(ids))
            hits = dict.fromkeys(ids, 0)
            for t in keep:
                for (cid,) in self.con.execute(
                        f"SELECT rowid FROM fts WHERE fts MATCH ? AND rowid IN ({qm})", (f'"{t}"', *ids)):
                    hits[cid] += 1
            n = len(keep)
            for cid in ids:
                best[cid] *= 0.4 + 0.6 * (hits[cid] / n)
        out = sorted(best.items(), key=lambda kv: -kv[1])
        return out[:k]

    # ---------------------------------------------------------------- citations in the query
    def query_citations(self, q):
        x = self.xref
        if not x:
            return []
        try:
            out = []
            for r in x.extract(q):
                if r.get("key"):
                    r["key"] = x.act_key(r["key"])
                    out.append(r)
            return out
        except Exception:
            return []

    def citation_hits(self, cits, k=50):
        """Chunks that ARE the cited law/article (legislation). Chunks of an explicitly cited
        article ("άρθρο 5 του ν. 4427/2016") are remembered in self.exact_cit: hybrid ranks them first."""
        out, self.exact_cit = [], set()
        for c in cits:
            key, art = c["key"], c.get("article")
            if art:
                rows = self.con.execute(
                    "SELECT id FROM chunks WHERE law_key=? AND article=? ORDER BY seq LIMIT ?",
                    (key, str(art), k)).fetchall()
                self.exact_cit.update(r[0] for r in rows)
            else:
                rows = self.con.execute(
                    "SELECT id FROM chunks WHERE law_key=? ORDER BY seq LIMIT ?", (key, k)).fetchall()
            out += [r[0] for r in rows]
        return out

    # ---------------------------------------------------------------- main entry
    def search(self, q, k=10, mode="hybrid", sources=None, year_from=None, year_to=None,
               pool=200, ondemand=40, w_dense=None):
        """mode bm25: FTS5 BM25 (+ exact-citation hits fused by RRF).
        mode hybrid: candidates = BM25 top-`pool` ∪ dense top-`pool` ∪ citation hits, scored
        (1-w_d)·minmax(BM25) + w_d·minmax(cosine) (+0.15 if the chunk IS a cited law/article).
        w_d = 0.5 for Greek and English (fusion sweep 10-21, eval/README_eval.md).
        Candidates without a stored vector get one computed on the fly (top `ondemand` by BM25)."""
        t0 = time.time()
        where, params = [], []
        if sources:
            where.append("d.source IN (%s)" % ",".join("?" * len(sources)))
            params += list(sources)
        if year_from:
            where.append("d.year >= ?"); params.append(int(year_from))
        if year_to:
            where.append("d.year <= ?"); params.append(int(year_to))
        wsql = " AND ".join(where)
        if self.cmap is not None:
            self.cmap.set_filter(sources, year_from, year_to)
        english = _looks_english(q)

        bm = self.bm25(q, k=pool, where=wsql, params=tuple(params))
        cits = self.query_citations(q)
        self.exact_cit = set()
        cit_ids = self.citation_hits(cits) if cits else []
        used = "bm25"
        if mode == "bm25" or self.dense is None:
            fused = [cid for cid, _ in bm]
            if cit_ids:
                fused = _rrf([("bm25", fused, 1.0), ("cite", cit_ids, 1.5)])
                if self.exact_cit:      # a named article goes first (same rule as hybrid)
                    fused = [c for c in cit_ids if c in self.exact_cit] + [c for c in fused if c not in self.exact_cit]
        else:
            used = "hybrid"
            wd = w_dense if w_dense is not None else (W_DENSE_EN if english else W_DENSE_EL)
            feats = self.candidates(q, bm, cit_ids, pool, ondemand, wsql, params)
            pen = None if sources else self.source_penalty(feats)
            fused = fuse(feats, wd, pen=pen)
        hits = self._materialise(self._pick(fused, k), q)
        out, per_doc = [], {}
        for h in hits:
            n = per_doc.get(h["doc_id"], 0)
            if n >= 2:
                continue
            per_doc[h["doc_id"]] = n + 1
            out.append(h)
            if len(out) >= k:
                break
        return {"query": q, "mode": used,
                "citations_in_query": [c["key"] + (f" άρθρο {c['article']}" if c.get("article") else "")
                                       for c in cits],
                "vectors_stored": int(self.dense.size) if self.dense else 0,
                "ms": int((time.time() - t0) * 1000), "hits": out}

    def source_penalty(self, ids, table=None):
        """{chunk_id: penalty} for candidates from down-weighted sources (SOURCE_PENALTY)."""
        table = SOURCE_PENALTY if table is None else table
        srcs = [k for k, v in table.items() if v]
        if not srcs:
            return None
        ids, pen = list(ids), {}
        cm = self.cmap
        if cm is not None:
            for cid in ids:
                src = cm.source(cid)
                if src in table and table[src]:
                    pen[cid] = table[src]
            return pen
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            for cid, src in self.con.execute(
                    "SELECT c.id, d.source FROM chunks c JOIN docs d ON d.id=c.doc "
                    f"WHERE c.id IN ({','.join('?' * len(part))}) AND d.source IN ({','.join('?' * len(srcs))})",
                    part + srcs):
                pen[cid] = table[src]
        return pen

    def candidates(self, q, bm, cit_ids, pool=200, ondemand=40, wsql="", params=()):
        """Hybrid candidate set with raw features: {chunk_id: (bm25 or None, cosine or None, is_cited)}.
        Candidates = BM25 top-`pool` ∪ citation hits ∪ dense top-`pool`; candidates without a stored
        vector get one computed on the fly (best `ondemand` by BM25 rank, citation hits first)."""
        qv = self.dense.encode_query(q)
        dense_hits = []
        if self.dense.size:
            dense_hits = self.dense.search(q, k=pool * 2)
            if wsql:
                keep = set(self._filter([c for c, _ in dense_hits], wsql, params))
                dense_hits = [h for h in dense_hits if h[0] in keep]
            dense_hits = dense_hits[:pool]
        bms = dict(bm)
        cand = list(dict.fromkeys([c for c, _ in bm] + cit_ids[:50] + [c for c, _ in dense_hits]))
        cos = {c: s for c, s in dense_hits}
        missing = []
        for c in cand:
            if c in cos:
                continue
            v = self.dense.vector(c) if self.dense.size else None
            if v is not None:
                cos[c] = float(v @ qv)
            else:
                missing.append(c)
        order = {c: i for i, c in enumerate([c for c, _ in bm])}
        cset = set(cit_ids)
        missing.sort(key=lambda c: -1 if c in cset else order.get(c, 10**6))
        todo = missing[:ondemand]
        if todo:
            from .dense import encode_passages
            qm = ",".join("?" * len(todo))
            rows = {r[0]: r[1:] for r in self.con.execute(
                f"SELECT c.id, c.locator, c.text, d.title FROM chunks c JOIN docs d ON d.id=c.doc "
                f"WHERE c.id IN ({qm})", todo)}
            ids = [c for c in todo if c in rows]
            if ids:
                V = encode_passages([(rows[c][0], unpack(rows[c][1]), rows[c][2]) for c in ids],
                                    threads=self.dense.threads)
                for c, v in zip(ids, V):
                    cos[c] = float(v @ qv)
        exact = getattr(self, "exact_cit", set())
        return {c: (bms.get(c), cos.get(c), 2 if c in exact else (1 if c in cset else 0)) for c in cand}

    def _filter(self, ids, wsql, params):
        if not ids:
            return ids
        cm = self.cmap
        if cm is not None and cm._flt is not None:     # in memory
            return [i for i in ids if cm.ok(i)]
        if cm is not None:      # docs rows only (small table), no chunk rows
            docs = sorted({cm.doc(i) for i in ids} - {-1})
            okd = set()
            for j in range(0, len(docs), 500):
                part = docs[j:j + 500]
                okd |= {r[0] for r in self.con.execute(
                    f"SELECT d.id FROM docs d WHERE d.id IN ({','.join('?' * len(part))}) AND {wsql}",
                    (*part, *params))}
            return [i for i in ids if cm.doc(i) in okd]
        q = ",".join("?" * len(ids))
        ok = {r[0] for r in self.con.execute(
            f"SELECT c.id FROM chunks c JOIN docs d ON d.id=c.doc WHERE c.id IN ({q}) AND {wsql}",
            (*ids, *params))}
        return [i for i in ids if i in ok]

    def _materialise(self, ids, q):
        if not ids:
            return []
        qm = ",".join("?" * len(ids))
        rows = self.con.execute(
            f"SELECT c.id, c.seq, c.locator, c.law, c.law_key, c.article, c.text, d.doc_id, d.source, "
            f"d.citation, d.title, d.date, d.year, d.court, d.path, d.ocr_conf "
            f"FROM chunks c JOIN docs d ON d.id=c.doc WHERE c.id IN ({qm})", ids).fetchall()
        by = {r[0]: r for r in rows}
        terms = set(textnorm.fts_query_terms(q))
        out = []
        for rank, cid in enumerate(ids):
            r = by.get(cid)
            if not r:
                continue
            text = unpack(r[6])
            out.append({
                "chunk_id": cid, "doc_id": r[7], "source": r[8], "citation": r[9],
                "locator": r[2], "law": r[3], "law_key": r[4], "article": r[5],
                "title": r[10], "date": r[11], "year": r[12], "court": r[13], "path": r[14],
                "ocr_conf": r[15], "text": text, "snippet": snippet(text, terms)})
        return out

    # ---------------------------------------------------------------- cross references
    def law_info(self, key, limit=30, article=None):
        """What amends / repeals / cites law `key` (optionally only its `article`), and what this law
        itself amends. cited_by_docs groups the citing passages per document."""
        con = self.con
        res = {"key": key, "article": article}
        x = self.xref
        if x and hasattr(x, "resolve_fek"):
            try:
                res["fek"] = x.resolve_fek(key)
            except Exception:
                res["fek"] = None
        rows = con.execute(
            "SELECT x.relation, x.article, x.src_key, d.citation, d.source, d.year, c.locator, d.doc_id "
            "FROM xrefs x JOIN chunks c ON c.id=x.chunk JOIN docs d ON d.id=x.doc "
            "WHERE x.key=? " + ("AND x.article=? " if article else "") + "ORDER BY d.year DESC, x.relation",
            (key, str(article)) if article else (key,)).fetchall()
        amended, cited = [], {}
        for rel, art, src_key, cit, src, year, loc, doc_id in rows:
            item = {"relation": rel, "article": art, "by_law": src_key, "by": cit, "locator": loc,
                    "source": src, "year": year, "doc_id": doc_id}
            if rel in ("amends", "replaces", "repeals", "adds") and src in ("fek_a", "fek_b"):
                amended.append(item)
            else:
                cited.setdefault(src, []).append(item)
        res["changed_by"] = _dedupe(amended)[:limit]
        res["cited_by_counts"] = {s: len(v) for s, v in cited.items()}
        res["cited_by"] = {s: _dedupe(v)[:limit] for s, v in cited.items()}
        docs = {}
        for s_, items in cited.items():
            g = {}
            for it in items:
                d = g.setdefault(it["doc_id"], {"by": it["by"], "doc_id": it["doc_id"], "year": it["year"],
                                                "articles": set(), "n": 0})
                d["n"] += 1
                if it.get("article"):
                    d["articles"].add(it["article"])
            lst = sorted(g.values(), key=lambda d: (-(d["year"] or 0), -d["n"]))
            for d in lst:
                d["articles"] = sorted(d["articles"], key=_artkey)
            docs[s_] = lst
        res["cited_by_doc_counts"] = {s_: len(v) for s_, v in docs.items()}
        res["cited_by_docs"] = {s_: v[:limit] for s_, v in docs.items()}
        own = con.execute(
            "SELECT x.relation, x.key, x.article, COUNT(*) FROM xrefs x WHERE x.src_key=? "
            "AND x.relation IN ('amends','replaces','repeals','adds') GROUP BY 1,2,3 "
            "ORDER BY 4 DESC LIMIT ?", (key, limit)).fetchall()
        res["this_law_changes"] = [{"relation": r, "key": k2, "article": a, "n": n} for r, k2, a, n in own]
        return res

    def chunk_refs(self, chunk_id):
        return [{"key": k, "article": a, "relation": r} for k, a, r in self.con.execute(
            "SELECT key, article, relation FROM xrefs WHERE chunk=?", (chunk_id,))]


def _artkey(a):
    m = re.match(r"(\d+)", a or "")
    return (int(m.group(1)) if m else 10**6, a or "")


def _dedupe(items):
    seen, out = set(), []
    for it in items:
        sig = (it["relation"], it.get("article"), it.get("by"), it.get("locator"))
        if sig in seen:
            continue
        seen.add(sig)
        out.append(it)
    return out


def fuse(feats, wd, cos_norm=True, pen=None):
    """score = (1-wd)·minmax(bm25) + wd·minmax(cos) + EXACT_BONUS (cited article) / CITE_BONUS (cited law)
    - pen[cid] (source penalty).
    Missing BM25 -> 0; missing cosine -> 25th percentile of the candidates' cosines."""
    bvals = [b for b, _, _ in feats.values() if b is not None]
    blo, bhi = (min(bvals), max(bvals)) if bvals else (0.0, 1.0)
    brng = (bhi - blo) or 1.0
    cvals = sorted(c for _, c, _ in feats.values() if c is not None)
    fill = cvals[len(cvals) // 4] if cvals else 0.0
    clo, chi = (fill, cvals[-1]) if (cvals and cos_norm) else (0.0, 1.0)
    crng = (chi - clo) or 1.0
    score = {}
    for cid, (b, c, cited) in feats.items():
        bn = (b - blo) / brng if b is not None else 0.0
        cn = max(0.0, ((c if c is not None else fill) - clo) / crng)
        score[cid] = (1 - wd) * bn + wd * cn + (EXACT_BONUS if cited == 2 else CITE_BONUS if cited else 0.0)
        if pen:
            score[cid] -= pen.get(cid, 0.0)
    return sorted(feats, key=lambda c: -score[c])


def _rrf(lists):
    score = {}
    for name, ids, w in lists:
        for rank, cid in enumerate(ids):
            score[cid] = score.get(cid, 0.0) + w / (RRF_K + rank + 1)
    return [cid for cid, _ in sorted(score.items(), key=lambda kv: -kv[1])]


_EN = re.compile(r"[A-Za-z]")
_GR = re.compile(r"[Ͱ-Ͽἀ-῿]")


def _looks_english(q):
    return len(_EN.findall(q)) > len(_GR.findall(q))


def snippet(text, terms, width=320):
    """Window of the passage with the most query-term hits."""
    if len(text) <= width:
        return text
    words = list(re.finditer(r"[^\W_]+", text))
    if not words or not terms:
        return text[:width] + "…"
    flags = [1 if textnorm.tokens(w.group(0))[:1] and textnorm.tokens(w.group(0))[0] in terms else 0
             for w in words]
    best, best_i, run = -1, 0, 0
    j = 0
    for i, w in enumerate(words):
        run += flags[i]
        while words[i].end() - words[j].start() > width:
            run -= flags[j]
            j += 1
        if run > best:
            best, best_i = run, j
    s = words[best_i].start()
    e = min(len(text), s + width)
    return ("…" if s > 0 else "") + text[s:e].strip() + ("…" if e < len(text) else "")
