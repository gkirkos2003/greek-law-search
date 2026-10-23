"""Sample candidate snippets for the xref gold set (fixed seed, streaming, read-only on corpus).

Writes search/tests/xref_samples.jsonl (unlabelled). Labels are added by hand into
search/tests/xref_gold.jsonl.   Run:  nice -n 19 ionice -c3 venv/bin/python tests/make_xref_samples.py
"""
import json
import os
import random
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # greek-law/
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xref_samples.jsonl")
SEED = 20261021
rng = random.Random(SEED)

CUE = re.compile(r"(?:[νΝ]\.|[Νν]όμου|[Νν]όμος|Ν\.Δ|Β\.Δ|Π\.Δ|π\.δ|ν\.δ|β\.δ|Α\.Ν|α\.ν|[Άά]ρθρ|ΑΚ|ΚΠολΔ|ΠΚ|Οδηγ|ΦΕΚ)\S*\s*\d")
AMEND = re.compile(r"αντικαθίσταται|τροποποιείται|καταργείται|καταργούνται|προστίθεται|συμπληρούται|συμπληρώνεται")


def window(t, pos, w=420):
    a = max(0, pos - w // 3)
    b = min(len(t), a + w)
    while a > 0 and not t[a - 1].isspace():
        a -= 1
    while b < len(t) and not t[b].isspace():
        b += 1
    return re.sub(r"[ \t]+", " ", t[a:b]).strip()


def decolumn(t):
    """pdftotext -layout two-column pages -> left column then right column per page block."""
    pages = t.split("\f")
    out = []
    for pg in pages:
        left, right = [], []
        for ln in pg.split("\n"):
            m = re.search(r"\S(\s{3,})\S", ln)
            if m and m.start(1) > 15:
                left.append(ln[:m.start(1)].strip())
                right.append(ln[m.end(1) - 1:].strip())
            elif len(ln) - len(ln.lstrip()) > 40:
                right.append(ln.strip())
            else:
                left.append(ln.strip())
        out.append("\n".join(left) + "\n" + "\n".join(right))
    s = "\n".join(out)
    s = re.sub(r"(\w)[-−]\n(\w)", r"\1\2", s)
    return s


def pick_from_text(t, n, cue=CUE, prefer=None):
    res = []
    ms = [m.start() for m in (prefer or cue).finditer(t)]
    if not ms and prefer:
        ms = [m.start() for m in cue.finditer(t)]
    rng.shuffle(ms)
    used = []
    for p in ms:
        if any(abs(p - u) < 600 for u in used):
            continue
        used.append(p)
        res.append(window(t, p))
        if len(res) >= n:
            break
    return res


def fek_files(lo, hi):
    tdir = os.path.join(ROOT, "text")
    years = sorted(int(y) for y in os.listdir(tdir) if y.isdigit() and lo <= int(y) <= hi)
    return tdir, years


def sample_fek(lo, hi, nfiles, per, decol=False, prefer=None, src="fek"):
    tdir, years = fek_files(lo, hi)
    out = []
    tries = 0
    while len(out) < nfiles * per and tries < nfiles * 8:
        tries += 1
        y = rng.choice(years)
        fs = sorted(os.listdir(os.path.join(tdir, str(y))))
        if not fs:
            continue
        f = rng.choice(fs)
        p = os.path.join(tdir, str(y), f)
        if os.path.getsize(p) > 3_000_000:
            continue
        with open(p, encoding="utf-8", errors="replace") as fh:
            t = fh.read()
        if decol:
            t = decolumn(t)
        for s in pick_from_text(t, per, prefer=prefer):
            out.append({"src": src, "doc": "fekA:" + f[:-4], "year": y, "text": s})
    return out


def sample_jsonl(path, nrec, per, src, field="text", stride=None):
    # stream; reservoir-free: choose line numbers from a known count
    with open(path, encoding="utf-8") as fh:
        total = sum(1 for _ in fh)
    want = set(rng.sample(range(total), nrec * 3))
    out = []
    with open(path, encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            if i not in want:
                continue
            r = json.loads(line)
            t = r.get(field) or ""
            for s in pick_from_text(t, per):
                out.append({"src": src, "doc": str(r.get("id")), "year": r.get("year") or r.get("yrApof"), "text": s})
            if len(out) >= nrec * per:
                break
    return out


def sample_nsk(n):
    path = os.path.join(ROOT, "caselaw", "nsk", "opinions.jsonl")
    with open(path, encoding="utf-8") as fh:
        total = sum(1 for _ in fh)
    want = sorted(rng.sample(range(total), n * 6))
    out = []
    with open(path, encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            if i not in want:
                continue
            r = json.loads(line)
            prov = [p for p in r.get("provisions") or [] if isinstance(p, str) and len(p) < 120
                    and "getValue" not in p and "function" not in p and ":" not in p and re.search(r"\d", p)]
            if len(prov) < 2:
                continue
            out.append({"src": "nsk", "doc": "nsk:" + r["id"], "year": r.get("year"), "provisions": prov[:8]})
            if len(out) >= n:
                break
    return out


def main():
    rows = []
    rows += sample_fek(1920, 1999, 7, 2, src="fek_ocr")
    rows += sample_fek(2000, 2026, 5, 2, decol=True, prefer=AMEND, src="fek_text_amend")
    rows += sample_fek(2000, 2026, 4, 2, decol=True, src="fek_text")
    rows += sample_jsonl(os.path.join(ROOT, "caselaw", "areios-pagos", "decisions.jsonl"), 6, 2, "areios_pagos")
    rows += sample_jsonl(os.path.join(ROOT, "caselaw", "elsyn-tnp", "decisions.jsonl"), 5, 2, "elsyn")
    rows += sample_jsonl(os.path.join(ROOT, "caselaw", "ste", "decisions_9372.jsonl"), 2, 2, "ste")
    rows += sample_nsk(8)
    for i, r in enumerate(rows):
        r["id"] = i
    with open(OUT, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(len(rows), "samples ->", OUT, file=sys.stderr)


if __name__ == "__main__" and len(sys.argv) == 1:
    main()


# ---- targeted tranche: hard patterns (old acts, EU, ΦΕΚ, katharevousa verbs) -------------
TARGETS = [
    ("old_bd_date", r"Β\.\s?Δ\.\s*(?:της|τής)\s+\d"),
    ("old_nd", r"Ν\.\s?Δ\.\s*(?:ύπ'\s*άριθ\.?\s*)?\d"),
    ("old_an", r"[Αα]\.\s?[Νν]\.\s*\d"),
    ("old_kn", r"[Κκ]\.\s?[Νν]\.\s*\d"),
    ("pnp", r"Π\.?\s?Ν\.?\s?Π\.?"),
    ("eu_dir", r"[Οο]δηγί\S*\s+\d"),
    ("eu_reg", r"[Κκ]ανονισμ\S*\s+\((?:ΕΕ|ΕΚ|ΕΟΚ)\)"),
    ("kath_verbs", r"[έε]τροποποιήθη|συμπληρο[ύυ]ται|άντικαθίσταται|καταργε[ίι]ται"),
    ("fek_ref", r"ΦΕΚ\s"),
    ("syntagma", r"Συντάγματος"),
    ("date_first", r"(?:από|άπό)\s+\d{1,2}[.-]\d{1,2}[.-]\d{2,4}\s+(?:Β\.|Ν\.|Π\.)"),
    ("pd_tos", r"[Πп]\.\s?[Δδ]/τος|[Ππ]\.\s?[Δδ]\.\s*\d"),
]


def targeted(per=2, max_files=150):
    tdir, years = fek_files(1920, 2026)
    out = []
    sources = []
    for name, pat in TARGETS:
        rx = re.compile(pat)
        got = 0
        scans = 0
        while got < per and scans < max_files:
            scans += 1
            if rng.random() < 0.6:
                y = rng.choice(years)
                fs = os.listdir(os.path.join(tdir, str(y)))
                if not fs:
                    continue
                f = rng.choice(fs)
                p = os.path.join(tdir, str(y), f)
                if os.path.getsize(p) > 2_000_000:
                    continue
                with open(p, encoding="utf-8", errors="replace") as fh:
                    t = fh.read()
                if y >= 2000:
                    t = decolumn(t)
                doc, src, yy = "fekA:" + f[:-4], "fek", y
            else:
                path = os.path.join(ROOT, "caselaw", rng.choice(["areios-pagos/decisions.jsonl", "elsyn-tnp/decisions.jsonl", "ste/decisions_9371.jsonl"]))
                # random seek into the big jsonl, read the next full line
                size = os.path.getsize(path)
                with open(path, "rb") as fh:
                    fh.seek(rng.randrange(size))
                    fh.readline()
                    line = fh.readline()
                if not line:
                    continue
                r = json.loads(line)
                t = r.get("text") or ""
                doc, src, yy = str(r.get("id")), path.split("/")[-2], r.get("year") or r.get("yrApof")
            ms = list(rx.finditer(t))
            if not ms:
                continue
            m = rng.choice(ms)
            out.append({"src": "target_" + name + ":" + src, "doc": doc, "year": yy, "text": window(t, m.start())})
            got += 1
    return out


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "targeted":
    rows = targeted()
    base = 1000
    with open(OUT.replace("xref_samples", "xref_samples_targeted"), "w", encoding="utf-8") as f:
        for i, r in enumerate(rows):
            r["id"] = base + i
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(len(rows), file=sys.stderr)


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "holdout":
    # blind held-out set, different seed, labelled AFTER the extractor was frozen
    rng.seed(777)
    rows = []
    rows += sample_fek(1920, 1999, 4, 2, src="fek_ocr")
    rows += sample_fek(2000, 2026, 4, 2, decol=True, prefer=AMEND, src="fek_text_amend")
    rows += sample_fek(2000, 2026, 2, 2, decol=True, src="fek_text")
    rows += sample_jsonl(os.path.join(ROOT, "caselaw", "areios-pagos", "decisions.jsonl"), 3, 2, "areios_pagos")
    rows += sample_jsonl(os.path.join(ROOT, "caselaw", "elsyn-tnp", "decisions.jsonl"), 3, 2, "elsyn")
    rows += sample_jsonl(os.path.join(ROOT, "caselaw", "ste", "decisions_9374.jsonl"), 2, 2, "ste")
    rows += sample_nsk(3)
    with open(OUT.replace("xref_samples", "xref_samples_holdout"), "w", encoding="utf-8") as f:
        for i, r in enumerate(rows):
            r["id"] = 2000 + i
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(len(rows), file=sys.stderr)
