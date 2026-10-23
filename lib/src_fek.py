"""ΦΕΚ Τεύχος Α΄ (laws / decrees) source module.

iter_units() -> Unit(doc_id="fekA:<stem>", source="fek_a", fingerprint, load)
load()       -> (doc, chunks)          (see search/DESIGN.md for the dict contract)

Text sources, best first:
  2000+   search/work/fekA_text/<y>/<stem>.txt  plain `pdftotext` reading order (column-correct,
          de-hyphenated), made by tools/extract_fekA.py;  fallback ../text/<y>/<stem>.txt
          (`pdftotext -layout`, columns interleaved side by side).
  <2000   ../text/<y>/<stem>.txt PaddleOCR lines (two-column pages interleaved; see notes/fek.md
          for the de-interleaving evaluation).
Metadata: ../index.jsonl (one row per law/decree; several per issue), ../ocr_done.jsonl (conf).
Everything here is read-only on the corpus.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass
from typing import Callable

PARSER_VERSION = "10" if os.environ.get("FEK_ARTFIX", "1") != "0" else "9"          # bump when cleaning/chunking changes -> fingerprints change

BASE = os.environ.get("GREEKLAW_ROOT", "data")  # corpus root (ΦΕΚ PDFs/text, caselaw)
PDFDIR = os.path.join(BASE, "pdf")
TEXTDIR = os.path.join(BASE, "text")
INDEX = os.path.join(BASE, "index.jsonl")
OCRDONE = os.path.join(BASE, "ocr_done.jsonl")
SEARCH = os.path.join(BASE, "search")
FEKA_TEXT = os.path.join(SEARCH, "work", "fekA_text")

SOURCE = "fek_a"
TARGET = 1500
HARD_MAX = 2500
MIN_CHUNK = 400


@dataclass
class Unit:
    doc_id: str
    source: str
    fingerprint: str
    load: Callable


# --------------------------------------------------------------------------- iter_units

def _year_dirs(root):
    try:
        with os.scandir(root) as it:
            return sorted(e.name for e in it if e.is_dir() and e.name.isdigit())
    except FileNotFoundError:
        return []


def _list_txt(root, year):
    """{stem: path} from a directory listing only (no stat: stats are slow on the NTFS mount)."""
    out = {}
    try:
        with os.scandir(os.path.join(root, year)) as it:
            for e in it:
                n = e.name
                if n.endswith(".txt") and len(n) == 15 and n[:11].isdigit() and n[4:6] == "01":
                    out[n[:-4]] = e
    except FileNotFoundError:
        pass
    return out


def _jsonl_last(path, key, strip=0):
    rows = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                    rows[r[key][:len(r[key]) - strip]] = r
                except (ValueError, KeyError, TypeError):
                    continue
    except FileNotFoundError:
        pass
    return rows


def iter_units(sources=None):
    """One Unit per ΦΕΚ Α΄ issue that has text now.

    Cheap: directory listings + ../ocr_done.jsonl + work/fekA_text/manifest.jsonl; a stat() only
    for the few files that have no row in either (stat() costs ~ms each on the NTFS mount).
    Fingerprint = parser version + text kind + (OCR row | extraction row | size+mtime)."""
    if sources and SOURCE not in sources:
        return
    ocr_rows = _jsonl_last(OCRDONE, "pdf", strip=4)
    man = _jsonl_last(os.path.join(FEKA_TEXT, "manifest.jsonl"), "stem")
    years = sorted(set(_year_dirs(TEXTDIR)) | set(_year_dirs(FEKA_TEXT)))
    deint_tag = "d1" if (DEINT and os.path.exists(os.path.join(SEARCH, "work", "fek_deint_tables.npz"))) else "d0"
    for y in years:
        old = _list_txt(TEXTDIR, y)
        new = _list_txt(FEKA_TEXT, y) if int(y) >= 2000 else {}
        for stem in sorted(set(old) | set(new)):
            r = man.get(stem)
            if stem in new and r and r.get("status") in ("ok", "ranges", "partial"):
                e, kind = new[stem], "pdf"
                fp = f"v{PARSER_VERSION}|pdf|{r['src_size']}|{r['src_mtime_ns']}|{r['chars']}"
            elif stem in old:
                e = old[stem]
                kind = "layout" if int(y) >= 2000 else "ocr"
                o = ocr_rows.get(stem) if kind == "ocr" else None
                if o:
                    fp = f"v{PARSER_VERSION}|ocr|{o.get('chars')}|{o.get('pages')}|{o.get('conf')}|{deint_tag}"
                else:
                    st = e.stat()
                    if st.st_size < 40:
                        continue
                    fp = f"v{PARSER_VERSION}|{kind}|{st.st_size}|{st.st_mtime_ns}"
            else:
                continue
            yield Unit(doc_id=f"fekA:{stem}", source=SOURCE, fingerprint=fp,
                       load=_Loader(stem, y, e.path, kind))


class _Loader:
    __slots__ = ("stem", "year", "path", "kind")

    def __init__(self, stem, year, path, kind):
        self.stem, self.year, self.path, self.kind = stem, year, path, kind

    def __call__(self):
        return load_issue(self.stem, self.path, self.kind)


# --------------------------------------------------------------------------- metadata (lazy)

_META = None
_CONF = None

CATALOGUE = {   # _catalogue_name -> (abbr for labels, xref kind code, header kind)
    "Nomos": ("ν.", "ν", "N"),
    "Proedriko_Diatagma": ("π.δ.", "πδ", "PD"),
    "Vasiliko_Diatagma": ("β.δ.", "βδ", "VD"),
    "Nomothetiko_Diatagma": ("ν.δ.", "νδ", "ND"),
    "Anagkastikos_Nomos": ("α.ν.", "αν", "AN"),
    "Praxi_Nomothetikou_Periexomenou": ("ΠΝΠ", "πνπ", "PNP"),
    "Nomothetiko_Proedriko_Diatagma": ("ν.π.δ.", "νπδ", "NPD"),
}
HKIND = {v[2]: (v[0], v[1], k) for k, v in CATALOGUE.items()}


def _issue_meta():
    """{(year:int, issue_no:int): {"date": iso, "laws": [..]}} from index.jsonl (~2 s, ~60 MB)."""
    global _META
    if _META is not None:
        return _META
    meta = {}
    seen = set()
    with open(INDEX, encoding="utf-8") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("search_IssueGroupID") != "1":
                continue
            d = r.get("search_IssueDate") or ""
            try:
                mm, dd, yy = d[:10].split("/")
                year, iso = int(yy), f"{yy}-{mm}-{dd}"
                no = int(r["search_DocumentNumber"])
            except (ValueError, KeyError):
                continue
            num = (r.get("search_LawProtocolNumber") or "").strip()
            if not num.isdigit():
                num = None
            desc = (r.get("search_Description") or "").strip()
            cat = r.get("_catalogue_name") or ""
            dkey = (year, no, cat, num, desc)
            if dkey in seen:
                continue
            seen.add(dkey)
            m = meta.setdefault((year, no), {"date": iso, "pages": r.get("search_Pages"), "laws": []})
            abbr, kcode, _ = CATALOGUE.get(cat, ("πράξη", None, None))
            if num:
                label = f"{abbr} {num}/{year}"
                key = f"{kcode}:{num}/{year}" if kcode else None
            else:   # unnumbered (most royal decrees before 1960): issue + position in the issue
                label = f"{abbr} ΦΕΚ Α΄ {no}/{year} ({len(m['laws']) + 1})"
                key = None
            m["laws"].append({"label": label, "key": key, "kind": kcode, "catalogue": cat,
                              "number": num, "year": year, "title": desc})
    _META = meta
    return meta


def _ocr_conf():
    global _CONF
    if _CONF is not None:
        return _CONF
    c = {}
    try:
        with open(OCRDONE, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                    c[r["pdf"][:-4]] = r.get("conf")
                except (ValueError, KeyError, TypeError):
                    continue
    except FileNotFoundError:
        pass
    _CONF = c
    return c


# --------------------------------------------------------------------------- character cleaning

_GREEK = re.compile(r"[Ͱ-Ͽἀ-῿]")
_LAT1 = re.compile(r"[¢´-¾À-ÿ]")
_COMB = re.compile(r"[̀-ͯ]")

# glyph substitutions seen in the ΦΕΚ PDFs / OCR
_CHARMAP = str.maketrans({
    "∆": "Δ", "µ": "μ", "Ω": "Ω", "−": "-", "‐": "-", "‑": "-",
    "­": "", "​": "", "﻿": "", " ": " ", " ": " ", " ": " ",
    " ": " ", "ϲ": "σ", ";": ";",
})
LAT2GR_UP = str.maketrans("ABEHIKMNOPTXYZ", "ΑΒΕΗΙΚΜΝΟΡΤΧΥΖ")
LAT2GR_LO = str.maketrans("aoxvuikp", "αοχνυικρ")
LAT_LOOK_UP = set("ABEHIKMNOPTXYZ")
LAT_LOOK_LO = set("aoxvuikp")


def _legacy_fix_token(tok):
    out = []
    for ch in tok:
        try:
            out.append(ch.encode("cp1252").decode("cp1253"))
        except (UnicodeEncodeError, UnicodeDecodeError):
            out.append(ch)
    return "".join(out)


def fix_legacy_encoding(text):
    """2000-2005 PDFs with Greek fonts mapped through WinAnsi: '¸÷ïíôáò õðüøç' -> 'Έχοντας υπόψη'.
    Applied per whitespace token, only when the document has many such tokens."""
    toks = text.split()
    bad = sum(1 for t in toks if _LAT1.search(t) and not _GREEK.search(t))
    if bad < 10 or bad < 0.02 * max(len(toks), 1):
        return text
    return re.sub(r"\S+", lambda m: _legacy_fix_token(m.group(0))
                  if (_LAT1.search(m.group(0)) and not _GREEK.search(m.group(0))) else m.group(0), text)


def _fix_mixed_token(tok, ocr):
    """Latin look-alike letters inside Greek words (NOMOΣ, TΗΣ, ΙΑNΟΥΑΡΙΟΥ); in OCR text also
    all-Latin look-alike tokens on Greek lines (IANOYAPIOY) — the caller decides via `ocr`."""
    if not _GREEK.search(tok):
        return tok
    letters = [c for c in tok if c.isalpha()]
    lat = [c for c in letters if c.isascii()]
    if not lat:
        return tok
    if all(c in LAT_LOOK_UP or c in LAT_LOOK_LO for c in lat):
        return tok.translate(LAT2GR_UP).translate(LAT2GR_LO)
    return tok


_ZERO_IN_NUM = re.compile(r"(?<=\d)[οΟoO](?=\d)|(?<=\d\d)[οΟoO](?=[/.]\d)|(?<=[/.]\d)[οΟoO](?=\d)")
_SLASH_PAREN = re.compile(r"\b(\d{1,5})\)((?:18[3-9]|19\d|20[0-3])\d)\b")   # 1671)1951 -> 1671/1951


def clean_line(line, ocr):
    s = line.translate(_CHARMAP)
    s = unicodedata.normalize("NFC", s)
    if "ο" in s or "o" in s or "O" in s or "Ο" in s:
        for _ in range(2):
            s = _ZERO_IN_NUM.sub("0", s)
    if ocr:
        s = _SLASH_PAREN.sub(r"\1/\2", s)
        s = s.replace("το5 ", "του ").replace("τε5 ", "του ")
    if re.search(r"[A-Za-z]", s) and _GREEK.search(s):
        toks = s.split(" ")
        greek_line = None
        for i, t in enumerate(toks):
            if not t:
                continue
            if _GREEK.search(t):
                toks[i] = _fix_mixed_token(t, ocr)
            elif ocr and len(t) >= 2:
                core = t.strip(".,;:·()«»\"'")
                if len(core) >= 2 and core.isalpha() and all(c in LAT_LOOK_UP for c in core):
                    if greek_line is None:
                        g = len(_GREEK.findall(s))
                        a = len(re.findall(r"[A-Za-z]", s))
                        greek_line = g > 2 * a
                    if greek_line:
                        toks[i] = t.translate(LAT2GR_UP)
        s = " ".join(toks)
    s = re.sub(r"[ \t]+", " ", s).strip()
    return s


def fold_upper(s):
    """Accent-free uppercase Greek with Latin look-alikes mapped (for pattern matching only)."""
    s = unicodedata.normalize("NFD", s)
    s = _COMB.sub("", s).upper()
    return s.translate(LAT2GR_UP)


def fold_lower(s):
    s = unicodedata.normalize("NFD", s)
    return _COMB.sub("", s).lower().replace("ς", "σ")


# --------------------------------------------------------------------------- boilerplate

_BOILER_RE = [re.compile(p) for p in (
    r"^ΕΦΗΜΕΡΙ[ΣΔΣ]?\w{0,2}\s*(ΤΗΣ\s*)?(ΚΥΒΕΡΝΗΣΕΩΣ)?\s*(\(?ΤΕΥΧΟΣ\s+\w+\)?)?\s*\d*$",
    r"^\d{1,5}\s+ΕΦΗΜΕΡΙ\w+\s+ΤΗΣ\s+ΚΥΒΕΡΝ\w+.{0,40}$",
    r"^ΕΦΗ[ΜΝ]ΕΡ\w*\s+ΤΗΣ\s+ΚΥΒΕΡ\w*.{0,40}$",
    r"^ΤΗΣ\s+ΚΥΒΕΡΝΗΣΕΩΣ$",
    r"^ΤΕΥΧΟΣ\s+(ΠΡΩΤΟΝ?|Α\W{0,2})\s*(\d+\s*/\s*\d{1,2}\.\d{1,2}\.\d{4})?\.?$",
    r"^ΑΡ(ΙΘ(ΜΟΣ)?)?\.?\s*ΦΥΛΛΟΥ\s*\d*$",
    r"^ΑΡΙΘΜΟΣ\s+ΦΥΛΛΟΥ\s*\d*$",
    r"^\d{1,5}$",
    r"^[-–—_.·•*\s]{1,}$",
    r"^[ΕE]$",
    r"^\*\d{10,}\*$",
)]
_TRAILER_RE = re.compile(r"^(ΕΘΝΙΚΟ ΤΥΠΟΓΡΑΦΕΙΟ\b|ΚΑΠΟΔΙΣΤΡΙΟΥ 34\b|.*WWW\.ET\.GR|ΤΙΜΗ ΠΩΛΗΣΗΣ ΦΥΛΛΩΝ)")
_MAST_WORDS = {"ΕΦΗΜΕΡΙΣ", "ΕΦΗΜΕΡΙΔΑ", "ΤΗΣ", "ΚΥΒΕΡΝΗΣΕΩΣ", "ΕΛΛΗΝΙΚΗΣ", "ΔΗΜΟΚΡΑΤΙΑΣ",
               "ΤΟΥ", "ΒΑΣΙΛΕΙΟΥ", "ΕΛΛΑΔΟΣ", "ΕΝ", "ΑΘΗΝΑΙΣ", "ΑΘΗΝΑ", "ΑΘΗΝΑΙ"}


_SIGN_RE = re.compile(r"^(SIGNATURE NOT|SIGNATURE VALID|VERIFIED$|DIGITALLY SIGNED|DATE: \d{4}\.|REASON:|"
                      r"LOCATION:|TYPOGRAFIO$|EEST$|EET$|\(EMBEDDED\)$|SIGNED PDF|ATHENS, ETHNIKO)")


def is_boiler(fu, line=""):
    if len(fu) > 90:
        return False
    if line and _SIGN_RE.match(line.upper()):
        return True
    for k, r in enumerate(_BOILER_RE):
        if r.match(fu):
            # masthead / running-header rules: only all-caps lines (body text may start with
            # "Εφημερίδα της Κυβερνήσεως, ..."); "Τεύχος Α’ 94/27.05.2016" is mixed case but exact
            if k in _CAPS_ONLY and line and not _is_caps(line):
                return False
            return True
    return False


_CAPS_ONLY = {0, 1, 2, 3}


# --------------------------------------------------------------------------- structure patterns

_APOS = r"[\'’΄`´ʹ‘\"]?"
_ARITH = r"ΑΡΙΘ\w{0,4}\.?"
_NUMTOK = r"([0-9]{1,5}|[Α-ΩϚϞϠ͵]{1,6}" + _APOS + r")"
_LAW_HDR = [   # (regex on folded upper line, header kind)
    (re.compile(r"^ΝΟΜΟΘΕΤΙΚΟΝ?\s+ΠΡΟΕΔΡΙΚΟΝ?\s+ΔΙΑΤΑΓΜΑ(?:\s+ΥΠ" + _APOS + r"\s*" + _ARITH + r"\s*" + _NUMTOK + r")?"), "NPD"),
    (re.compile(r"^ΝΟΜΟΘΕΤΙΚΟΝ?\s+ΔΙΑΤΑΓΜΑ(?:\s+ΥΠ" + _APOS + r"\s*" + _ARITH + r"\s*" + _NUMTOK + r")?"), "ND"),
    (re.compile(r"^ΠΡΟΕΔΡΙΚΟΝ?\s+ΔΙΑΤΑΓΜΑ(?:\s+ΥΠ" + _APOS + r"\s*" + _ARITH + r"\s*" + _NUMTOK + r")?"), "PD"),
    (re.compile(r"^ΒΑΣΙΛΙΚΟΝ?\s+ΔΙΑΤΑΓΜΑ(?:\s+ΥΠ" + _APOS + r"\s*" + _ARITH + r"\s*" + _NUMTOK + r")?"), "VD"),
    (re.compile(r"^ΑΝΑΓΚΑΣΤΙΚΟΣ\s+ΝΟΜΟΣ(?:\s+ΥΠ" + _APOS + r"\s*" + _ARITH + r"\s*" + _NUMTOK + r")?"), "AN"),
    (re.compile(r"^ΠΡΑΞΗ\s+ΝΟΜΟΘΕΤΙΚΟΥ\s+ΠΕΡΙΕΧΟΜΕΝΟΥ"), "PNP"),
    (re.compile(r"^ΝΟΜΟΣ\s+ΥΠ" + _APOS + r"\s*" + _ARITH + r"\s*" + _NUMTOK), "N"),
    (re.compile(r"^ΝΟΜΟΣ\s+(?:" + _ARITH + r"\s*)?([0-9]{1,5})\.?$"), "N"),
    (re.compile(r"^ΔΙΑΤΑΓΜΑ\.?$"), "D"),
]
_ORD_UNITS = {"ΠΡΩΤ": 1, "ΔΕΥΤΕΡ": 2, "ΤΡΙΤ": 3, "ΤΕΤΑΡΤ": 4, "ΠΕΜΠΤ": 5, "ΕΚΤ": 6, "ΕΒΔΟΜ": 7,
              "ΟΓΔΟ": 8, "ΕΝΑΤ": 9, "ΕΝΝΑΤ": 9}
_ORD_TENS = {"ΔΕΚΑΤ": 10, "ΕΝΔΕΚΑΤ": 11, "ΔΩΔΕΚΑΤ": 12, "ΕΙΚΟΣΤ": 20, "ΤΡΙΑΚΟΣΤ": 30,
             "ΤΕΣΣΑΡΑΚΟΣΤ": 40, "ΠΕΝΤΗΚΟΣΤ": 50, "ΕΞΗΚΟΣΤ": 60, "ΕΒΔΟΜΗΚΟΣΤ": 70,
             "ΟΓΔΟΗΚΟΣΤ": 80, "ΕΝΕΝΗΚΟΣΤ": 90, "ΕΚΑΤΟΣΤ": 100}
_ORD_END = r"(?:Ο|ΟΝ|Η|ΗΝ|Α)"
_ORD_WORD = "(?:" + "|".join(sorted(list(_ORD_UNITS) + list(_ORD_TENS), key=len, reverse=True)) + ")" + _ORD_END
_ART_RE = re.compile(
    r"^[\'’΄`´ʹ‘\"]?\s*ΑΡ[ΘO9]Ρ(?:[ΟΥO]Ν?\s+|\.\s*)"
    r"(?:(?P<rom>[ΙVΧ]{1,6})\.?(?=\s|$)"
    r"|(?P<num>\d{1,3})(?:(?P<suf>[Α-Ω]{1,2})(?![Α-Ω])|\s(?P<suf2>[Α-Ω])(?=\s*[.:\-–]?\s*$))?"
    r"|(?P<ord>" + _ORD_WORD + r"(?:\s+" + _ORD_WORD + r"){0,2})"
    r"|(?P<only>ΜΟΝΟΝ?))"
    r"(?P<rest>.*)$")
_GR_NUM = {c: v for c, v in zip("ΑΒΓΔΕϚΖΗΘ", range(1, 10))}
_GR_NUM.update({"Σ": 200, "ϛ": 6, "Ϛ": 6, "Ι": 10, "Κ": 20, "Λ": 30, "Μ": 40, "Ν": 50, "Ξ": 60,
                "Ο": 70, "Π": 80, "Ϟ": 90, "Ϙ": 90, "Ρ": 100, "Τ": 300, "Υ": 400, "Φ": 500,
                "Χ": 600, "Ψ": 700, "Ω": 800, "Ϡ": 900})


def greek_numeral(tok):
    """'ΡΜΗ΄' -> 148 (old laws were numbered with Greek numerals). None if not parseable."""
    t = re.sub(r"[\'’΄`´ʹ‘\"]", "", tok)
    if not t:
        return None
    if t.isdigit():
        return int(t)
    total, mult = 0, 1
    for ch in t:
        if ch == "͵":
            mult = 1000
            continue
        v = _GR_NUM.get(ch)
        if v is None:
            return None
        total += v * mult
        mult = 1
    return total or None


def roman_value(tok):
    t = tok.replace("Ι", "I").replace("Χ", "X")
    vals = {"I": 1, "V": 5, "X": 10}
    total, prev = 0, 0
    for ch in reversed(t):
        v = vals.get(ch)
        if v is None:
            return None
        total += -v if v < prev else v
        prev = max(prev, v)
    return total if 0 < total < 60 else None


def ordinal_value(words):
    total = 0
    for w in words.split():
        stem = re.sub(_ORD_END + "$", "", w)
        for table in (_ORD_TENS, _ORD_UNITS):
            if stem in table:
                total += table[stem]
                break
        else:
            return None
    return total or None


def match_law_header(fu):
    if len(fu) > 70:
        return None
    for rx, kind in _LAW_HDR:
        m = rx.match(fu)
        if m:
            num = None
            if m.groups() and m.group(1):
                num = greek_numeral(m.group(1))
            rest = fu[m.end():].strip(" .:")
            if kind == "N" and num is None:
                continue
            # TOC lines like "ΝΟΜΟΣ 3288. Περί ..." are long; real headers are short
            if len(rest) > 25 and not rest.startswith("("):
                continue
            return kind, (str(num) if num else None)
    return None


_LOWER_START = re.compile(r"^[a-zα-ωάέήίόύώϊϋΐΰ]")


def match_article(line, fu):
    """Returns (article_id, display, rest) or None. `line` original, `fu` folded upper."""
    if len(fu) > 140:
        return None
    first = line.lstrip("'’΄`´ʹ‘\" ")[:1]
    if first not in "ΆΑA":
        return None
    m = _ART_RE.match(fu)
    if not m:
        return None
    rest = m.group("rest")
    rest_stripped = rest.strip()
    if m.group("rom"):
        v = roman_value(m.group("rom"))
        if not v:
            return None
        art = disp = str(v)
    elif m.group("num"):
        suf = m.group("suf") or m.group("suf2") or ""
        if suf in ("Ο", "ΟΝ"):          # "Άρθρον 12ον" = ordinal ending, not 12Ο
            suf = ""
        art = m.group("num") + suf
        disp = art
    elif m.group("ord"):
        v = ordinal_value(m.group("ord"))
        if not v:
            return None
        art = str(v)
        disp = line.split(None, 1)[1][:len(m.group("ord")) + 3].split(":")[0].strip(" .") if " " in line else art
        disp = " ".join(disp.split()[:len(m.group("ord").split())])
    else:
        art, disp = "μόνο", "μόνο"
    # "Άρθρο 5 του ν. ..." = sentence, not a heading
    orig_rest = line[len(line) - len(rest):] if len(rest) <= len(line) else rest
    rs = orig_rest.strip()
    if rs[:1] in (".", ":", "-", "–", "—"):
        rs = rs[1:].strip()
    if rs and _LOWER_START.match(rs):
        return None
    if rs.startswith("(") is False and re.match(r"^(ΤΟΥ|ΤΗΣ|ΠΑΡ|ΚΑΙ|ΤΩΝ|ΤΟ|ΑΝΤΙ)\b", rest_stripped.lstrip(".:-– ")) \
            and not rs[:1].isupper():
        return None
    return art, disp, rs


def _art_sortkey(art):
    m = re.match(r"(\d+)(.*)", art)
    return (int(m.group(1)), m.group(2)) if m else (None, art)


# --------------------------------------------------------------------------- reading the text

def _pages(raw, kind):
    if kind in ("pdf", "layout"):
        pages = raw.split("\f")
    else:   # OCR: pages joined by a blank line, lines by \n
        pages = re.split(r"\n\s*\n", raw)
    return [p.split("\n") for p in pages]


_RUNHEAD_LAYOUT = re.compile(r"ΕΦΗΜΕΡΙ\w*\s+ΤΗΣ\s+ΚΥΒΕΡΝΗΣΕΩΣ.*ΤΕΥΧΟΣ|ΤΕΥΧΟΣ.*ΑΡ\.\s*ΦΥΛΛΟΥ")


def _layout_to_columns(lines):
    """Fallback for `pdftotext -layout` text: split each page at the column gutter
    (a column of spaces shared by most long lines) and emit left column then right column."""
    lines = [l for l in lines if not (_RUNHEAD_LAYOUT.search(fold_upper(l)) or _SIGN_RE.match(l.strip().upper()))]
    longl = [l for l in lines if len(l) > 60]
    if len(longl) < 8:
        return lines
    width = max(len(l) for l in longl)
    best, best_score = None, 0
    for c in range(int(width * 0.35), int(width * 0.65)):
        sc = sum(1 for l in longl if len(l) > c + 1 and l[c] == " " and l[c - 1] == " " or len(l) <= c)
        if sc > best_score:
            best, best_score = c, sc
    if best is None or best_score < 0.8 * len(longl):
        return lines
    left, right = [], []
    for l in lines:
        left.append(l[:best])
        right.append(l[best:])
    return left + right


ARTFIX = os.environ.get("FEK_ARTFIX", "1") != "0"  # v10: inline headings + heading resync
_INLINE_ART = re.compile(
    r"(?<=[.;:·»)\]])\s+(?=[\'’΄`´ʹ‘*]?\s*(?:[ΆΑA]ρ[θ9]ρον?|ΑΡ[ΘO9]ΡΟΝ?)\s+\d{1,3}[Α-Ω]?\.?"
    r"(?:\s*$|\s+[(\[«Α-ΩΆΈΉΊΌΎΏ]))")


def split_inline_articles(lines):
    """'... αλλοδαπή. 'Αρθρο 42 (άρθρο 42 ν.δ. 170/1973) Η αρχή ...' -> heading starts its own line."""
    out = []
    for ln in lines:
        if len(ln) > 30 and ("ρθρ" in ln or "ΡΘΡ" in ln or "ρ9ρ" in ln or "Ρ9Ρ" in ln):
            out.extend(p for p in _INLINE_ART.split(ln) if p.strip())
        else:
            out.append(ln)
    return out


