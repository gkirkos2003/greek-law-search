#!/usr/bin/env python3
"""Column-correct re-extraction of the 2000+ ΦΕΚ Α΄ PDFs.

Why: ../text/<year>/*.txt for 2000+ was made with `pdftotext -layout`, which keeps the two
page columns side by side on one physical line (left/right interleaved). Plain `pdftotext`
(no -layout = "reading order" mode) detects the columns, emits left column then right column,
and also de-hyphenates line ends. Measured on 40 random issues: 0 lost words apart from
de-hyphenated halves; 3,768-page / 843 MB issue Α 94/2016 = 10.5 s, 31 MB RSS.

Output: search/work/fekA_text/<year>/<stem>.txt  (raw pdftotext output, \f between pages;
        cleaning is done later by lib/src_fek.py so it can change without re-extracting)
        search/work/fekA_text/manifest.jsonl  one row per attempt (last row per stem wins)
Resumable: a stem is skipped when its manifest row matches the PDF's current size+mtime.
Fallbacks: whole-file timeout -> per 100-page ranges -> status "fail" (src_fek then falls back
to the old -layout text in ../text).

Run (ONE process, low priority) — from search/:
  nice -n 19 ionice -c3 venv/bin/python tools/extract_fekA.py [--years 2000-2026] [--limit N]
"""
import argparse, json, os, subprocess, sys, time

BASE = os.environ.get("GREEKLAW_ROOT", "data")
PDFDIR = os.path.join(BASE, "pdf")
OUTDIR = os.path.join(BASE, "search", "work", "fekA_text")
MANIFEST = os.path.join(OUTDIR, "manifest.jsonl")
WHOLE_TIMEOUT = 300
RANGE_PAGES = 100
RANGE_TIMEOUT = 120
TOTAL_CAP = 1800          # max seconds spent on one PDF (all attempts)


def load_manifest():
    done = {}
    if os.path.exists(MANIFEST):
        with open(MANIFEST, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                done[r["stem"]] = r
    return done


def pdftotext(pdf, first=None, last=None, timeout=WHOLE_TIMEOUT):
    cmd = ["pdftotext", "-q", "-enc", "UTF-8"]
    if first:
        cmd += ["-f", str(first), "-l", str(last)]
    cmd += [pdf, "-"]
    r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    return r.stdout.decode("utf-8", "replace")


def npages(pdf):
    try:
        r = subprocess.run(["pdfinfo", pdf], capture_output=True, timeout=60)
        for line in r.stdout.decode("utf-8", "replace").splitlines():
            if line.startswith("Pages:"):
                return int(line.split()[1])
    except Exception:
        pass
    return 0


def extract(pdf):
    t0 = time.time()
    try:
        return pdftotext(pdf), "ok"
    except subprocess.TimeoutExpired:
        pass
    n = npages(pdf)
    parts = []
    for a in range(1, n + 1, RANGE_PAGES):
        if time.time() - t0 > TOTAL_CAP:
            return "".join(parts), "partial"
        b = min(n, a + RANGE_PAGES - 1)
        try:
            parts.append(pdftotext(pdf, a, b, RANGE_TIMEOUT))
        except subprocess.TimeoutExpired:
            parts.append("\f" * (b - a + 1))
    return "".join(parts), ("ranges" if parts else "fail")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="2000-2100")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    y0, y1 = (int(x) for x in args.years.split("-"))
    os.makedirs(OUTDIR, exist_ok=True)
    done = load_manifest()
    jobs = []
    for y in sorted(os.listdir(PDFDIR)):
        if not (y.isdigit() and y0 <= int(y) <= y1):
            continue
        with os.scandir(os.path.join(PDFDIR, y)) as it:
            for e in it:
                if e.name.endswith(".pdf"):
                    st = e.stat()
                    stem = e.name[:-4]
                    r = done.get(stem)
                    outp = os.path.join(OUTDIR, y, stem + ".txt")
                    if r and r.get("src_size") == st.st_size and r.get("src_mtime_ns") == st.st_mtime_ns \
                            and (r["status"] in ("fail", "empty") or os.path.exists(outp)):
                        continue
                    jobs.append((y, stem, e.path, st))
    jobs.sort()
    if args.limit:
        jobs = jobs[:args.limit]
    print(f"{len(jobs)} PDFs to extract", flush=True)
    t_all = time.time()
    with open(MANIFEST, "a", encoding="utf-8") as mf:
        for i, (y, stem, pdf, st) in enumerate(jobs, 1):
            t = time.time()
            try:
                txt, status = extract(pdf)
            except Exception as ex:          # never die on one file
                txt, status = "", "fail"
                print(f"FAIL {stem}: {type(ex).__name__}: {ex}"[:200], flush=True)
            if len("".join(txt.split())) < 50 and status != "fail":
                status = "empty"
            if txt and status != "fail":
                d = os.path.join(OUTDIR, y)
                os.makedirs(d, exist_ok=True)
                outp = os.path.join(d, stem + ".txt")
                with open(outp + ".part", "w", encoding="utf-8") as fh:
                    fh.write(txt)
                os.replace(outp + ".part", outp)
            row = {"stem": stem, "src_size": st.st_size, "src_mtime_ns": st.st_mtime_ns,
                   "status": status, "secs": round(time.time() - t, 2), "chars": len(txt)}
            mf.write(json.dumps(row) + "\n")
            mf.flush()
            if i % 200 == 0 or status not in ("ok",):
                el = time.time() - t_all
                print(f"[{i}/{len(jobs)}] {stem} {status} {row['secs']}s  elapsed {el:.0f}s "
                      f"eta {el / i * (len(jobs) - i):.0f}s", flush=True)
    print(f"DONE {len(jobs)} in {time.time() - t_all:.0f}s", flush=True)


if __name__ == "__main__":
    sys.exit(main())
