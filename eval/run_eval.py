#!/usr/bin/env python3
"""hit@k / MRR for BM25 vs hybrid on eval/questions.jsonl.

  venv/bin/python eval/run_eval.py [--k 5] [--modes bm25,hybrid] [--ondemand 200]
Writes eval/results.json and eval/results.md.
doc hit   = a top-k passage comes from a gold document (ΦΕΚ issue / decision / opinion)
art hit   = (legislation only) a top-k passage is the gold law AND article
"""
import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault("OMP_NUM_THREADS", "3")

from lib.searcher import Searcher   # noqa: E402


def norm_id(doc_id):
    """'ste:Α4691/2015' == 'ste:4691/2015'; case/space-insensitive."""
    if not doc_id:
        return None
    src, _, rest = doc_id.partition(":")
    rest = re.sub(r"^[^\d]+", "", rest.replace(" ", ""))
    return f"{src.lower()}:{rest}"


def judge(hits, gold, k):
    gdocs = {norm_id(g.get("doc_id")) for g in gold if g.get("doc_id")}
    garts = {(g["law_key"], str(g["article"])) for g in gold if g.get("law_key") and g.get("article")}
    gstem = {"fekA:" + g["fek_stem"] for g in gold if g.get("fek_stem")}
    doc_rank = art_rank = None
    for i, h in enumerate(hits[:k], 1):
        did = norm_id(h["doc_id"])
        if doc_rank is None and (did in gdocs or h["doc_id"] in gstem):
            doc_rank = i
        if art_rank is None and garts and (h.get("law_key"), str(h.get("article"))) in garts:
            art_rank = i
    return doc_rank, art_rank


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--modes", default="bm25,hybrid")
    ap.add_argument("--ondemand", type=int, default=200)
    ap.add_argument("--threads", type=int, default=3)
    ap.add_argument("--only")
    ap.add_argument("--qfile", default="questions.jsonl")
    ap.add_argument("--out", default="results", help="writes eval/<out>.json + .md")
    a = ap.parse_args()
    qs = [json.loads(l) for l in open(os.path.join(HERE, a.qfile), encoding="utf-8")]
    if a.only:
        qs = [q for q in qs if q["id"] in a.only.split(",")]
    s = Searcher(dense=True, threads=a.threads)
    modes = a.modes.split(",")
    rows = []
    for q in qs:
        row = {"id": q["id"], "lang": q["lang"], "source": q["gold"][0]["source"],
               "has_citation": q.get("has_citation", False), "question": q["question"]}
        for m in modes:
            t0 = time.time()
            res = s.search(q["question"], k=max(a.k, 10), mode=m, ondemand=a.ondemand)
            dr, ar = judge(res["hits"], q["gold"], a.k)
            dr10, _ = judge(res["hits"], q["gold"], 10)
            row[m] = {"doc_rank": dr, "art_rank": ar, "doc_rank10": dr10, "s": round(time.time() - t0, 2),
                      "top": [f"{h['citation']} | {h.get('locator')}" for h in res["hits"][:5]]}
        rows.append(row)
        print(q["id"], q["lang"], row["source"], " ".join(
            f"{m}:doc={row[m]['doc_rank']} art={row[m]['art_rank']}" for m in modes), flush=True)

    def agg(sel, m):
        sel = list(sel)
        if not sel:
            return None
        n = len(sel)
        hit = sum(1 for r in sel if r[m]["doc_rank"]) / n
        mrr = sum(1 / r[m]["doc_rank"] for r in sel if r[m]["doc_rank"]) / n
        hit10 = sum(1 for r in sel if r[m]["doc_rank10"]) / n
        leg = [r for r in sel if r["source"] == "fek_a"]
        art = (sum(1 for r in leg if r[m]["art_rank"]) / len(leg)) if leg else None
        return {"n": n, f"hit@{a.k}": round(hit, 3), "mrr": round(mrr, 3), "hit@10": round(hit10, 3),
                f"article_hit@{a.k}": None if art is None else round(art, 3)}

    groups = {"all": rows, "greek": [r for r in rows if r["lang"] == "el"],
              "english": [r for r in rows if r["lang"] == "en"]}
    for src in sorted({r["source"] for r in rows}):
        groups[src] = [r for r in rows if r["source"] == src]
    summary = {g: {m: agg(v, m) for m in modes} for g, v in groups.items()}
    out = {"k": a.k, "ondemand": a.ondemand, "vectors_stored": s.dense.size if s.dense else 0,
           "summary": summary, "rows": rows, "when": time.strftime("%Y-%m-%d %H:%M")}
    json.dump(out, open(os.path.join(HERE, a.out + ".json"), "w"), ensure_ascii=False, indent=1)
    lines = [f"# Eval results: {a.qfile} ({out['when']}, k={a.k}, stored vectors={out['vectors_stored']})", "",
             "| group | n | " + " | ".join(f"{m} hit@{a.k} | {m} MRR" for m in modes) + " |",
             "|---|---|" + "---|---|" * len(modes)]
    for g, v in summary.items():
        if not any(v.values()):
            continue
        n = next(x["n"] for x in v.values() if x)
        lines.append(f"| {g} | {n} | " + " | ".join(
            f"{v[m][f'hit@{a.k}']:.2f} | {v[m]['mrr']:.2f}" for m in modes) + " |")
    lines += ["", f"Legislation article-level hit@{a.k}: " + ", ".join(
        f"{m} {summary['fek_a'][m][f'article_hit@{a.k}']}" for m in modes if summary.get("fek_a")), "",
        "| q | lang | source | " + " | ".join(f"{m} rank" for m in modes) + " | question |",
        "|---|---|---|" + "---|" * len(modes) + "---|"]
    for r in rows:
        lines.append(f"| {r['id']} | {r['lang']} | {r['source']} | " + " | ".join(
            str(r[m]["doc_rank"] or "–") for m in modes) + f" | {r['question'][:90]} |")
    open(os.path.join(HERE, a.out + ".md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines[:12 + len(groups)]))


if __name__ == "__main__":
    main()