DEINT = os.environ.get("FEK_DEINT", "1") != "0"   # FEK_DEINT=0 disables; trade-off in notes/fek.md


def read_lines(path, kind, year, info=None):
    """-> cleaned content lines of the issue (reading order). `info` (dict) gets stats."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        raw = fh.read()
    ocr = kind == "ocr"
    if not ocr and year >= 2000:
        raw = fix_legacy_encoding(raw)
    pages = _pages(raw, kind)
    out = []
    npg = len(pages)
    err_pages = deint_pages = 0
    tabs = None
    if ocr and DEINT:
        from . import fek_deint
        tabs = fek_deint.tables()
    for pi, page in enumerate(pages):
        if kind == "layout":
            page = _layout_to_columns(page)
        cut_trailer = pi >= npg - 2
        keep = []
        for li, line in enumerate(page):
            if line.startswith("[[OCR ERROR"):
                err_pages += 1
                continue
            s = clean_line(line, ocr)
            if not s:
                continue
            fu = fold_upper(s)
            if cut_trailer and not ocr and _TRAILER_RE.match(fu):
                break
            if is_boiler(fu, s):
                continue
            if li < 25 and pi == 0 and fu.strip(" .,") in _MAST_WORDS:
                continue
            if ocr and li < 12 and re.match(r"^ΕΝ\s*'?ΑΘΗΝΑΙΣ\W*$", fu):
                continue
            keep.append(s)
        if tabs is not None:
            keep, fired = fek_deint.deinterleave(tabs, keep)
            deint_pages += fired
        if ARTFIX:
            keep = split_inline_articles(keep)
        out.extend(keep)
    if info is not None:
        info.update(pages=npg, ocr_error_pages=err_pages, deint_pages=deint_pages)
    return out


# --------------------------------------------------------------------------- line joining

_HYPH_END = re.compile(r"(\w)[-‐](\s*)$")
_PARA_START = re.compile(r"^(\d{1,3}[.)]\s|[α-ωa-z]{1,2}[.)]\s|\(\w{1,4}\)\s|[ivxIVX]{1,4}[.)]\s|[-–•]\s|"
                         r"ΚΕΦΑΛΑΙΟ|ΜΕΡΟΣ|ΤΜΗΜΑ|ΤΙΤΛΟΣ|ΒΙΒΛΙΟ|Άρθρο|Αρθρο|ΑΡΘΡΟ)")


def join_lines(lines):
    """Lines -> paragraphs (list of str). De-hyphenates line ends, joins wrapped lines."""
    paras, cur = [], ""
    for ln in lines:
        if not cur:
            cur = ln
            continue
        m = _HYPH_END.search(cur)
        if m:
            nxt0 = ln[:1]
            prev_word = re.search(r"(\w+)[-‐]\s*$", cur)
            pw = prev_word.group(1) if prev_word else ""
            if nxt0.islower() or (pw.isupper() and ln.split(" ")[0].rstrip(".,;:").isupper()):
                cur = cur[:m.start(1) + 1] + ln
                continue
            cur = cur.rstrip() + ln
            continue
        if _PARA_START.match(ln) or (cur[-1:] in ".:;»" and ln[:1].isupper() and len(cur) > 1):
            paras.append(cur)
            cur = ln
        else:
            cur = cur + " " + ln
    if cur:
        paras.append(cur)
    return [re.sub(r"\s+", " ", p).strip() for p in paras if p.strip()]


# --------------------------------------------------------------------------- segmentation

def _tokens(s):
    return [w for w in re.findall(r"\w{3,}", fold_lower(s)) if not w.isdigit()]


def _title_score(desc, lines):
    want = _tokens(desc)[:10]
    if not want:
        return 0.0
    have = set(_tokens(" ".join(lines)))
    return sum(1 for w in want if w in have) / len(want)


def _title_anchors(lines, laws):
    """Locate each act's index title in the text (fallback when no headers are printed / OCR'd).
    Window of 3 lines (OCR interleaving), title's first 10 content words; the TOC lists the
    same titles first, so take the LAST good occurrence. -> [(line_idx, law_idx)] sorted."""
    toks = [set(_tokens(l)) for l in lines]
    leader = [bool(re.search(r"\.\s?\.\s?\.|…|\.{2,}\s*\d+$", l)) for l in lines]
    found = {}
    for li, law in enumerate(laws):
        want = _tokens(law["title"] or "")[:10]
        if len(want) < 2:
            continue
        best, best_i = 0.0, None
        for i in range(len(lines)):
            if not toks[i] or want[0] not in toks[i] and (len(want) < 2 or want[1] not in toks[i]):
                continue
            have = toks[i] | (toks[i + 1] if i + 1 < len(lines) else set()) | \
                (toks[i + 2] if i + 2 < len(lines) else set())
            if any(leader[i:i + 3]):          # table-of-contents line (dot leaders)
                continue
            sc = sum(1 for w in want if w in have) / len(want)
            if sc >= 0.6 and sc >= best - 0.1:
                best, best_i = max(best, sc), i
        if best_i is not None and best_i not in found:
            found[best_i] = li
    return sorted(found.items())


def _marker_headers(lines):
    """Old issues number their acts "(1)", "(2)", ... on a line of their own. Take the first
    increasing run 1, 2, 3 ... as act boundaries (kind unknown -> 'D')."""
    out, want = [], 1
    for i, ln in enumerate(lines):
        m = re.match(r"^\((\d{1,2})\)$", ln.strip())
        if m and int(m.group(1)) == want:
            out.append((i, "D", None))
            want += 1
    return out


def segment_laws(lines, laws, year, issue_no):
    """Split the issue's lines into law segments. Returns list of (law_dict|None, start, end)."""
    hdrs = []
    for i, ln in enumerate(lines):
        if len(ln) > 70 or ln[:1] in "«\"“":
            continue
        fu = fold_upper(ln)
        h = match_law_header(fu)
        if h:
            letters = [c for c in ln if c.isalpha()]
            upper = sum(1 for c in letters if c.isupper()) / max(1, len(letters))
            if upper < 0.75 and not (h[0] == "N" and re.match(r"^Ν[όο]μος\s+\d", ln)):
                continue
            hdrs.append((i, h[0], h[1]))
    forced = {}
    if len(hdrs) < 2 and len(laws) >= 2:
        alt = _marker_headers(lines)
        tit = _title_anchors(lines, laws)
        if len(tit) >= max(2, len(alt)):
            hdrs = [(i, "T", None) for i, _ in tit]
            forced = {i: li for i, li in tit}
        elif len(alt) >= 2:
            hdrs = alt
    # numbered header for an index law -> keep; dedupe (TOC repeats): keep the occurrence with more text
    assigned = {}
    used = set()
    segs = []
    for j, (i, kind, num) in enumerate(hdrs):
        end = hdrs[j + 1][0] if j + 1 < len(hdrs) else len(lines)
        segs.append([i, end, kind, num, None])
    # drop D/unnumbered headers immediately following a numbered one of same act (e.g. "ΔΙΑΤΑΓΜΑ" banner)
    # match to index
    for seg in segs:
        i, end, kind, num, _ = seg
        if i in forced and forced[i] not in used:
            used.add(forced[i])
            seg[4] = laws[forced[i]]
            continue
        cands = []
        for li, law in enumerate(laws):
            if li in used:
                continue
            hk = CATALOGUE.get(law["catalogue"], (None, None, None))[2]
            if num and law["number"] == num:
                cands.append((3 + (hk == kind), li))
            elif not num and not law["number"]:
                compat = (hk == kind) or (kind == "D" and hk in ("VD", "PD", "ND")) \
                    or (kind == "VD" and hk == "PD") or (kind == "PD" and hk == "VD")
                if compat:
                    sc = _title_score(law["title"], lines[i + 1:min(end, i + 8)])
                    cands.append((1 + sc, li))
            elif not num and law["number"] and (hk == kind or kind == "D"):
                # header number lost (OCR) -> title match only
                sc = _title_score(law["title"], lines[i + 1:min(end, i + 8)])
                if sc >= 0.5:
                    cands.append((1 + sc, li))
        if cands:
            cands.sort(key=lambda x: (-x[0], x[1]))
            best = cands[0][1]
            used.add(best)
            seg[4] = laws[best]
        else:
            abbr = HKIND.get(kind, ("δ.",))[0] if kind != "D" else "δ."
            kcode = HKIND.get(kind, (None, None))[1] if kind != "D" else None
            if num:
                seg[4] = {"label": f"{abbr} {num}/{year}", "key": f"{kcode}:{num}/{year}" if kcode else None,
                          "kind": kcode, "number": num, "year": year, "title": None, "catalogue": None,
                          "from_text": True}
            else:
                seg[4] = None
    # an unmatched header that has nothing of its own merges into the previous segment
    merged = []
    for seg in segs:
        if seg[4] is None and merged:
            merged[-1][1] = seg[1]
        else:
            merged.append(seg)
    out = []
    first = merged[0][0] if merged else len(lines)
    if first > 0:
        out.append((laws[0] if len(laws) == 1 else None, 0, first, "front"))
    for i, end, kind, num, law in merged:
        out.append((law, i, end, "law"))
    if len(laws) == 1 and len(out) >= 1:
        # single-act issue: anything unassigned belongs to it
        out = [(o[0] or laws[0], o[1], o[2], o[3]) for o in out]
    return out


