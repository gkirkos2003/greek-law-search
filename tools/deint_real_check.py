#!/usr/bin/env python3
"""Real-OCR check of lib/fek_deint.py (no ground truth available, so use held-out signals).

Signal: article headings ("Άρθρον k") — the de-interleaver never looks at them. In correct
reading order a heading k is followed by k+1; L/R interleaving scrambles that. We count the
fraction of consecutive heading pairs (k, k+1) with de-interleaving off vs on, plus pages fired,
and the time cost. With --show N it prints N random fired pages before/after for eyeballing.

Run from search/:  nice -n 19 ionice -c3 venv/bin/python tools/deint_real_check.py [ndocs] [--show N]
"""
import os, random, re, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from lib import src_fek, fek_deint  # noqa: E402

ndocs = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 300
show = int(sys.argv[sys.argv.index("--show") + 1]) if "--show" in sys.argv else 0
rnd = random.Random(11)
T = fek_deint.tables()
assert T is not None, "build tables first: tools/build_deint_tables.py"

stems = []
for y in sorted(os.listdir(src_fek.TEXTDIR)):
    if y.isdigit() and 1880 <= int(y) < 2000:
        stems += [(y, f[:-4]) for f in os.listdir(os.path.join(src_fek.TEXTDIR, y)) if f.endswith(".txt")]
stems = rnd.sample(stems, ndocs)


def art_nums(lines):
    out = []
    for ln in lines:
        m = src_fek.match_article(ln, src_fek.fold_upper(ln))
        if m and m[0].isdigit():
            out.append(int(m[0]))
    return out


def succ(nums):
    pairs = list(zip(nums, nums[1:]))
    return sum(1 for a, b in pairs if b == a + 1), len(pairs)


tot = {0: [0, 0], 1: [0, 0]}
pages = fired = 0
t_on = t_off = 0.0
shown = []
for y, st in stems:
    path = os.path.join(src_fek.TEXTDIR, y, st + ".txt")
    for mode in (0, 1):
        src_fek.DEINT = bool(mode)  # forced on/off regardless of FEK_DEINT
        info = {}
        t = time.time()
        lines = src_fek.read_lines(path, "ocr", int(y), info)
        dt = time.time() - t
        if mode:
            t_on += dt
            pages += info["pages"]
            fired += info["deint_pages"]
        else:
            t_off += dt
        a, b = succ(art_nums(lines))
        tot[mode][0] += a
        tot[mode][1] += b
    if show and len(shown) < show and info["deint_pages"]:
        shown.append(path)

print(f"docs {ndocs}, pages {pages}, pages de-interleaved {fired} ({100 * fired / max(pages, 1):.1f}%)")
for mode in (0, 1):
    a, b = tot[mode]
    print(f"  deint={'on ' if mode else 'off'}: article successions k->k+1 {a}/{b} = {100 * a / max(b, 1):.1f}%")
print(f"read_lines time: off {t_off / ndocs:.3f}s/doc, on {t_on / ndocs:.3f}s/doc")

for path in shown:
    with open(path, encoding="utf-8", errors="replace") as fh:
        pgs = re.split(r"\n\s*\n", fh.read())
    for pg in pgs:
        lines = [src_fek.clean_line(l, True) for l in pg.split("\n")]
        lines = [l for l in lines if l]
        out, f = fek_deint.deinterleave(T, lines)
        if f:
            print("=" * 30, path, "BEFORE (first 24 lines)")
            print("\n".join(lines[:24]))
            k = fek_deint.LAST_SPLIT
            print("-" * 30, "AFTER: column L (first 14 lines)")
            print("\n".join(out[:14]))
            print("-" * 30, "AFTER: column R (first 14 lines)")
            print("\n".join(out[k:k + 14]))
            break
