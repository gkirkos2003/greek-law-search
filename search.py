#!/usr/bin/env python3
"""Greek law search.

  search.py "ερώτηση ή question"            top passages with exact citations
  search.py -k 20 --source nsk,areios_pagos "..."
  search.py --years 1990-2010 "..."
  search.py --mode bm25 "..."              BM25 only (default: hybrid if vectors exist)
  search.py --law "ν. 4412/2016"           cross-references: ΦΕΚ, who amends / repeals / cites it
  search.py --law "άρθρο 5 ν. 4412/2016"   same, for one article
  search.py --json "..."                   machine-readable output

Run with the venv:  python search.py "..."
"""
import argparse
import json
import os
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("OMP_NUM_THREADS", "3")

from lib.searcher import Searcher   # noqa: E402

SRC_NAME = {"fek_a": "ΦΕΚ Α΄", "fek_b": "ΦΕΚ Β΄", "areios_pagos": "Άρειος Πάγος",
            "elsyn": "Ελεγκτικό Συνέδριο", "coa": "Ελεγκτικό Συνέδριο (PDF)", "nsk": "ΝΣΚ",
            "ste": "ΣτΕ", "eisap": "Εισαγγελία ΑΠ"}


def fmt_hit(i, h, refs=None):
    cite = h["citation"] or h["doc_id"]
    loc = h.get("locator") or ""
    head = f"[{i}] {cite}" + (f" — {loc}" if loc and loc not in cite else "")
    lines = [head]
    meta = []
    if h.get("date"):
        meta.append(h["date"])
    meta.append(SRC_NAME.get(h["source"], h["source"]))
    if h.get("ocr_conf"):
        meta.append(f"OCR conf {h['ocr_conf']:.2f}")
    lines.append("    " + " · ".join(meta))
    if h.get("title"):
        lines.append("    " + textwrap.shorten(h["title"], 160))
    lines.append(textwrap.fill(h["snippet"], 100, initial_indent="    │ ", subsequent_indent="    │ "))
    if refs:
        r = ", ".join(sorted({(x["key"] + (f" άρ.{x['article']}" if x.get("article") else "")
                               + ("" if x["relation"] == "cites" else f" ({x['relation']})"))
                              for x in refs}))[:400]
        lines.append(f"    ↳ cites: {r}")
    lines.append(f"    file: {h['path']}")
    return "\n".join(lines)


LAW_SHOW = 8


def print_law(info):
    print(f"== {info['key']}" + (f" άρθρο {info['article']}" if info.get("article") else ""))
    for f in info.get("fek") or []:
        print(f"   ΦΕΚ {f.get('label')}  {f.get('date') or ''}  {textwrap.shorten(f.get('title') or '', 120)}")
    ch = info.get("changed_by") or []
    print(f"\n   Changed by later legislation ({len(ch)} shown):")
    for it in ch:
        print(f"     - {it['relation']:<8} {('άρθρο ' + it['article']) if it.get('article') else '':<12} "
              f"by {it.get('by_law') or ''} {it['by']} — {it.get('locator') or ''}")
    own = info.get("this_law_changes") or []
    if own:
        print(f"\n   This law itself changes:")
        for it in own:
            print(f"     - {it['relation']:<8} {it['key']} {('άρθρο ' + it['article']) if it.get('article') else ''}")
    dc = info.get("cited_by_doc_counts") or {}
    print("\n   Cited by: " + (", ".join(f"{SRC_NAME.get(s, s)} {n} docs ({info['cited_by_counts'][s]} passages)"
                                     for s, n in sorted(dc.items(), key=lambda kv: -kv[1])) or "—"))
    for src, items in sorted((info.get("cited_by_docs") or {}).items(), key=lambda kv: -len(kv[1])):
        print(f"   [{SRC_NAME.get(src, src)}] newest first:")
        for it in items[:LAW_SHOW]:
            arts = ", ".join(it["articles"][:12]) + (" …" if len(it["articles"]) > 12 else "")
            print(f"     - {it['by']}" + (f" — άρθρα {arts}" if arts else "") + f"  ({it['n']}×)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", nargs="*")
    ap.add_argument("-k", type=int, default=8)
    ap.add_argument("--mode", choices=["hybrid", "bm25"], default="hybrid")
    ap.add_argument("--source", help="comma list: fek_a,fek_b,nsk,areios_pagos,elsyn,ste,eisap,coa")
    ap.add_argument("--years", help="e.g. 1990-2010")
    ap.add_argument("--law", help='cross-reference report, e.g. "ν. 4412/2016" or ν:4412/2016')
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--full", action="store_true", help="print full passage text")
    a = ap.parse_args()

    s = Searcher(dense=(a.mode == "hybrid" and not a.law))
    if a.law:
        key = a.law
        art = None
        if ":" not in key and s.xref:
            ks = [(s.xref.act_key(r["key"]), r.get("article")) for r in s.xref.extract(a.law) if r.get("key")]
            key, art = ks[0] if ks else (key, None)
        info = s.law_info(key, limit=max(30, a.k), article=art)
        print(json.dumps(info, ensure_ascii=False, indent=1) if a.json else "", end="")
        if not a.json:
            print_law(info)
        return
    q = " ".join(a.query).strip()
    if not q:
        ap.print_help()
        return
    yf = yt = None
    if a.years:
        yf, _, yt = a.years.partition("-")
    res = s.search(q, k=a.k, mode=a.mode, sources=a.source.split(",") if a.source else None,
                   year_from=yf or None, year_to=yt or None)
    if a.json:
        for h in res["hits"]:
            h["refs"] = s.chunk_refs(h["chunk_id"])
            if not a.full:
                h.pop("text", None)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return
    print(f"Query: {q}   [{res['mode']}, {res['ms']} ms]"
          + (f"   citations: {', '.join(res['citations_in_query'])}" if res["citations_in_query"] else ""))
    if not res["hits"]:
        print("No results.")
    for i, h in enumerate(res["hits"], 1):
        if a.full:
            h["snippet"] = h["text"]
        print()
        print(fmt_hit(i, h, s.chunk_refs(h["chunk_id"])))
    laws = []
    for h in res["hits"]:
        if h.get("law_key") and h["law_key"] not in laws:
            laws.append(h["law_key"])
    for key in laws[:3]:
        info = s.law_info(key, limit=5)
        ch = info["changed_by"]
        if ch:
            print(f"\n⚠ {key} was later touched by: " + "; ".join(
                f"{it.get('by_law') or it['by']} ({it['relation']}{' άρθρο ' + it['article'] if it.get('article') else ''})"
                for it in ch[:5]))


if __name__ == "__main__":
    main()