def segment_articles(lines, start, end):
    """-> list of (article_id|None, display|None, s, e) inside [start, end)."""
    cands = []
    for i in range(start, end):
        ln = lines[i]
        if ln[:1] in "«“":
            continue
        m = match_article(ln, fold_upper(ln))
        if m:
            cands.append((m[0], m[1], i, _art_sortkey(m[0])[0]))
    arts = []
    cur = None          # current numeric value
    for k, (art, disp, i, n) in enumerate(cands):
        ok = True
        if n is not None and cur is not None:
            ok = (n == 1) or (cur < n <= cur + 3) or (n == cur and art != arts[-1][0])
            if not ok and ARTFIX and n > cur:
                # resync after missed headings: accept a jump if the next headings continue it
                nxt = [c[3] for c in cands[k + 1:k + 4] if c[3] is not None]
                if not any(cur < m <= cur + 3 for m in nxt):     # normal sequence resumes soon -> skip
                    ok = (n <= cur + 10 and nxt[:1] == [n + 1]) or (nxt[:2] == [n + 1, n + 2])
        if ok:
            arts.append((art, disp, i))
            if n is not None:
                cur = n
    res = []
    if not arts:
        return [(None, None, start, end)]
    if arts[0][2] > start:
        res.append((None, "pre", start, arts[0][2]))
    for k, (art, disp, i) in enumerate(arts):
        e = arts[k + 1][2] if k + 1 < len(arts) else end
        res.append((art, disp, i, e))
    return res


