#!/usr/bin/env python3
"""Extract ΦΕΚ Τεύχος Β΄ 2000–2026 (born-digital) into a few big shard files.

Plain `pdftotext` (reading order = column-correct, de-hyphenated; see notes/fek.md), the
2000–2005 WinAnsi/cp1253 mojibake fixed at extraction time (lib.src_fek.fix_legacy_encoding).

Output (NTFS is slow with many small files, so no per-issue files):
  search/work/fekB_text/<year>.jsonl.gz   one gzip MEMBER per record (the file is a valid
                                          multi-member .jsonl.gz: `zcat` works), record =
                                          {"stem","year","pages","text","fixed_mojibake"}
  search/work/fekB_text/manifest.jsonl    one row per stem: status, src size/mtime, pages, chars,
                                          shard, off, len (byte range of the gzip member), secs
Resumable: stems already in the manifest are skipped; on start every shard is truncated back to
the end of its last manifest-recorded member (drops a half-written tail after a kill).
Order: newest year first (2026 -> 2000), issues ascending inside a year.

Run from search/ (ONE process, low priority; Bash run_in_background — nohup/& dies):
  OMP_NUM_THREADS=1 nice -n 19 ionice -c3 venv/bin/python -u tools/extract_fekB.py [--years 2000-2026] [--limit N]
"""
import argparse, gzip, json, os, subprocess, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from lib.src_fek import fix_legacy_encoding  # noqa: E402

BASE = os.environ.get("GREEKLAW_ROOT", "data")
PDFDIR = os.path.join(BASE, "seriesB")
OUTDIR = os.path.join(BASE, "search", "work", "fekB_text")
MANIFEST = os.path.join(OUTDIR, "manifest.jsonl")
WHOLE_TIMEOUT = 300
RANGE_PAGES = 100
RANGE_TIMEOUT = 120
TOTAL_CAP = 1200


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


def load_manifest():
    rows = {}
    if os.path.exists(MANIFEST):
        with open(MANIFEST, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                rows[r["stem"]] = r
    return rows


def repair_shards(rows):
    ends = {}
    for r in rows.values():
        if r.get("shard"):
            ends[r["shard"]] = max(ends.get(r["shard"], 0), r["off"] + r["len"])
    for name in os.listdir(OUTDIR) if os.path.isdir(OUTDIR) else []:
        if name.endswith(".jsonl.gz"):
            p = os.path.join(OUTDIR, name)
            end = ends.get(name, 0)
            if os.path.getsize(p) > end:
                with open(p, "r+b") as fh:
                    fh.truncate(end)
                print(f"truncated {name} to {end}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="2000-2026")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    y0, y1 = (int(x) for x in args.years.split("-"))
    os.makedirs(OUTDIR, exist_ok=True)
    rows = load_manifest()
    repair_shards(rows)
    years = sorted((y for y in os.listdir(PDFDIR) if y.isdigit() and y0 <= int(y) <= y1), reverse=True)
    todo = []
    for y in years:
        with os.scandir(os.path.join(PDFDIR, y)) as it:
            names = sorted(e.name for e in it if e.name.endswith(".pdf"))
        todo += [(y, n[:-4]) for n in names if n[:-4] not in rows]
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(todo)} PDFs to extract ({len(rows)} already in manifest)", flush=True)
    t_all = time.time()
    chars_all = pages_all = 0
    shard_fh = {}
    with open(MANIFEST, "a", encoding="utf-8") as mf:
        for i, (y, stem) in enumerate(todo, 1):
            pdf = os.path.join(PDFDIR, y, stem + ".pdf")
            t = time.time()
            try:
                st = os.stat(pdf)
                txt, status = extract(pdf)
            except Exception as ex:
                st, txt, status = None, "", "fail"
                print(f"FAIL {stem}: {type(ex).__name__}: {ex}"[:200], flush=True)
            fixed = False
            if txt:
                new = fix_legacy_encoding(txt)
                fixed = new is not txt and new != txt
                txt = new
            if status != "fail" and len("".join(txt.split())) < 50:
                status = "empty"
            row = {"stem": stem, "year": int(y), "status": status,
                   "src_size": st.st_size if st else None, "src_mtime_ns": st.st_mtime_ns if st else None,
                   "pages": txt.count("\f") + (1 if txt and not txt.endswith("\f") else 0),
                   "chars": len(txt), "fixed_mojibake": fixed, "shard": None, "off": None, "len": None}
            if status in ("ok", "ranges", "partial"):
                shard = f"{y}.jsonl.gz"
                fh = shard_fh.get(shard)
                if fh is None:
                    for old in shard_fh.values():
                        old.close()
                    shard_fh.clear()
                    fh = shard_fh[shard] = open(os.path.join(OUTDIR, shard), "ab")
                rec = {"stem": stem, "year": int(y), "pages": row["pages"], "text": txt,
                       "fixed_mojibake": fixed}
                blob = gzip.compress((json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8"),
                                     compresslevel=6)
                off = fh.tell()
                fh.write(blob)
                fh.flush()
                row.update(shard=shard, off=off, len=len(blob))
                chars_all += len(txt)
                pages_all += row["pages"]
            row["secs"] = round(time.time() - t, 2)
            mf.write(json.dumps(row) + "\n")
            mf.flush()
            if i % 500 == 0 or status not in ("ok", "empty"):
                el = time.time() - t_all
                print(f"[{i}/{len(todo)}] {stem} {status}  elapsed {el:.0f}s  {i / el:.2f} PDF/s  "
                      f"{pages_all / el:.1f} pages/s  {chars_all / 1e6:.0f}M chars  "
                      f"eta {el / i * (len(todo) - i) / 3600:.1f}h", flush=True)
    for fh in shard_fh.values():
        fh.close()
    print(f"DONE {len(todo)} in {time.time() - t_all:.0f}s", flush=True)


if __name__ == "__main__":
    sys.exit(main())
