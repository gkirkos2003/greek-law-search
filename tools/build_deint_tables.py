#!/usr/bin/env python3
"""Build the hashed word / within-line-bigram tables for lib/fek_deint.py from the pre-2000
PaddleOCR texts (every STEP-th file).  ~1 thread, < 300 MB RAM.
Run from search/:  nice -n 19 ionice -c3 venv/bin/python tools/build_deint_tables.py [STEP]
"""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from lib.fek_deint import Tables, TABLES  # noqa: E402

TEXT = os.path.join(os.environ.get("GREEKLAW_ROOT", "data"), "text")
step = int(sys.argv[1]) if len(sys.argv) > 1 else 3
t, n, t0 = Tables(), 0, time.time()
for y in sorted(os.listdir(TEXT)):
    if not y.isdigit() or int(y) >= 2000:
        continue
    for f in sorted(os.listdir(os.path.join(TEXT, y)))[::step]:
        if not f.endswith(".txt"):
            continue
        with open(os.path.join(TEXT, y, f), encoding="utf-8", errors="replace") as fh:
            for line in fh:
                t.add_line(line.strip())
        n += 1
    print(y, n, f"{time.time() - t0:.0f}s", flush=True)
t.save(TABLES)
print("saved", TABLES, n, "docs", f"{time.time() - t0:.0f}s")