# --------------------------------------------------------------------------- chunking

_SIG_START = re.compile(r"^(Αθήνα|Αθήναι|Εν Αθήναις|Έν Αθήναις|Ἐν Ἀθήναις|'Εν 'Αθήναις|Εν 'Αθήναις|Αθήνησι)\b,?\s*"
                        r"(,|τη|τήν|την|\d)")


def _split_long(text, limit=HARD_MAX):
    """Split one over-long paragraph on sentence boundaries, then on spaces."""
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for sent in re.split(r"(?<=[.;·])\s+(?=[«\"(]?[Α-ΩΆ-ΏA-Z0-9])", text):
        if len(cur) + len(sent) + 1 <= TARGET or not cur:
            cur = (cur + " " + sent).strip()
        else:
            parts.append(cur)
            cur = sent
    if cur:
        parts.append(cur)
    out = []
    for p in parts:
        while len(p) > limit:
            cut = p.rfind(" ", 0, TARGET)
            if cut < TARGET // 2:
                cut = TARGET
            out.append(p[:cut].strip())
            p = p[cut:].strip()
        if p:
            out.append(p)
    return out


def pack_paragraphs(paras):
    """Greedy pack paragraphs into pieces of ~TARGET chars (hard max HARD_MAX).
    Returns [(texts, first_par, last_par)] where par = top-level paragraph number in effect
    (numbered "k." paragraphs count only when k = previous + 1, so restarting lists don't)."""
    units, cur_par = [], 0
    for p in paras:
        m = re.match(r"^(\d{1,3})\.\s", p)
        if m and int(m.group(1)) == cur_par + 1:
            cur_par += 1
        for part in _split_long(p):
            units.append((part, cur_par))
    pieces, cur = [], []
    size = 0
    for u in units:
        if cur and size + len(u[0]) + 1 > TARGET:
            pieces.append(cur)
            cur, size = [], 0
        cur.append(u)
        size += len(u[0]) + 1
    if cur:
        if pieces and size < MIN_CHUNK and sum(len(x[0]) + 1 for x in pieces[-1]) + size <= HARD_MAX:
            pieces[-1].extend(cur)
        else:
            pieces.append(cur)
    return [([u[0] for u in pc], pc[0][1], pc[-1][1]) for pc in pieces]


