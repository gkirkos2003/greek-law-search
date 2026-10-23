"""xref quality + speed test.

Run:  cd search && nice -n 19 venv/bin/python tests/test_xref.py [-v] [--speed]
Gold: tests/xref_gold.jsonl (hand-labelled, sampled with a fixed seed by tests/make_xref_samples.py).
Metrics are set-based per snippet:
  keys      = {key}
  articles  = {(key, article)}            (article None = act cited without an article)
  relations = {(key, article, relation)}
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from lib import xref  # noqa: E402

GOLD = os.path.join(HERE, "xref_gold.jsonl")


def predict(r):
    if "provisions" in r:
        cs = xref.extract_nsk(r["provisions"], doc_year=r.get("year") if isinstance(r.get("year"), int) else None)
    else:
        y = r.get("year")
        try:
            y = int(y) if y else None
        except Exception:
            y = None
        cs = xref.extract(r["text"], self_law=r.get("self_law"), doc_year=y)
    return cs


def prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 1.0
    rc = tp / (tp + fn) if tp + fn else 1.0
    f = 2 * p * rc / (p + rc) if p + rc else 0.0
    return p, rc, f


def evaluate(verbose=False, split=None):
    tot = {k: [0, 0, 0] for k in ("keys", "articles", "relations")}
    rows = [json.loads(l) for l in open(GOLD, encoding="utf-8")]
    if split:
        rows = [r for r in rows if r.get("split") == split]
    for r in rows:
        gold = [tuple(x) for x in r["labels"]]
        pred = predict(r)
        sets = {
            "keys": ({g[0] for g in gold}, {c["key"] for c in pred}),
            "articles": ({(g[0], g[1]) for g in gold}, {(c["key"], c["article"]) for c in pred}),
            "relations": ({tuple(g) for g in gold}, {(c["key"], c["article"], c["relation"]) for c in pred}),
        }
        for k, (gs, ps) in sets.items():
            tot[k][0] += len(gs & ps)
            tot[k][1] += len(ps - gs)
            tot[k][2] += len(gs - ps)
        if verbose:
            gs, ps = sets["relations"]
            if gs != ps:
                print("--- #%s %s" % (r["id"], r["src"]))
                for x in sorted(ps - gs, key=str):
                    print("   FP", x)
                for x in sorted(gs - ps, key=str):
                    print("   FN", x)
    res = {}
    for k, (tp, fp, fn) in tot.items():
        p, rc, f = prf(tp, fp, fn)
        res[k] = {"P": round(p, 3), "R": round(rc, 3), "F1": round(f, 3), "tp": tp, "fp": fp, "fn": fn}
    res["n_snippets"] = len(rows)
    res["n_labels"] = sum(len(r["labels"]) for r in rows)
    return res


def speed_sample(n=3000, size=1000):
    """~1 kB chunks from real text: Ελ.Συν. decisions + ΦΕΚ text 2010 + OCR 1965 (read-only)."""
    root = os.path.dirname(os.path.dirname(HERE))
    chunks = []
    p = os.path.join(root, "caselaw", "elsyn-tnp", "decisions.jsonl")
    with open(p, encoding="utf-8") as f:
        for line in f:
            t = json.loads(line).get("text") or ""
            for i in range(0, len(t) - size, size):
                chunks.append(t[i:i + size])
            if len(chunks) >= n // 2:
                break
    for sub in ("2010", "1965"):
        d = os.path.join(root, "text", sub)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d))[:40]:
            with open(os.path.join(d, fn), encoding="utf-8", errors="replace") as f:
                t = f.read()
            for i in range(0, len(t) - size, size):
                chunks.append(t[i:i + size])
            if len(chunks) >= n:
                break
    return chunks[:n]


def speed(chunks):
    t0 = time.perf_counter()
    nc = 0
    for c in chunks:
        nc += len(xref.extract(c))
    dt = time.perf_counter() - t0
    return {"chunks": len(chunks), "seconds": round(dt, 2), "chunks_per_s": round(len(chunks) / dt), "citations": nc}


def test_candidate_scan_equivalent():
    """extract() scans literal prefixes (_CAND) instead of ANCHOR.finditer; both must agree."""
    bad = 0
    for r in (json.loads(l) for l in open(GOLD, encoding="utf-8")):
        for t in ([r["text"]] if "text" in r else r["provisions"]):
            ft = xref.fold(t)
            a = [m.span() for m in xref.ANCHOR.finditer(ft)]
            b, skip = [], 0
            for cm in xref._CAND.finditer(ft):
                p = cm.start()
                if p < skip or (p and ft[p - 1].isalnum()):
                    continue
                m = xref.ANCHOR.match(ft, p)
                if m:
                    b.append(m.span())
                    skip = m.end()
            bad += a != b
    assert bad == 0, bad


def test_quality():
    res = evaluate()
    assert res["keys"]["P"] >= 0.95 and res["keys"]["R"] >= 0.90, res


def _short(res):
    return " | ".join("%s P=%.3f R=%.3f F1=%.3f (tp %d fp %d fn %d)" % (k, v["P"], v["R"], v["F1"], v["tp"], v["fp"], v["fn"])
                      for k, v in res.items() if isinstance(v, dict))


if __name__ == "__main__":
    split = None
    for a in sys.argv:
        if a.startswith("--split="):
            split = a.split("=", 1)[1]
    if split:
        res = evaluate(verbose="-v" in sys.argv, split=split)
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        for sp in ("dev", "holdout", None):
            res = evaluate(verbose="-v" in sys.argv and sp == "holdout", split=sp)
            print("%-8s n=%d labels=%d  %s" % (sp or "ALL", res["n_snippets"], res["n_labels"], _short(res)))
    test_candidate_scan_equivalent()
    if "--speed" in sys.argv:
        print(json.dumps(speed(speed_sample()), indent=1))
