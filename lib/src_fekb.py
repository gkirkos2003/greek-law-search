"""ΦΕΚ Τεύχος Β΄ (ministerial / secondary acts) 2000–2026 source module.

iter_units() -> Unit(doc_id="fekB:<stem>", source="fek_b", fingerprint, load)
load()       -> (doc, chunks)          (DESIGN.md contract + extra chunk key "head")

Text: search/work/fekB_text/<year>.jsonl.gz shards written by tools/extract_fekB.py (plain
pdftotext reading order, 2000–2005 mojibake already fixed). The manifest holds the byte range
of every record, so iter_units only reads the manifest and load() decompresses one gzip member.
There is no index for Β΄: issue date, acts, act numbers, titles and issuing authorities are
derived from the text (see notes/fekb.md).
"""
from __future__ import annotations

import gzip
import json
import os
import re

from . import src_fek as A
from .src_fek import Unit

PARSER_VERSION = "1"
SOURCE = "fek_b"
BASE = A.BASE
PDFDIR = os.path.join(BASE, "seriesB")
TEXTDIR = os.path.join(A.SEARCH, "work", "fekB_text")
MANIFEST = os.path.join(TEXTDIR, "manifest.jsonl")
OK = ("ok", "ranges", "partial")


# --------------------------------------------------------------------------- units

