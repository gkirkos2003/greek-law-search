#!/usr/bin/env python3
"""Second real-OCR check: line-boundary coherence judged by tables the de-interleaver does NOT use.
Modern tables = words/bigrams from the 2000+ born-digital extraction. For every adjacent output line
pair (a, b): hyphen pair -> does a's half + b's first word form a modern word; else is the cross-line
bigram frequent in the modern corpus. Off vs on, same 200 random OCR issues (1880-1999).
Run from search/: nice -n 19 ionice -c3 venv/bin/python tools/deint_real_check2.py
"""
import os, random, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from lib import src_fek, fek_deint  # noqa: E402

M = fek_deint.Tables()
for y in range(2000, 2027):
    d = os.path.join(src_fek.FEKA_TEXT, str(y))
    if os.path.isdir(d):
        for f in sorted(os.listdir(d))[::8]:
            for line in open(os.path.join(d, f), encoding="utf-8", errors="replace"):
                M.add_line(line.strip())
rnd = random.Random(11)
stems = []
for y in sorted(os.listdir(src_fek.TEXTDIR)):
    if y.isdigit() and 1880 <= int(y) < 2000:
        stems += [(y, f[:-4]) for f in os.listdir(os.path.join(src_fek.TEXTDIR, y)) if f.endswith(".txt")]
stems = rnd.sample(stems, 200)
res = {}
for mode in (0, 1):
    src_fek.DEINT = bool(mode)
    hy = hyok = bg = bgok = 0
    for y, st in stems:
        lines = src_fek.read_lines(os.path.join(src_fek.TEXTDIR, y, st + ".txt"), "ocr", int(y))
        for a, b in zip(lines, lines[1:]):
            aw, bw = a.split()[-1], b.split()[0]
            if a.endswith("-") and len(aw) > 2:
                hy += 1
                hyok += M.word(fek_deint._fold(aw[:-1] + bw)) >= 2
            else:
                x, z = fek_deint._fold(aw), fek_deint._fold(bw)
                if x and z:
                    bg += 1
                    bgok += M.bigram(x, z) >= 5
    res[mode] = (hyok / max(hy, 1), bgok / max(bg, 1), hy, bg)
    print(f"deint={'on ' if mode else 'off'}: hyphen joins forming a modern word {100 * res[mode][0]:.1f}% "
          f"(n={hy}); cross-line bigram frequent in modern corpus {100 * res[mode][1]:.1f}% (n={bg})", flush=True)
