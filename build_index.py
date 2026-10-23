#!/usr/bin/env python3
"""Build / update the Greek law search index. Every step is incremental — rerun any time
(e.g. after the NSK OCR finishes) and only new/changed documents are processed.

  build_index.py all              text index + cross-refs + vectors (the usual command)
  build_index.py text  [--sources nsk,areios_pagos] [--limit N]
  build_index.py refts            re-tokenise FTS rows after a lib/textnorm.py change (keeps chunk ids)
  build_index.py xref             citation/amendment table for docs not yet scanned
  build_index.py embed [--threads 3] [--sources fek_a,nsk] [--max N]
  build_index.py stats

Always run niced:  nice -n 19 ionice -c3 venv/bin/python build_index.py all
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
LOG = os.path.join(HERE, "logs", "build.log")


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["all", "text", "refts", "xref", "embed", "stats"])
    ap.add_argument("--sources")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--threads", type=int, default=3)
    ap.add_argument("--max", type=int, help="embed at most N chunks this run")
    a = ap.parse_args()
    for v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(v, str(a.threads))
    srcs = set(a.sources.split(",")) if a.sources else None

    from lib import index
    if a.step in ("all", "text"):
        log(f"== text index (sources={srcs or 'all'})")
        index.build(sources=srcs, limit=a.limit, log=log)
    if a.step in ("all", "text"):
        from lib import chunkmap
        chunkmap.build(index.connect(readonly=True), full=True, log=log)   # chunk -> doc -> source for search
    if a.step in ("all", "refts"):
        log("== re-tokenise FTS rows from older text normalisation")
        index.refts(log=log, sources=srcs)
    if a.step in ("all", "xref"):
        log("== cross-references")
        index.build_xrefs(log=log)
    if a.step in ("all", "embed"):
        log("== vectors")
        from lib import dense
        dense.embed_pending(threads=a.threads, sources=srcs, max_chunks=a.max, log=log)
    if a.step in ("all", "stats", "text"):
        log("stats " + json.dumps(index.stats(), ensure_ascii=False))


if __name__ == "__main__":
    main()