def _manifest():
    rows = {}
    try:
        with open(MANIFEST, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                rows[r["stem"]] = r
    except FileNotFoundError:
        pass
    return rows


def iter_units(sources=None):
    """Only stems already extracted (manifest rows with a shard range). No stat() calls."""
    if sources and SOURCE not in sources:
        return
    for stem, r in sorted(_manifest().items()):
        if r.get("status") not in OK or not r.get("shard"):
            continue
        fp = f"v{PARSER_VERSION}|{r['src_size']}|{r['src_mtime_ns']}|{r['chars']}|{r['shard']}:{r['off']}"
        yield Unit(doc_id=f"fekB:{stem}", source=SOURCE, fingerprint=fp, load=_Loader(r))


class _Loader:
    __slots__ = ("row",)

    def __init__(self, row):
        self.row = row

    def __call__(self):
        return load_record(self.row)


def read_record(row):
    with open(os.path.join(TEXTDIR, row["shard"]), "rb") as fh:
        fh.seek(row["off"])
        blob = fh.read(row["len"])
    return json.loads(gzip.decompress(blob))


# --------------------------------------------------------------------------- patterns

_MONTHS = {"ΙΑΝΟΥΑΡΙΟΥ": 1, "ΦΕΒΡΟΥΑΡΙΟΥ": 2, "ΜΑΡΤΙΟΥ": 3, "ΑΠΡΙΛΙΟΥ": 4, "ΜΑΙΟΥ": 5,
           "ΙΟΥΝΙΟΥ": 6, "ΙΟΥΛΙΟΥ": 7, "ΑΥΓΟΥΣΤΟΥ": 8, "ΣΕΠΤΕΜΒΡΙΟΥ": 9, "ΟΚΤΩΒΡΙΟΥ": 10,
           "ΝΟΕΜΒΡΙΟΥ": 11, "ΔΕΚΕΜΒΡΙΟΥ": 12}
_MAST_DATE = re.compile(r"^(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s+((?:19|20)\d\d)$")
_RUN_DATE = re.compile(r"ΤΕΥΧΟΣ\s+Β\W{0,3}\s*(\d+)\s*/\s*(\d{1,2})\.(\d{1,2})\.(\d{4})")
_BOILER_B = [re.compile(p) for p in (
    r"^ΤΕΥΧΟΣ\s+(ΔΕΥΤΕΡΟΝ?|Β\W{0,2})\s*(\d+\s*/\s*\d{1,2}\.\d{1,2}\.\d{4})?\.?$",
    r"^\(?ΤΕΥΧΟΣ\s+ΔΕΥΤΕΡΟΝ?\)?$",
    r"^ΠΕΡΙΕΧΟΜΕΝΑ$", r"^Π\s?Ε\s?Ρ\s?Ι\s?Ε\s?Χ\s?Ο\s?Μ\s?Ε\s?Ν\s?Α$",
)]
_MARKER = re.compile(r"^\((\d{1,3})\)$")
_SECTION = re.compile(r"^(ΑΠΟΦΑΣΕΙΣ|ΑΠΟΦΑΣΗ|ΚΟΙΝΕΣ ΥΠΟΥΡΓΙΚΕΣ ΑΠΟΦΑΣΕΙΣ|ΚΟΙΝΗ ΥΠΟΥΡΓΙΚΗ ΑΠΟΦΑΣΗ|"
                      r"ΥΠΟΥΡΓΙΚΕΣ ΑΠΟΦΑΣΕΙΣ|ΔΙΟΡΘΩΣΕΙΣ ΣΦΑΛΜΑΤΩΝ|ΔΙΟΡΘΩΣΗ ΣΦΑΛΜΑΤΟΣ|ΕΓΚΥΚΛΙΟΙ|ΕΓΚΥΚΛΙΟΣ|"
                      r"ΠΡΑΞΕΙΣ|ΠΡΑΞΗ|ΚΑΝΟΝΙΣΜΟΙ|ΚΑΝΟΝΙΣΜΟΣ|ΑΝΑΚΟΙΝΩΣΕΙΣ|ΑΝΑΚΟΙΝΩΣΗ|ΓΝΩΜΟΔΟΤΗΣΕΙΣ|"
                      r"ΠΡΟΚΗΡΥΞΕΙΣ|ΠΡΟΚΗΡΥΞΗ|ΑΠΟΦΑΣΕΙΣ ΤΟΥ ΔΗΜΑΡΧΟΥ|ΚΑΤΑΣΤΑΤΙΚΑ|ΣΥΜΒΑΣΕΙΣ|ΟΡΓΑΝΙΣΜΟΙ)"
                      r"\.?$")
_NUM_LINE = re.compile(r"^(?:ΑΡΙΘΜ?\.?|ΑΡ\.\s*ΠΡΩΤ\.?|ΑΡΙΘ\.\s*ΠΡΩΤ\.?|ΑΡΙΘΜΟΣ)\s*:?\s*([^\s].{0,79}\d.{0,40}|\d.{0,80})$")
_NUM_INLINE = re.compile(r"(?:[υϋ]π[’'΄]?\s*αρ(?:ιθμ?)?\.?\s*(?:πρωτ\.\s*)?|[υϋ]πό\s+στοιχεία\s+)"
                         r"([0-9Α-Ωα-ωA-Za-z][^\s,]*(?:\s?/\s?[^\s,]+)*)")
_AUTH_START = re.compile(r"^(Ο|Η|ΤΟ|ΟΙ|ΤΑ|Τ Ο|Ο ΑΝΑΠΛΗΡΩΤΗΣ|Η ΑΝΑΠΛΗΡΩΤΡΙΑ)\s+[Α-ΩΆ-Ώ]")
_BODY_START = re.compile(r"^(Έχοντας|Εχοντας|Έχοντες|Λαμβάνοντας|Με την|Με τις|Με το|Με απόφαση|Με την υπ|"
                         r"Αφού|Σύμφωνα|1\.\s|Α\.\s|Άρθρο|Αρθρο)")


def _caps(s):
    return A._is_caps(s)


# --------------------------------------------------------------------------- reading

def clean_lines(text):
    """Issue text -> (content lines, issue date iso or None)."""
    pages = text.split("\f")
    out = []
    date = None
    npg = len(pages)
    for pi, page in enumerate(pages):
        cut_trailer = pi >= npg - 2
        for li, line in enumerate(page.split("\n")):
            s = A.clean_line(line, False)
            if not s:
                continue
            fu = A.fold_upper(s)
            if date is None:
                m = _RUN_DATE.search(fu)
                if m:
                    date = f"{int(m.group(4)):04d}-{int(m.group(3)):02d}-{int(m.group(2)):02d}"
                elif pi == 0 and li < 80:
                    m = _MAST_DATE.match(fu)
                    if m:
                        date = f"{m.group(3)}-{_MONTHS[m.group(2)]:02d}-{int(m.group(1)):02d}"
            if cut_trailer and A._TRAILER_RE.match(fu):
                break
            if len(s) <= 1 or A.is_boiler(fu, s) or any(r.match(fu) for r in _BOILER_B):
                continue
            if _RUN_DATE.search(fu) and len(fu) < 40:
                continue
            if pi == 0 and li < 40 and (fu.strip(" .,") in A._MAST_WORDS or _MAST_DATE.match(fu)):
                continue
            out.append(s)
    return out, date


# --------------------------------------------------------------------------- acts

def find_acts(lines):
    """-> list of (start, end) line ranges, one per act."""
    starts, want = [], 1
    for i, ln in enumerate(lines):
        m = _MARKER.match(ln)
        if m and int(m.group(1)) == want:
            starts.append(i)
            want += 1
    if len(starts) >= 2 or (starts and len(lines) - starts[0] > 5):
        # the act-number line may sit just before its marker ("Αριθμ. 23" / "(1)")
        adj, prev = [], -1
        for s in starts:
            k = s
            while k - 1 > prev and s - k < 5:
                ln = lines[k - 1]
                fu = A.fold_upper(ln)
                if _NUM_LINE.match(fu) and len(ln) < 100:
                    k -= 1
                    continue
                if _SECTION.match(fu) or _caps(ln) or _BODY_START.match(ln) or ln.rstrip().endswith(":") \
                        or A._SIG_START.match(ln) or len(ln) > 120:
                    break
                k -= 1
            adj.append(k)
            prev = s
        return [(a, b) for a, b in zip(adj, adj[1:] + [len(lines)])]
    # single act: starts at the first section heading / number line
    for i, ln in enumerate(lines):
        fu = A.fold_upper(ln)
        if _SECTION.match(fu) or (_NUM_LINE.match(fu) and len(ln) < 90):
            return [(i, len(lines))]
    return [(0, len(lines))] if lines else []


def parse_act_head(lines, a, b, section):
    """Number, title, authority, kind, body start of the act in lines[a:b]. The "(n)" marker can
    sit before, inside or after the number/title lines (reading order of the PDF)."""
    i, number, kind, tl, seen = a, None, section, [], False
    while i < b and i < a + 14:
        ln = lines[i]
        fu = A.fold_upper(ln)
        if _MARKER.match(ln):
            seen = True
            i += 1
            continue
        if _SECTION.match(fu):
            kind = ln.strip(" .")
            i += 1
            continue
        m = _NUM_LINE.match(fu)
        if m and number is None and len(ln) < 100:
            number = ln[len(ln) - len(m.group(1)):].strip(" .:")
            i += 1
            continue
        if _BODY_START.match(ln) or (_caps(ln) and (tl or seen)) or len(tl) >= 6:
            break
        tl.append(ln)
        i += 1
    title = " ".join(A.join_lines(tl)).lstrip("* ").strip() or None
    auth = []
    while i < b and len(auth) < 3:
        ln = lines[i]
        if _MARKER.match(ln):
            i += 1
            continue
        if not _caps(ln) or _SECTION.match(A.fold_upper(ln)):
            break
        auth.append(ln)
        i += 1
    authority = " ".join(auth) or None
    if number is None:
        # summary-style acts: "Με την υπ’ αριθμ. 30244/26−07−2013 απόφαση ..."
        m = _NUM_INLINE.search(" ".join(lines[i:i + 3]))
        if m:
            number = m.group(1).rstrip(".")
    if number and len(number) > 60:
        number = number[:60]
    return number, title, authority, kind, i


def build(lines, citation):
    acts_meta, chunks = [], []
    sections = [(i, ln.strip(" .")) for i, ln in enumerate(lines) if _SECTION.match(A.fold_upper(ln))]
    for k, (a, b) in enumerate(find_acts(lines)):
        section = None
        for i, name in sections:
            if i > a + 3:
                break
            section = name
        number, title, authority, kind, body0 = parse_act_head(lines, a, b, section)
        label = f"Αριθμ. {number}" if number else f"πράξη ({k + 1})"
        acts_meta.append({"number": number, "title": title, "authority": authority, "kind": kind})
        head = f"{citation} — {label}" + (f" — {title[:200]}" if title else "")
        body = [ln for ln in lines[body0:b] if not _MARKER.match(ln)]
        n0 = len(chunks)
        for art, disp, s, e in A.segment_articles(body, 0, len(body)):
            paras = A.join_lines(body[s:e])
            paras = A._strip_signatures(paras)
            if not paras:
                continue
            pieces = A.pack_paragraphs(paras)
            for pi, (piece, p0, p1) in enumerate(pieces):
                text = "\n".join(piece).strip()
                if len(text) < 30:
                    continue
                if art is not None:
                    loc = f"άρθρο {disp}"
                    if len(pieces) > 1:
                        loc += (f" §{max(p0, 1)}" + (f"–{p1}" if p1 != max(p0, 1) else "")) if p1 > 0 \
                            else f" (μέρος {pi + 1}/{len(pieces)})"
                else:
                    loc = "κείμενο" if len(pieces) == 1 else f"μέρος {pi + 1}/{len(pieces)}"
                chunks.append({"seq": len(chunks), "locator": f"{label} — {loc}", "law": None,
                               "article": art, "text": text, "head": head})
        if len(chunks) == n0 and title:     # act with no body text (e.g. title-only correction)
            chunks.append({"seq": len(chunks), "locator": f"{label} — τίτλος", "law": None,
                           "article": None, "text": title, "head": head})
    return acts_meta, chunks


# --------------------------------------------------------------------------- load

def load_record(row):
    rec = read_record(row)
    stem = rec["stem"]
    year, no = int(stem[:4]), int(stem[6:])
    lines, date = clean_lines(rec["text"])
    if date and not date.startswith(str(year)):
        date = None                      # masthead date of another year = misread
    cit = f"ΦΕΚ Β΄ {no}/{A._fmt_date(date)}" if date else f"ΦΕΚ Β΄ {no}/{year}"
    acts, chunks = build(lines, cit)
    titled = [a for a in acts if a["title"]]
    if len(acts) == 1 and titled:
        title = titled[0]["title"]
    elif titled:
        title = f"{titled[0]['title']} (+{len(acts) - 1} πράξεις)"
    else:
        title = f"{len(acts)} πράξεις" if acts else cit
    if title and len(title) > 300:
        title = title[:297] + "…"
    doc = {"doc_id": f"fekB:{stem}", "source": SOURCE, "citation": cit, "title": title,
           "date": date, "year": year, "number": str(no), "court": None,
           "path": os.path.join(PDFDIR, str(year), stem + ".pdf"), "ocr_conf": None,
           "meta": {"issue": f"Β {no}/{year}", "acts": acts, "pages": rec.get("pages"),
                    "fixed_mojibake": rec.get("fixed_mojibake"), "text_source": "pdftotext-reading-order",
                    "text_path": f"{os.path.join(TEXTDIR, row['shard'])}#{row['off']}", "parser": PARSER_VERSION}}
    return doc, chunks


def load_stem(stem):
    r = _manifest().get(stem)
    if not r or r.get("status") not in OK:
        raise KeyError(stem)
    return load_record(r)


if __name__ == "__main__":
    import sys
    for st in sys.argv[1:]:
        d, ch = load_stem(st)
        print(json.dumps(d, ensure_ascii=False, indent=1))
        for c in ch:
            print(f"--- [{c['seq']}] {c['locator']}  ({len(c['text'])} chars)")
            print("    head:", c["head"][:160])
            print(c["text"][:300])
