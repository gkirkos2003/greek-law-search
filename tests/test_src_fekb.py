#!/usr/bin/env python3
"""Self-test for lib/src_fekb.py on ~N random ΦΕΚ Β΄ issues, stratified by the years extracted so far.

Run from search/:  OMP_NUM_THREADS=1 nice -n 19 ionice -c3 venv/bin/python tests/test_src_fekb.py [N] [seed]
Prints: units, chunks, chars/chunk percentiles, acts/issue, % acts with number / title / authority,
% issues with a date, % chunks whose locator carries an act number, % chunks with article, load time.
"""
import collections, os, random, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from lib import src_fekb  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 200
SEED = int(sys.argv[2]) if len(sys.argv) > 2 else 7
DOC_KEYS = {"doc_id", "source", "citation", "title", "date", "year", "number", "court", "path",
            "ocr_conf", "meta"}
CHUNK_KEYS = {"seq", "locator", "law", "article", "text", "head"}


def pct(xs, ps=(5, 25, 50, 75, 95, 99)):
    xs = sorted(xs)
    return {p: xs[min(len(xs) - 1, int(len(xs) * p / 100))] for p in ps} if xs else {}


def main():
    t = time.time()
    units = list(src_fekb.iter_units())
    print(f"iter_units: {len(units)} units in {time.time() - t:.2f}s")
    if not units:
        print("nothing extracted yet")
        return
    assert len({u.doc_id for u in units}) == len(units)
    by = collections.defaultdict(list)
    for u in units:
        by[u.doc_id[5:9]].append(u)
    rnd = random.Random(SEED)
    per = max(1, N // len(by))
    sample = [x for y in sorted(by) for x in rnd.sample(by[y], min(per, len(by[y])))]
    print(f"sample: {len(sample)} issues from years {sorted(by)}")
    sizes, times, acts_n, slow = [], [], [], []
    tot = art = numbered_loc = dated = 0
    acts_tot = a_num = a_title = a_auth = 0
    over = 0
    for u in sample:
        t = time.time()
        doc, chunks = u.load()
        times.append(time.time() - t)
        slow.append((times[-1], u.doc_id, doc["meta"]["pages"]))
        assert set(doc) == DOC_KEYS and doc["source"] == "fek_b" and doc["doc_id"] == u.doc_id
        dated += bool(doc["date"])
        acts = doc["meta"]["acts"]
        acts_n.append(len(acts))
        for a in acts:
            acts_tot += 1
            a_num += bool(a["number"])
            a_title += bool(a["title"])
            a_auth += bool(a["authority"])
        for i, c in enumerate(chunks):
            assert set(c) == CHUNK_KEYS and c["seq"] == i and c["law"] is None
            sizes.append(len(c["text"]))
            over += len(c["text"]) > 2500
            tot += 1
            art += bool(c["article"])
            numbered_loc += c["locator"].startswith("Αριθμ.")
    n = len(sample)
    print(f"chunks: {tot}  ({tot / n:.1f}/issue)  chars/chunk: {pct(sizes)}  >2500: {over}")
    print(f"acts/issue: {pct(acts_n, (50, 90, 99))}  total acts {acts_tot}: with number "
          f"{100 * a_num / max(acts_tot, 1):.1f}%  title {100 * a_title / max(acts_tot, 1):.1f}%  "
          f"authority {100 * a_auth / max(acts_tot, 1):.1f}%")
    print(f"issues with date: {100 * dated / n:.1f}%   chunks with act number in locator: "
          f"{100 * numbered_loc / max(tot, 1):.1f}%   chunks with article: {100 * art / max(tot, 1):.1f}%")
    print(f"load s/doc: {pct(times, (50, 90, 99))}  mean {sum(times) / n:.3f}s  slowest "
          f"{[(round(a, 2), b, p) for a, b, p in sorted(slow, reverse=True)[:3]]}")


if __name__ == "__main__":
    main()
