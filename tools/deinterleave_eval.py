#!/usr/bin/env python3
"""Evaluate a de-interleaving heuristic for PaddleOCR two-column ΦΕΚ pages (no box coords saved).

Synthetic test: take 2000+ born-digital ΦΕΚ pages, get every text line with its bbox
(pdftotext -bbox-layout), split into left / right column, then re-order the lines the way
PaddleOCR does (sort by box top y at 200 DPI with a little jitter, ties by x) -> interleaved
input with known truth.  Metric = successor accuracy: fraction of lines whose next line in the
output is its true next line (reading order L column then R column).
Also run on genuinely single-column sequences to measure damage.

Run from search/:  nice -n 19 ionice -c3 venv/bin/python tools/deinterleave_eval.py
"""
import os, random, re, subprocess, sys, time, zlib
import xml.etree.ElementTree as ET
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from lib import src_fek  # noqa: E402

BASE = os.environ.get("GREEKLAW_ROOT", "data")
WORK = os.path.join(BASE, "search", "work")
from lib.fek_deint import Tables, deinterleave as _deint  # noqa: E402

STATS = []


def deinterleave(t, lines):
    return _deint(t, lines, stats=STATS)


# ------------------------------------------------------------------ synthetic data

def page_lines(pdf, first, last):
    r = subprocess.run(["pdftotext", "-q", "-bbox-layout", "-f", str(first), "-l", str(last), pdf, "-"],
                       capture_output=True, timeout=120)
    xml = r.stdout.decode("utf-8", "replace")
    xml = re.sub(r"<!DOCTYPE[^>]*>", "", xml).replace('xmlns="http://www.w3.org/1999/xhtml"', "")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    pages = []
    for pg in root.iter("page"):
        W = float(pg.get("width"))
        L, R, S = [], [], []
        for fl in pg.iter("flow"):
            for bl in fl.iter("block"):
                for ln in bl.iter("line"):
                    words = [w.text or "" for w in ln.iter("word")]
                    txt = src_fek.clean_line(" ".join(words), False)
                    if not txt or src_fek.is_boiler(src_fek.fold_upper(txt), txt):
                        continue
                    x0, y0, x1 = float(ln.get("xMin")), float(ln.get("yMin")), float(ln.get("xMax"))
                    item = (txt, x0, y0)
                    if x1 < W * 0.54:
                        L.append(item)
                    elif x0 > W * 0.46:
                        R.append(item)
                    else:
                        S.append(item)
        pages.append((L, R, S))
    return pages


def paddle_order(items, rnd):
    sc = 200 / 72.0
    pts = [(it[2] * sc + rnd.gauss(0, 1.5), it[1] * sc, it[0]) for it in items]
    pts.sort(key=lambda p: (p[0], p[1]))
    # PaddleOCR sorted_boxes: swap neighbours whose y differs < 10 px and x is reversed
    for i in range(len(pts) - 1):
        for j in range(i, -1, -1):
            if abs(pts[j + 1][0] - pts[j][0]) < 10 and pts[j + 1][1] < pts[j][1]:
                pts[j], pts[j + 1] = pts[j + 1], pts[j]
            else:
                break
    return [p[2] for p in pts]


def succ_acc(out, truth):
    nxt = {truth[i]: truth[i + 1] for i in range(len(truth) - 1)}
    ok = sum(1 for a, b in zip(out, out[1:]) if nxt.get(a) == b)
    return ok / max(1, len(truth) - 1)


def main():
    rnd = random.Random(3)
    pdfs = []
    for y in range(2006, 2026):
        d = os.path.join(BASE, "pdf", str(y))
        fs = sorted(os.listdir(d))
        pdfs += [os.path.join(d, f) for f in rnd.sample(fs, 6)]
    # tables from the reading-order 2000+ extraction, excluding the test PDFs
    t = Tables()
    test_stems = {os.path.basename(p)[:-4] for p in pdfs}
    n = 0
    t0 = time.time()
    for y in range(2005, 2027):
        d = os.path.join(src_fek.FEKA_TEXT, str(y))
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d))[::3]:
            if f[:-4] in test_stems:
                continue
            with open(os.path.join(d, f), encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    t.add_line(line.strip())
            n += 1
    print(f"tables from {n} docs in {time.time() - t0:.0f}s")
    res = {"two_base": [], "two_new": [], "two_fired": 0, "two_n": 0,
           "one_base": [], "one_new": [], "one_fired": 0, "one_n": 0}
    for pdf in pdfs:
        try:
            pages = page_lines(pdf, 2, 4)
        except subprocess.TimeoutExpired:
            continue
        for L, R, S in pages:
            if len(L) >= 10 and len(R) >= 10 and len(S) <= 3:
                truth = [x[0] for x in L] + [x[0] for x in R]
                if len(set(truth)) != len(truth):
                    continue
                inter = paddle_order(L + R, rnd)
                STATS.clear()
                out, fired = deinterleave(t, inter)
                if STATS:
                    res.setdefault("two_stats", []).append(STATS[-1])
                res["two_base"].append(succ_acc(inter, truth))
                res["two_new"].append(succ_acc(out, truth))
                res["two_fired"] += fired
                res["two_n"] += 1
                # single-column control: the left column alone
                one = [x[0] for x in L]
                STATS.clear()
                out1, fired1 = deinterleave(t, one)
                if STATS:
                    res.setdefault("one_stats", []).append(STATS[-1])
                res["one_base"].append(1.0)
                res["one_new"].append(succ_acc(out1, one))
                res["one_fired"] += fired1
                res["one_n"] += 1
    m = lambda xs: sum(xs) / max(1, len(xs))
    print(f"two-column pages: {res['two_n']}  successor acc interleaved {m(res['two_base']):.3f} "
          f"-> de-interleaved {m(res['two_new']):.3f}  (fired on {res['two_fired']})")
    for k in ("two_stats", "one_stats"):
        st = sorted(res.get(k, []))
        if st:
            q = lambda i: [round(sorted(x[i] for x in st)[int(len(st) * p)], 3) for p in (0.05, 0.25, 0.5, 0.75, 0.95)]
            print(k, "switch-rate q05..q95", q(0), " gain/line", q(1), " minor-col share", q(2))
    print(f"single-column control: {res['one_n']}  successor acc 1.000 -> {m(res['one_new']):.3f} "
          f"(fired on {res['one_fired']})")


if __name__ == "__main__":
    main()
