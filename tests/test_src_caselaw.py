"""Sample-check lib/src_caselaw.py.

Run from search/:  OMP_NUM_THREADS=1 nice -n 19 ionice -c3 venv/bin/python tests/test_src_caselaw.py [n_per_source] [source ...]

Prints per source: units, sampled docs, chunks, chars/chunk percentiles, % docs with a date,
3 example chunks, load time, plus contract checks (hard max, empty chunks, unique doc_ids, seq order).
"""
import os
import random
import resource
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import src_caselaw as S  # noqa: E402


def pct(v, q):
    if not v:
        return 0
    v = sorted(v)
    return v[min(len(v) - 1, int(q * len(v)))]


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    srcs = sys.argv[2:] or list(S.SOURCES)
    rnd = random.Random(42)
    total_t = time.time()
    for src in srcs:
        t0 = time.time()
        units = list(S.iter_units({src}))
        t_iter = time.time() - t0
        ids = [u.doc_id for u in units]
        dup_ids = len(ids) - len(set(ids))
        sample = units if len(units) <= n else rnd.sample(units, n)
        if src == "nsk":       # make sure both kinds are represented
            full = [u for u in units if not u.fingerprint.endswith(":-")]
            meta = [u for u in units if u.fingerprint.endswith(":-")]
            sample = rnd.sample(full, min(n // 2, len(full))) + rnd.sample(meta, min(n - n // 2, len(meta)))
        t1 = time.time()
        loaded = []
        for u in sample:
            d, ch = u.load()
            loaded.append((u, d, ch))
        t_load = time.time() - t1
        sizes = [len(c["text"]) for _, _, ch in loaded for c in ch]
        nch = [len(ch) for _, _, ch in loaded]
        dated = sum(1 for _, d, _ in loaded if d.get("date"))
        over = sum(1 for s in sizes if s > S.HARD_MAX)
        empty = sum(1 for s in sizes if s == 0)
        tiny = sum(1 for s in sizes if s < 150)
        badseq = sum(1 for _, _, ch in loaded if [c["seq"] for c in ch] != list(range(len(ch))))
        badkeys = sum(1 for _, d, _ in loaded if set(d) != {"doc_id", "source", "citation", "title", "date", "year",
                                                            "number", "court", "path", "ocr_conf", "meta"})
        idmismatch = sum(1 for u, d, _ in loaded if d["doc_id"] != u.doc_id or d["source"] != src)
        print("=" * 100)
        print(f"[{src}] units={len(units)} dup_doc_ids={dup_ids} iter_units={t_iter:.1f}s")
        print(f"  sampled={len(loaded)} load={t_load:.2f}s ({1000 * t_load / max(1, len(loaded)):.1f} ms/doc)")
        print(f"  chunks={len(sizes)}  chunks/doc p50={pct(nch, .5)} p90={pct(nch, .9)} max={max(nch or [0])}")
        print(f"  chars/chunk p10={pct(sizes, .1)} p50={pct(sizes, .5)} p90={pct(sizes, .9)} max={max(sizes or [0])}"
              f"  >{S.HARD_MAX}: {over}  <150: {tiny}  empty: {empty}")
        print(f"  date detected: {100 * dated / max(1, len(loaded)):.0f}%  bad seq: {badseq}  bad keys: {badkeys}"
              f"  id mismatch: {idmismatch}")
        if src == "nsk":
            ft = [d for _, d, _ in loaded if d["meta"].get("has_fulltext")]
            print(f"  nsk: fulltext docs in sample={len(ft)}  with conf={sum(1 for d in ft if d['ocr_conf'])}"
                  f"  units with text={sum(1 for u in units if not u.fingerprint.endswith(':-'))}")
            st = {}
            for _, d, _ in loaded:
                st[d["meta"]["status"]] = st.get(d["meta"]["status"], 0) + 1
            print(f"  nsk status in sample: {st}")
        for u, d, ch in rnd.sample(loaded, min(3, len(loaded))):
            print(f"  --- {d['doc_id']} | {d['citation']} | {d['date']} | {d['title'][:90] if d['title'] else None}")
            if ch:
                c = ch[min(len(ch) - 1, rnd.randrange(len(ch)))]
                print(f"      [{c['seq']}/{len(ch)} {c['locator']}] ({len(c['text'])} chars)")
                print("      " + c["text"][:700].replace("\n", "\n      "))
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print("=" * 100)
    print(f"total {time.time() - total_t:.1f}s  max RSS {rss:.0f} MB")


if __name__ == "__main__":
    main()
