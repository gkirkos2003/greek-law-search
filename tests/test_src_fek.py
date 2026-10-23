#!/usr/bin/env python3
"""Self-test for lib/src_fek.py on ~200 random ΦΕΚ Α΄ issues stratified by decade.

Run from search/:  OMP_NUM_THREADS=1 nice -n 19 ionice -c3 venv/bin/python tests/test_src_fek.py [N] [seed]
Prints: units found, docs/chunks, chars/chunk percentiles, % chunks with article / law,
load time per doc, per-era breakdown, and contract checks (sizes, keys, seq order).
"""
import os, random, sys, time, collections

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from lib import src_fek  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 200
SEED = int(sys.argv[2]) if len(sys.argv) > 2 else 7
DOC_KEYS = {"doc_id", "source", "citation", "title", "date", "year", "number", "court", "path",
            "ocr_conf", "meta"}
CHUNK_KEYS = {"seq", "locator", "law", "article", "text"}


def pct(xs, ps=(5, 25, 50, 75, 95, 99)):
    xs = sorted(xs)
    if not xs:
        return {}
    return {p: xs[min(len(xs) - 1, int(len(xs) * p / 100))] for p in ps}


def main():
    t = time.time()
    units = list(src_fek.iter_units())
    t_iter = time.time() - t
    kinds = collections.Counter(u.fingerprint.split("|")[1] for u in units)
    print(f"iter_units: {len(units)} units in {t_iter:.1f}s  by text source: {dict(kinds)}")
    t = time.time()
    list(src_fek.iter_units())
    print(f"iter_units (warm cache) {time.time() - t:.1f}s")
    assert len({u.doc_id for u in units}) == len(units), "duplicate doc_id"

    by_dec = collections.defaultdict(list)
    for u in units:
        by_dec[int(u.doc_id[5:9]) // 10 * 10].append(u)
    rnd = random.Random(SEED)
    per = max(1, N // len(by_dec))
    sample = []
    for dec in sorted(by_dec):
        sample += rnd.sample(by_dec[dec], min(per, len(by_dec[dec])))
    print(f"sample: {len(sample)} issues over {len(by_dec)} decades")

    sizes, times, errs, slow = [], [], [], []
    tot = art = law = 0
    era = collections.defaultdict(lambda: [0, 0, 0, 0, 0])   # docs, chunks, art, law, empty docs
    over = under = 0
    t_meta = time.time()
    src_fek._issue_meta(); src_fek._ocr_conf()
    print(f"metadata load {time.time() - t_meta:.1f}s")
    for u in sample:
        t = time.time()
        try:
            doc, chunks = u.load()
        except Exception as ex:          # report, keep going
            errs.append((u.doc_id, repr(ex)))
            continue
        times.append(time.time() - t)
        slow.append((times[-1], u.doc_id, len(chunks)))
        assert set(doc) == DOC_KEYS, set(doc) ^ DOC_KEYS
        assert doc["doc_id"] == u.doc_id and doc["source"] == "fek_a"
        y = doc["year"]
        e = "pre1900" if y < 1900 else "1900-1949" if y < 1950 else "1950-1999" if y < 2000 else "2000+"
        era[e][0] += 1
        era[e][1] += len(chunks)
        if not chunks:
            era[e][4] += 1
        for i, c in enumerate(chunks):
            assert set(c) == CHUNK_KEYS and c["seq"] == i
            n = len(c["text"])
            sizes.append(n)
            over += n > src_fek.HARD_MAX
            under += n < src_fek.MIN_CHUNK
            tot += 1
            if c["article"]:
                art += 1
                era[e][2] += 1
            if c["law"]:
                law += 1
                era[e][3] += 1
    print(f"docs loaded: {len(times)}  errors: {len(errs)}  chunks: {tot}")
    for d, ex in errs[:10]:
        print("  ERR", d, ex)
    print("chars/chunk percentiles:", pct(sizes))
    print(f"chunks > {src_fek.HARD_MAX}: {over}   chunks < {src_fek.MIN_CHUNK}: {under} ({100 * under / max(tot, 1):.1f}%)")
    print(f"% chunks with article: {100 * art / max(tot, 1):.1f}   % with law: {100 * law / max(tot, 1):.1f}")
    print("load s/doc percentiles:", {k: round(v, 3) for k, v in pct(times, (50, 90, 99)).items()},
          f"max {max(times):.2f}s  mean {sum(times) / len(times):.3f}s")
    print("slowest:", [(round(a, 2), b, c) for a, b, c in sorted(slow, reverse=True)[:3]])
    print("per era: docs chunks %article %law empty-docs")
    for e in ("pre1900", "1900-1949", "1950-1999", "2000+"):
        d, c, a, lw, em = era[e]
        if d:
            print(f"  {e:10s} {d:4d} {c:6d}  {100 * a / max(c, 1):5.1f}  {100 * lw / max(c, 1):5.1f}  {em}")


if __name__ == "__main__":
    main()
