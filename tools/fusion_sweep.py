"""Fusion sweep on eval/questions.jsonl: build hybrid candidates once per question, then score
many fusion settings offline. Writes eval/sweep_<tag>.json.
  venv/bin/python tools/fusion_sweep.py [--tag x] [--only q01,q02] [--ondemand 200]"""
import argparse, json, os, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "eval"))
os.environ.setdefault("OMP_NUM_THREADS", "2")
from lib import searcher as S
from run_eval import judge

ap = argparse.ArgumentParser()
ap.add_argument("--tag", default="x"); ap.add_argument("--only"); ap.add_argument("--ondemand", type=int, default=200)
ap.add_argument("--threads", type=int, default=2); ap.add_argument("--qfile", default="questions.jsonl")
ap.add_argument("--pens", default="", help="fek_b penalties to sweep at mm wd 0.5, e.g. 0,0.05,0.1")
a = ap.parse_args()
qs = [json.loads(l) for f in a.qfile.split(",") for l in open(os.path.join(ROOT, "eval", f), encoding="utf-8")]
if a.only:
    qs = [q for q in qs if q["id"] in a.only.split(",")]
s = S.Searcher(dense=True, threads=a.threads)
cache = []
for q in qs:
    t0 = time.time()
    bm = s.bm25(q["question"], k=200)
    cits = s.query_citations(q["question"]); s.exact_cit = set()
    cit_ids = s.citation_hits(cits) if cits else []
    feats = s.candidates(q["question"], bm, cit_ids, 200, a.ondemand)
    ids = list(feats)
    meta = {}
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        for cid, did, lk, art in s.con.execute(
                "SELECT c.id, d.doc_id, c.law_key, c.article FROM chunks c JOIN docs d ON d.id=c.doc "
                f"WHERE c.id IN ({','.join('?' * len(part))})", part):
            meta[cid] = (did, lk, art)
    pen1 = s.source_penalty(feats, {"fek_b": 1.0}) or {}
    cache.append((q, feats, meta, [c for c, _ in bm], cit_ids, pen1))
    print(q["id"], len(feats), f"{time.time()-t0:.1f}s", flush=True)

def ranked_hits(order, meta, k=5):
    out, per = [], {}
    for c in order:
        if c not in meta:
            continue
        did, lk, art = meta[c]
        if per.get(did, 0) >= 2:
            continue
        per[did] = per.get(did, 0) + 1
        out.append({"doc_id": did, "law_key": lk, "article": art})
        if len(out) >= k:
            break
    return out

def evaluate(fn):
    res = {"el": [], "en": [], "fekb": []}
    per_q = {}
    for q, feats, meta, bmo, cit, pen1 in cache:
        r, _ = judge(ranked_hits(fn(feats, bmo, cit, pen1), meta), q["gold"], 5)
        res[q["lang"]].append(1 if r else 0); per_q[q["id"]] = r
        if q["gold"][0]["source"] == "fek_b":
            res["fekb"].append(1 if r else 0)
    return {l: (round(sum(v) / len(v), 3) if v else None) for l, v in res.items()}, per_q

def _bm25_order(f, b, c, p=None):
    if not c:
        return b
    ex = [x for x in c if f.get(x, (0, 0, 0))[2] == 2]
    return ex + [x for x in S._rrf([("bm25", b, 1.0), ("cite", c, 1.5)]) if x not in set(ex)]
configs = {"bm25": _bm25_order}
for norm in (False, True):
    for wd in (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 1.0):
        configs[f"{'mm' if norm else 'raw'}_wd{wd}"] = (lambda wd, norm: lambda f, b, c, p: S.fuse(f, wd, cos_norm=norm))(wd, norm)
for pv in [float(x) for x in a.pens.split(",") if x]:
    configs[f"mm_wd0.5_bpen{pv}"] = (lambda pv: lambda f, b, c, p: S.fuse(f, 0.5, pen={k: v * pv for k, v in p.items()}))(pv)
out = {}
for name, fn in configs.items():
    agg, per = evaluate(fn)
    out[name] = {"agg": agg, "per_q": per}
    print(f"{name:18s} el={agg['el']} en={agg['en']} fekb={agg['fekb']}")
json.dump(out, open(os.path.join(ROOT, f"eval/sweep_{a.tag}.json"), "w"), indent=1)