def _strip_signatures(paras):
    """Drop the closing date/signature block of an act (short tail starting 'Αθήνα, ...')."""
    for k in range(len(paras) - 1, max(-1, len(paras) - 8), -1):
        if _SIG_START.match(paras[k]):
            tail = sum(len(p) for p in paras[k:])
            if tail < 1500:
                return paras[:k]
            break
    return paras


def build_chunks(lines, laws, year, issue_no):
    chunks = []
    toc_done = False
    for law, s, e, what in segment_laws(lines, laws, year, issue_no):
        label = law["label"] if law else None
        arts = segment_articles(lines, s, e)
        # table-of-contents detection: >=3 consecutive thin article segments
        body = [sum(len(x) for x in lines[a[2] + 1:a[3]] if not _is_caps(x)) for a in arts]
        thin = [a[0] is not None and body[k] < 150 for k, a in enumerate(arts)]
        toc_idx = set()
        # numbering that restarts at 1 after a run of mostly-thin entries = table of contents
        starts = [k for k, a in enumerate(arts) if a[0] is not None and _art_sortkey(a[0])[0] == 1]
        for s0, s1 in zip(starts, starts[1:]):
            run = sorted(body[s0:s1])
            if s1 - s0 >= 3 and run[len(run) // 2] < 250:
                toc_idx.update(range(s0, s1))
        k = 0
        while k < len(arts):
            if thin[k]:
                j = k
                while j < len(arts) and thin[j]:
                    j += 1
                if j - k >= 3:
                    toc_idx.update(range(k, j))
                k = j
            else:
                k += 1
        toc_lines = []
        for ai, (art, disp, a, b) in enumerate(arts):
            if ai in toc_idx:
                toc_lines.extend(lines[a:b])
                continue
            if toc_lines:
                _emit_toc(chunks, toc_lines, label, toc_done)
                toc_done, toc_lines = True, []
            paras = join_lines(lines[a:b])
            if ai == len(arts) - 1:
                paras = _strip_signatures(paras)
            if not paras:
                continue
            if art is None:
                kind = "προοίμιο" if (what == "law" and disp == "pre") else \
                       ("αρχή φύλλου" if what == "front" else None)
            pieces = pack_paragraphs(paras)
            for pi, (piece, p0, p1) in enumerate(pieces):
                text = "\n".join(piece).strip()
                if art is None and (len(text) < 30 or (what == "front" and len(text) < 200)):
                    continue
                if art is not None:
                    loc = f"άρθρο {disp}"
                    if len(pieces) > 1:
                        if p1 > 0:
                            p0 = max(p0, 1)
                            loc += f" §{p0}" + (f"–{p1}" if p1 != p0 else "")
                        else:
                            loc += f" (μέρος {pi + 1}/{len(pieces)})"
                else:
                    loc = kind or "κείμενο"
                    if len(pieces) > 1:
                        loc += f" (μέρος {pi + 1}/{len(pieces)})"
                if label:
                    loc = f"{label} — {loc}"
                chunks.append({"seq": len(chunks), "locator": loc, "law": label,
                               "article": art, "text": text})
        if toc_lines:
            _emit_toc(chunks, toc_lines, label, toc_done)
            toc_done = True
    return chunks


def _is_caps(line):
    letters = [c for c in line if c.isalpha()]
    return len(letters) >= 4 and sum(1 for c in letters if c.isupper()) >= 0.9 * len(letters)


def _emit_toc(chunks, toc_lines, label, toc_done):
    if toc_done:
        return
    text = " ".join(toc_lines)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > TARGET:
        cut = text.rfind(" ", 0, TARGET)
        text = text[:cut] + " …"
    loc = (f"{label} — " if label else "") + "περιεχόμενα"
    chunks.append({"seq": len(chunks), "locator": loc, "law": label, "article": None, "text": text})


# --------------------------------------------------------------------------- load

def _fmt_date(iso):
    if not iso:
        return None
    y, m, d = iso.split("-")
    return f"{d}.{m}.{y}"


def load_issue(stem, path, kind):
    year, no = int(stem[:4]), int(stem[6:])
    im = _issue_meta().get((year, no)) or {"date": None, "laws": [], "pages": None}
    laws = im["laws"]
    info = {}
    lines = read_lines(path, kind, year, info)
    chunks = build_chunks(lines, laws, year, no)
    if not chunks and laws:
        # no usable text (e.g. OCR failed on every page): keep the issue findable by its acts' titles
        text = "\n".join(f"{l['label']}: {l['title']}" for l in laws if l["title"])
        if text:
            chunks = [{"seq": 0, "locator": "τίτλοι πράξεων (χωρίς κείμενο)", "law": None,
                       "article": None, "text": text[:HARD_MAX]}]
    date = im["date"]
    citation = f"ΦΕΚ Α΄ {no}/{_fmt_date(date)}" if date else f"ΦΕΚ Α΄ {no}/{year}"
    if len(laws) == 1:
        title = f"{laws[0]['label']}: {laws[0]['title']}" if laws[0]["title"] else laws[0]["label"]
    elif laws:
        title = "; ".join(l["label"] for l in laws[:4]) + (f" (+{len(laws) - 4})" if len(laws) > 4 else "")
        if laws[0]["title"]:
            title += f" — {laws[0]['title']}"
    else:
        title = citation
    conf = _ocr_conf().get(stem) if kind == "ocr" else None
    meta = {
        "issue": f"Α {no}/{year}",
        "laws": [{k: l[k] for k in ("label", "key", "kind", "number", "year", "title")} for l in laws],
        "pages": int(im["pages"]) if str(im.get("pages") or "").isdigit() else None,
        "text_source": {"pdf": "pdftotext-reading-order", "layout": "pdftotext-layout",
                        "ocr": "paddleocr"}[kind],
        "lang": "el+de" if year < 1850 else "el",
        "text_path": path,
        "ocr_error_pages": info.get("ocr_error_pages") or 0,
        "deinterleaved_pages": info.get("deint_pages") or 0,
        "parser": PARSER_VERSION,
    }
    doc = {"doc_id": f"fekA:{stem}", "source": SOURCE, "citation": citation, "title": title,
           "date": date, "year": year, "number": str(no), "court": None,
           "path": os.path.join(PDFDIR, str(year), stem + ".pdf"), "ocr_conf": conf, "meta": meta}
    return doc, chunks


def load_stem(stem):
    """Convenience for debugging: load one issue by stem with the best available text."""
    y = stem[:4]
    p = os.path.join(FEKA_TEXT, y, stem + ".txt")
    if int(y) >= 2000 and os.path.exists(p):
        return load_issue(stem, p, "pdf")
    p = os.path.join(TEXTDIR, y, stem + ".txt")
    return load_issue(stem, p, "layout" if int(y) >= 2000 else "ocr")


if __name__ == "__main__":
    import sys
    for st in sys.argv[1:]:
        d, ch = load_stem(st)
        print(json.dumps(d, ensure_ascii=False, indent=1))
        for c in ch:
            print(f"--- [{c['seq']}] {c['locator']}  ({len(c['text'])} chars)")
            print(c["text"][:400])
