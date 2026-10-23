"""Greek legal citation + amendment extraction (agent xref).

Public API
----------
extract(text, self_law=None, doc_year=None, include_self=False) -> list[dict]
    One dict per cited provision:
      kind       ν | κν | πδ | νδ | αν | βδ | νπδ | πνπ | κωδ | Σ | eu | fek
      number     "4412" (str) or None for date-identified acts / codes
      year       int or None
      key        canonical id (see law_key), e.g. "ν:4412/2016", "βδ:1953-06-03",
                 "κωδ:ΑΚ", "Σ" (1975; "Σ:1952" older), "eu:dir:2014/24", "eu:reg:2016/679",
                 "eu:ΣΛΕΕ", "fekA:2016/188" (standalone ΦΕΚ refs with a year only)
      article    "146", "5Α", "5-8" (range) or None
      paragraph  "2" / "1,2" or None
      raw        original text of the citation (article phrase .. act phrase)
      start,end  char offsets into `text` (into NFC(text) if text had combining marks);
                 line-end hyphenation is joined internally, offsets are mapped back
      relation   amends | replaces | repeals | adds | cites
      fek        "fekA:2016/147" when the act is followed by "(Α΄ 147)" etc., else None
      date       ISO date for date-identified acts, else None
    self_law: key of the act the text belongs to; citations of that key and
    "του παρόντος" are dropped (unless include_self=True, then kept with relation "self").
    doc_year: publication year of the document, disambiguates 2-digit years.

law_key(kind, number=None, year=None, date=None) -> str
act_key(key) -> join key (κν:N/Y -> ν:N/Y)
parse_nsk_provision("Ν 3463/2006Α146") -> {"key": "ν:3463/2006", "article": "146", ...} or None
extract_nsk(provisions_list) -> list[dict]   (compact format first, free text fallback)
resolve_fek(key) -> list[{label, stem, date, title}]
build_tables()  -> builds search/work/law_to_fek.json.gz + law_ranges.json from ../index.jsonl
fold(text)      -> same-length accent/case/lookalike folded copy (used internally)
"""
from __future__ import annotations

import bisect
import datetime as _dt
import gzip
import json
import os
import re
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
SEARCH_ROOT = os.path.dirname(HERE)
CORPUS_ROOT = os.path.dirname(SEARCH_ROOT)
WORK = os.path.join(SEARCH_ROOT, "work")
INDEX_JSONL = os.path.join(CORPUS_ROOT, "index.jsonl")
TABLE_PATH = os.path.join(WORK, "law_to_fek.json.gz")
RANGES_PATH = os.path.join(WORK, "law_ranges.json")
CUR_YEAR = _dt.date.today().year

# --------------------------------------------------------------------------------------
# folding: same length, lowercase, no accents, ς->σ, Latin lookalikes -> Greek
# --------------------------------------------------------------------------------------


def _build_fold():
    t = {}
    for cp in list(range(0x0370, 0x0400)) + list(range(0x1F00, 0x2000)):
        ch = chr(cp)
        base = "".join(c for c in unicodedata.normalize("NFD", ch) if not unicodedata.combining(c))
        if len(base) == 1:
            low = base.lower()
            if len(low) == 1 and low != ch:
                t[cp] = low
    t[ord("ς")] = "σ"
    t[ord("Σ")] = "σ"
    t[ord("ϲ")] = "σ"
    latin = {"A": "α", "B": "β", "E": "ε", "H": "η", "I": "ι", "K": "κ", "M": "μ", "N": "ν",
             "O": "ο", "P": "ρ", "T": "τ", "X": "χ", "Y": "υ", "Z": "ζ",
             "a": "α", "o": "ο", "e": "ε", "i": "ι", "k": "κ", "v": "ν", "u": "υ", "p": "ρ",
             "t": "τ", "x": "χ", "y": "υ", "n": "η", "m": "μ", "z": "ζ", "b": "β"}
    for k, v in latin.items():
        t[ord(k)] = v
    for ch in "’‘´`΄ʼ′ʹ᾽᾿":
        t[ord(ch)] = "'"
    for ch in "“”„″":
        t[ord(ch)] = '"'
    for ch in "‐‑‒–—―−":
        t[ord(ch)] = "-"
    for ch in "∕⁄":
        t[ord(ch)] = "/"
    t[0x00A0] = " "
    t[0x2009] = " "
    t[0x202F] = " "
    t[ord("∆")] = "δ"
    t[ord("Ω")] = "ω"
    t[ord("µ")] = "μ"
    t[ord("ϐ")] = "β"
    t[ord("ϑ")] = "θ"
    t[ord("·")] = "."  # ano teleia is mostly a sentence separator in our texts
    t[ord("·")] = "."
    t[ord(";")] = ";"  # Greek question mark
    for cp in range(ord("A"), ord("Z") + 1):
        t.setdefault(cp, chr(cp).lower())
    return t


_FOLD = _build_fold()
_COMBINING = re.compile(r"[̀-ͯ]")


def fold(s: str) -> str:
    return s.translate(_FOLD)


# --------------------------------------------------------------------------------------
# kinds / keys
# --------------------------------------------------------------------------------------
KIND_ALIASES = {
    "ν": "ν", "n": "ν", "νομος": "ν", "law": "ν",
    "κν": "κν", "πδ": "πδ", "νδ": "νδ", "αν": "αν", "βδ": "βδ", "νπδ": "νπδ", "πνπ": "πνπ",
    "κωδ": "κωδ", "σ": "Σ", "Σ": "Σ", "eu": "eu", "fek": "fek",
}
CATALOGUE_KIND = {
    "Nomos": "ν", "Proedriko_Diatagma": "πδ", "Nomothetiko_Diatagma": "νδ",
    "Anagkastikos_Nomos": "αν", "Vasiliko_Diatagma": "βδ",
    "Praxi_Nomothetikou_Periexomenou": "πνπ", "Nomothetiko_Proedriko_Diatagma": "νπδ",
}


_CANON_KINDS = {"ν", "κν", "πδ", "νδ", "αν", "βδ", "νπδ", "πνπ", "κωδ", "Σ", "eu", "fek"}


def _norm_kind(kind):
    if kind in _CANON_KINDS:
        return kind
    k = re.sub(r"[\s.]", "", fold(kind or ""))
    return KIND_ALIASES.get(k, kind)


def act_key(key):
    """Join key for cross-reference tables: κ.ν. 2190/1920 and ν. 2190/1920 are the same act."""
    return "ν:" + key[3:] if key and key.startswith("κν:") else key


def law_key(kind, number=None, year=None, date=None):
    """Canonical key. Number/year acts: "ν:4412/2016". Date-identified: "βδ:1953-06-03".
    Codes: law_key("κωδ", "ΑΚ") -> "κωδ:ΑΚ". Constitution: "Σ" (or "Σ:1952" for older ones)."""
    kind = _norm_kind(kind)
    if kind == "κωδ":
        return "κωδ:" + str(number)
    if kind == "Σ":
        return "Σ" if not year or int(year) == 1975 else "Σ:%d" % int(year)
    if kind == "eu":
        return "eu:%s" % number  # number already "dir:2014/24"
    if kind == "fek":
        return str(number)
    if number not in (None, ""):
        n = str(number).lstrip("0") or "0"
        return "%s:%s/%d" % (kind, n, int(year))
    if date:
        if isinstance(date, (tuple, list)):
            date = "%04d-%02d-%02d" % tuple(date)
        return "%s:%s" % (kind, date)
    raise ValueError("law_key needs number+year or date")


# --------------------------------------------------------------------------------------
# year helpers (2-digit years)
# --------------------------------------------------------------------------------------
_RANGES = None
_RANGES_YEARS = None


def _ranges():
    global _RANGES, _RANGES_YEARS
    if _RANGES is None:
        try:
            with open(RANGES_PATH, encoding="utf-8") as f:
                raw = json.load(f)
            _RANGES_YEARS = raw.pop("_years", {})
            _RANGES = {k: {int(y): v for y, v in d.items()} for k, d in raw.items()}
        except Exception:
            _RANGES, _RANGES_YEARS = {}, {}
    return _RANGES


def _expand_year(yy, kind=None, number=None, doc_year=None):
    """2-digit -> 4-digit using per-year number ranges from the ΦΕΚ index."""
    yy = int(yy)
    maxy = doc_year if doc_year else CUR_YEAR
    cands = [c for c in (2000 + yy, 1900 + yy, 1800 + yy) if c <= maxy]
    if not cands:
        cands = [1900 + yy]
    if kind and number and str(number).isdigit():
        rk = _ranges().get("ν" if kind == "κν" else kind)
        if rk:
            n = int(number)
            for c in cands:  # most recent first
                r = rk.get(c)
                if r and r[0] - 5 <= n <= r[1] + 5:
                    return c
    return cands[0]


def _year(ystr, kind=None, number=None, doc_year=None):
    if ystr is None:
        return None
    ystr = ystr.replace("ο", "0").replace("o", "0")
    if len(ystr) == 4:
        y = int(ystr)
        if 1821 <= y <= CUR_YEAR + 1:
            return y
        return None
    if len(ystr) == 2:
        return _expand_year(ystr, kind, number, doc_year)
    return None


MONTHS = {
    "ιανουαρ": 1, "φεβρουαρ": 2, "μαρτ": 3, "απριλ": 4, "μαι": 5, "μαΐ": 5, "ιουν": 6,
    "ιουλ": 7, "αυγουστ": 8, "σεπτεμβρ": 9, "οκτωβρ": 10, "νοεμβρ": 11, "δεκεμβρ": 12,
}
_MONTH_RE = r"(?:ιανουαρ|φεβρουαρ|μαρτ|απριλ|μαι|ιουν|ιουλ|αυγουστ|σεπτεμβρ|οκτωβρ|νοεμβρ|δεκεμβρ)[α-ω]*"


_TODAY = _dt.date.today()


def _mkdate(y, m, d):
    try:
        dd = _dt.date(y, m, d)
    except Exception:
        return None
    if dd > _TODAY and y >= 2000:
        try:
            dd = _dt.date(y - 100, m, d)  # "14.12.26" in an old text = 1926
        except Exception:
            return None
    return dd.isoformat()


def _parse_numdate(s, doc_year=None):
    """'3/5.6.1953' -> (sign, pub); '24.10/10.11.1958'; '31-8/18-9-1935'; '10-1-1974';
    '2.10.52'; '2-8/1927'; '10/20-11-1939'. Returns (sign_iso, pub_iso) or (None, None)."""
    m = re.fullmatch(r"(\d{1,2})(?:/(\d{1,2}))?[.\-](\d{1,2})(?:/(\d{1,2})[.\-](\d{1,2}))?[.\-/](\d{4}|\d{2})", s)
    if not m:
        m2 = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
        if m2:
            return _mkdate(int(m2.group(3)), int(m2.group(2)), int(m2.group(1))), None
        return None, None
    d1, d1b, m1, d2, m2, y = m.groups()
    y = int(y)
    if y < 100:
        y = _expand_year(y, doc_year=doc_year)
    if d2:  # d.m/d.m.y
        return _mkdate(y, int(m1), int(d1)), _mkdate(y, int(m2), int(d2))
    if d1b:  # d/d.m.y
        a, b = int(d1), int(d1b)
        return _mkdate(y, int(m1), a), _mkdate(y, int(m1), b)
    return _mkdate(y, int(m1), int(d1)), None


def _parse_textdate(s):
    m = re.match(r"(\d{1,2})(?:ησ|ης|η)?\s+(" + _MONTH_RE + r")\s+(\d{4})", s)
    if not m:
        return None
    mon = None
    for k, v in MONTHS.items():
        if m.group(2).startswith(k):
            mon = v
            break
    return _mkdate(int(m.group(3)), mon, int(m.group(1))) if mon else None


# --------------------------------------------------------------------------------------
# patterns (all on folded text)
# --------------------------------------------------------------------------------------
NB = r"(?<![α-ωa-z0-9])"          # no letter/digit before
NA = r"(?![α-ωa-z])"              # no letter after
DOT = r"\s?\.?\s?"
NUM = r"\d(?:[\dο]{0,4}\d)?"     # tolerate OCR 'Ο' inside numbers
SEPY = r"(?:\s?/\s?|\)(?:[ ]|\n(?=\d{4}))?)"
YR = r"(?:\d{4}|\d{2}|\d[\dο]{2}\d)(?![\d])"
NUMDATE = r"(?:\d{1,2}(?:/\d{1,2})?[.\-]\d{1,2}(?:/\d{1,2}[.\-]\d{1,2})?[.\-/](?:\d{4}|\d{2})|\d{1,2}/\d{1,2}/(?:1[89]\d\d|20\d\d))(?![\d])"
TEXTDATE = r"\d{1,2}(?:ησ|ης|η)?\s+" + _MONTH_RE + r"\s+\d{4}"

# act keywords -> kind  (order matters: longer / more specific first)
_KW = [
    ("νπδ", r"ν\.\s?π\.\s?δ\.(?!\s?δ)|νομοθετικ\w*\s+προεδρικ\w*\s+διαταγμ\w*"),
    ("πνπ", r"π\.\s?ν\.\s?π\.?|πνπ|πραξ\w*\s+νομοθετικου\s+περιεχομενου"),
    ("κν", r"κ\.\s?ν\.?|κν(?=\s?\d)|κωδικοποιητικ\w*\s+νομ\w*"),
    ("αν", r"α\.\s?ν\.?|αν\.?(?=\s?\d)|αναγκ(?:αστικ\w*|\.)\s+νομ\w*|αν\.\s+νομ\w*"),
    ("νδ", r"ν\.\s?δ(?:\.|/τ\w*|/γμ\w*|(?=\s?\d)|\b)|νδ(?=\s?\d)|νομοθετικ\w*\s+διαταγμ\w*|νομοθ\.\s*διατ\w*\.?"),
    ("πδ", r"π\.\s?δ(?:\.|/τ\w*|/γ?μ\w*|(?=\s?\d))|πδ(?=\s?\d)|πδ/τ\w*|προεδρικ\w*\s+διαταγμ\w*|προεδρ\.\s*διατ\w*\.?"),
    ("βδ", r"β\.\s?4\.(?=\s?\d)|β\.\s?δ(?:\.|/τ\w*|/γμ\w*|(?=\s?\d))|βδ(?=\s?\d)|βασιλικ\w*\s+διαταγμ\w*|β\.\s*διατ\w*\.?"),
    ("ν", r"ν\.?(?=\s?\d)|ν\.|νομ(?:ου|οσ|ον|ο|\.)" + NA),
]
_KW_RE = "|".join("(?P<k_%s>%s)" % (i, p) for i, (_, p) in enumerate(_KW))
_KW_KIND = {"k_%d" % i: k for i, (k, _) in enumerate(_KW)}

# codes: abbreviation -> canonical
_CODES = [
    ("ΚΠολΔ", r"κ\.?\s?πολ\.?\s?δ(?:ικ)?\.?|κ[ωο]δικ\w*\s+πολιτικ\w*\s+δικονομ\w*"),
    ("ΕισΝΚΠολΔ", r"εισν\.?\s?κ\.?\s?πολ\.?\s?δ\.?"),
    ("ΚΠΔ", r"κ\.?\s?π\.?\s?δ\.?|κ\.?\s?ποιν\.?\s?δ(?:ικ)?\.?|κ[ωο]δικ\w*\s+ποινικ\w*\s+δικονομ\w*"),
    ("ΚΔΔιαδ", r"κ\.?\s?δ\.?\s?διαδ\.?|κ[ωο]δικ\w*\s+διοικητικ\w*\s+διαδικασ\w*"),
    ("ΚΔΔ", r"κ\.?\s?δ\.?\s?δ\.?|κ\.?\s?διοικ\.?\s?δ(?:ικ)?\.?|κ[ωο]δικ\w*\s+διοικητικ\w*\s+δικονομ\w*"),
    ("ΚΕΔΕ", r"κ\.?\s?ε\.?\s?δ\.?\s?ε\.?|κ[ωο]δικ\w*\s+εισπραξ\w*\s+δημοσιων\s+εσοδων"),
    ("ΚΦΕ", r"κ\.?\s?φ\.?\s?ε\.?|κ[ωο]δικ\w*\s+φορολογιασ\s+εισοδηματοσ"),
    ("ΚΦΔ", r"κ\.?\s?φ\.?\s?δ\.?|κ[ωο]δικ\w*\s+φορολογικ\w*\s+διαδικασ\w*"),
    ("ΚΔΚ", r"κ\.?\s?δ\.?\s?κ\.?|κ[ωο]δικ\w*\s+δημων\s+και\s+κοινοτητων"),
    ("ΚΟΚ", r"κ\.?\s?ο\.?\s?κ\.?|κ[ωο]δικ\w*\s+οδικ\w*\s+κυκλοφορι\w*"),
    ("ΕισΝΑΚ", r"εισν\.?\s?α\.?\s?κ\.?|εισαγωγικ\w*\s+νομ\w*\s+(?:του\s+)?αστικ\w*\s+κ[ωο]δικ\w*"),
    ("ΑΚ", r"α\.?\s?κ\.?|αστικ\w*\s+κ[ωο]δικ\w*"),
    ("ΠΚ", r"π\.?\s?κ\.?|ποιν\.?\s?κ(?:ωδ)?\.?|ποινικ\w*\s+κ[ωο]δικ\w*"),
    ("ΥΚ", r"υ\.?\s?κ\.?|υπαλληλικ\w*\s+κ[ωο]δικ\w*"),
    ("ΕμπΝ", r"εμπ\.?\s?ν\.?|εμπορικ\w*\s+νομ\w*"),
    ("ΕΣΔΑ", r"ε\.?\s?σ\.?\s?δ\.?\s?α\.?|ευρωπαικ\w*\s+συμβασ\w*\s+(?:για\s+τα\s+|των\s+)?δικαιωματων\s+του\s+ανθρωπου"),
    ("ΣΛΕΕ", r"σ\.?\s?λ\.?\s?ε\.?\s?ε\.?|συνθηκ\w*\s+για\s+τη\s+λειτουργια\s+τησ\s+ευρωπαικησ\s+ενωσησ"),
    ("ΣΕΕ", r"σ\.?\s?ε\.?\s?ε\.?(?=\s|[,.;)]|$)|συνθηκ\w*\s+για\s+την\s+ευρωπαικη\s+ενωση"),
    ("Σ", r"συνταγματ\w*|συντ\.|σ(?=[\s.,;)]|$)"),
]
_EU_TREATIES = {"ΣΛΕΕ", "ΣΕΕ"}
_CODE_RE = "|".join("(?P<c_%d>%s)" % (i, p) for i, (_, p) in enumerate(_CODES))
_CODE_NAME = {"c_%d" % i: k for i, (k, _) in enumerate(_CODES)}
# abbreviations that need a following/preceding article number (too short to trust alone)
_EUSFX = r"(?:ε\.?\s?ε\.?|ε\.?\s?κ\.?|ε\.?\s?ο\.?\s?κ\.?|ευρατομ|ευ|εκ|εc|εεc|κεππα|δευ)"

ANCHOR = re.compile(
    NB + r"(?:"
    # ΦΕΚ references
    r"(?P<fek>φ\.?\s?ε\.?\s?κ\.?)\s*(?:"
    r"(?:τ\.?|τευχ\w*\.?)?\s*(?P<fs1>ααπ|υοδδ|ασεπ|νπδδ|α|β|γ|δ)'{0,2}\.?\s*(?:αρ\.?\s*|φ\.?\s*)?(?P<fn1>\d{1,5})(?:\s?/\s?(?P<fd1>" + NUMDATE + r"|\d{4}))?"
    r"|(?P<fn2>\d{1,5})\s?/\s?(?:τ\.?\s?|τευχ\w*\s?)?(?P<fs2>ααπ|υοδδ|ασεπ|νπδδ|α|β|γ|δ)'{0,2}\.?(?:\s?/\s?(?P<fd2>" + NUMDATE + r"|\d{4}))?"
    r"|(?P<fn3>\d{1,5})(?:\s?/\s?(?P<fd3>" + NUMDATE + r"|\d{4}))?\s*(?:τ\.?|τευχ\w*\.?)?\s*(?P<fs3>ααπ|υοδδ|ασεπ|νπδδ|α|β|γ|δ)'{0,2}" + NA +
    r")"
    # EU acts with keyword
    r"|(?P<eukw>οδηγι\w*|οδηγ\.|κανονισμ\w*|καν\.|αποφασ\w*|αποφ\.)\s*(?:(?:του\s+συμβουλιου|του\s+ευρωπαικου\s+κοινοβουλιου(?:\s+και\s+του\s+συμβουλιου)?|τησ\s+επιτροπησ)\s+)?"
    r"(?:\((?P<eup>εε|εκ|εοκ|ευρατομ|ευ|εc)\)\s*)?(?:(?:αριθμ?|αρ)\.?\s*)?(?P<eua>\d{1,4})\s?/\s?(?P<eub>\d{1,4})(?:\s?/\s?(?P<eus>" + _EUSFX + r"))?" + NA +
    # EU acts without keyword: 2004/18/ΕΚ
    r"|(?P<eua2>\d{2,4})/(?P<eub2>\d{1,4})/(?P<eus2>" + _EUSFX + r")" + NA +
    # reversed: 4412/2016 νόμου ;  υπ' αριθ. 4412/2016 νόμου
    r"|(?P<rn>" + NUM + r")" + SEPY + r"(?P<ry>" + YR + r")\s+(?P<rkw>νομου|νομοσ|νομο|νομον|ν\.?\s?δ\.?(?:/τ\w*)?|ν\.|νομοθετικ\w*\s+διαταγμ\w*|π\.?\s?δ\.?(?:/τ\w*)?|προεδρικ\w*\s+διαταγμ\w*|β\.?\s?δ\.?|α\.?\s?ν\.?)" + NA +
    # date first: το από 2.10.52 Β.Δ.
    r"|(?P<dd>" + NUMDATE + r"|" + TEXTDATE + r")\s+(?P<dkw>β\.?\s?[δ34]\.?(?:/τ\w*)?|βασιλικ\w*\s+διαταγμ\w*|ν\.?\s?δ\.?(?:/τ\w*)?|νομοθετικ\w*\s+διαταγμ\w*|π\.?\s?δ\.?(?:/τ\w*)?|προεδρικ\w*\s+διαταγμ\w*|π\.?\s?ν\.?\s?π\.?|πραξ\w*\s+νομοθετικου\s+περιεχομενου)" + NA +
    # keyword acts
    r"|(?P<kw>" + _KW_RE + r")\s*(?:(?:υπ'?\s?)?αριθ(?:μ)?\.?\s*)?(?:"
    r"(?P<n>" + NUM + r")" + SEPY + r"(?P<y>" + YR + r")(?!\s?/\s?\d)(?!\s+(?:ιανου|φεβρ|μαρτ|απριλ|μαι|ιουν|ιουλ|αυγου|σεπτ|οκτω|νοεμ|δεκεμ))(?:α(?P<cart>\d+[α-ζ]?)(?:π(?P<cpar>\d+))?)?"
    r"|(?P<n2>\d{1,5})\s+(?:απο|τησ)\s+(?P<nd2>" + NUMDATE + r"|" + TEXTDATE + r")"
    r"|(?:τησ\s+|απο\s+)?(?P<d>" + NUMDATE + r"|" + TEXTDATE + r")"
    r")"
    # anaphora: του ίδιου νόμου / κώδικα
    r"|(?P<ana>(?:ιδιου|ιδιασ|αυτου|αυτησ)\s+(?:ωσ\s+ανω\s+)?(?P<anakw>νομου|ν\.|κωδικα|κωδ\.|διαταγματοσ|π\.\s?δ\.|ν\.\s?δ\.|β\.\s?δ\.)(?!\s?\d)|(?:ωσ\s+ανω\s+|ανωτερω\s+|προαναφερθεντοσ\s+|ιδιου\s+ωσ\s+ανω\s+)(?P<anakw2>νομου|κωδικα|διαταγματοσ)(?!\s?\d)|νομου\s+αυτου|κωδικα\s+αυτου)"
    # codes / constitution
    r"|(?P<code>" + _CODE_RE + r")" + NA +
    r")"
)

# literal prefixes of every ANCHOR alternative: an all-literal alternation lets sre skip
# non-candidate positions in C (tests/test_xref.py checks equivalence with ANCHOR.finditer)
_CAND_PREFIXES = (
    ["φε", "φ.", "φ ", "οδηγ", "κανον", "καν.", "αποφ", "ν.", "ν ", "νο", "νδ", "π.", "π ", "πδ", "πν",
     "πραξ", "προεδρ", "προαν", "πκ", "ποιν", "κ.", "κ ", "κν", "κπ", "κδ", "κε", "κφ", "κοκ", "κωδ",
     "κοδ", "α.", "α ", "αν", "ακ", "αστικ", "αυτου", "αυτησ", "β.", "β ", "βδ", "βασιλ", "ιδιου",
     "ιδιασ", "ωσ ", "υ.", "υ ", "υκ", "υπαλλ", "εμπ", "ε.", "ε ", "εσδ", "ευρωπ", "εισν", "εισαγ",
     "σ.", "σ ", "σ,", "σ;", "σ)", "σ\n", "σλ", "συνθ", "συντ"]
    + ["ν%d" % d for d in range(10)] + ["κ%d" % d for d in range(10)] + ["α%d" % d for d in range(10)]
    + ["π%d" % d for d in range(10)] + ["β%d" % d for d in range(10)] + [str(d) for d in range(10)]
)
_CAND = re.compile("|".join(re.escape(x) for x in sorted(set(_CAND_PREFIXES), key=len, reverse=True)))

ARTWORD = re.compile(NB + r"(?:[αδκ]ρ[θ90]ρ(?:ο|ου|α|ων|ον|ω|\.)?|αρθ\.|αρ\.|αρθρα|αρθρων)(?![α-ω])\s*")
_ORD = [("ενδεκατ", 11), ("δωδεκατ", 12), ("δεκατ", 10), ("πρωτ", 1), ("δευτερ", 2), ("τριτ", 3),
        ("τεταρτ", 4), ("πεμπτ", 5), ("εκτ", 6), ("εβδομ", 7), ("ογδο", 8), ("ενν?ατ", 9),
        ("εικοστ", 20), ("τριακοστ", 30)]
_ORD_RE = "(?:" + "|".join(p for p, _ in _ORD) + r")(?:ο|ου|ον|α|ων|οσ|η|ησ)(?![α-ω])"
TOK = re.compile(
    r"\s*(?:"
    r"(?P<num>\d{1,4}(?:[α-θι](?![α-ω'])|\s?-\s?[α-θι](?![α-ω]))?)(?![\d/.,]\d)"
    r"|(?P<ord>" + _ORD_RE + r"(?:\s+" + _ORD_RE + r")?)"
    r"|(?P<sub>παρ(?:αγρ\w*)?\.?|§+|εδ(?:αφ\w*)?\.?|περ(?:ιπτ\w*)?\.?|υποπερ(?:ιπτ\w*)?\.?|υποπαρ\.?|στοιχ\w*\.?|αριθ(?:μ)?\.?|αρ\.|σημ\.?|τελ\.?|στ[ιί]χ\w*\.?)"
    r"|(?P<let>[α-ω]{1,3}'{1,2}|[α-ω]{1,2}\)|[ivx]{1,4}\))"
    r"|(?P<let1>[α-ω](?=[\s,;)]))"
    r"|(?P<skip>επ\.|επομ\w*|\(\d{1,2}\)|[()])"
    r"|(?P<sep>,|και|εωσ|εωσ\s+και|μεχρι|εωσ\s+του|-|εκ\s+των\s+οποιων)"
    r"|(?P<det>του|τησ|των|το|τα)(?=\s)"
    r")"
)
_GAPW = (r"(?:του|τησ|των|τον|την|το|στο|στον|στην|στα|σε|απο|με|κατα|ωσ\s+ανω|ανωτερω|προαναφερθεντοσ|"
         r"ισχυοντοσ|ιδιου|νεου|επ\.|επομ\w*|\(|ε\.?|\((?:παρ\w*\.?|§)\s*\d{1,3}\)|\(\d{1,2}\))")
GAP_OK = re.compile(r"^[\s,()]*(?:" + _GAPW + r"\s*)*$|^\s*(?:του|τησ)\s+(?:κ[ωο]δικα|οργανισμου|κανονισμου|καταστατικου)\s[^().;«»\d]{0,90}\(\s*$")
KYRO_GAP = re.compile(r"^[\s,]*(?:του|τησ|των)?\s*(?:κυρω\w*|που\s+κυρωθηκε|ο\s+οποιοσ\s+κυρωθηκε)\s+(?:με\s+|δια\s+)?(?:το|του|την|τησ)?\s*$")
CHAIN_OK = re.compile(r"^\s*(?:,|και|καθωσ\s+και)\s*(?:(?:το|τα|του|των|τησ|στο|στα|με\s+το)\s+)?$")

# relation verbs (folded)
_OPER_WORDS = {
    "amends": ["τροποποιειται", "τροποποιουνται", "αναριθμειται", "αναριθμουνται", "αναδιατυπωνεται",
               "τροποποιουμε", "τροποποιουνται"],
    "replaces": ["αντικαθισταται", "αντικαθιστανται", "αντικαθιστωνται", "αντικαθιστουμε"],
    "repeals": ["καταργειται", "καταργουνται", "καταργουμε"],
    "adds": ["προστιθεται", "προστιθενται", "συμπληρωνεται", "συμπληρωνονται", "συμπληρουται",
             "συμπληρουνται", "παρεμβαλλεται", "παρεμβαλλονται", "προσθετουμε", "συμπληρωνουμε"],
}


def _ocr_word(w):
    # OCR tolerance for old scans: θ->9/δ/0, κ->χ (line-end hyphens are joined before matching)
    out = []
    for i, ch in enumerate(w):
        if ch == "θ":
            out.append("[θ9δ0]")
        elif ch == "κ":
            out.append("[κχ]")
        else:
            out.append(re.escape(ch))
    return "".join(out)


OPER = re.compile(
    NB + r"(?P<v>" + "|".join("(?P<%s>%s)" % (k, "|".join(_ocr_word(w) for w in sorted(set(v), key=len, reverse=True)))
                              for k, v in _OPER_WORDS.items()) + r")(?![α-ω])"
)
HEAD = re.compile(
    r"\s*(?:αρθρο\s+\S+\s+)?(?:(?P<p>περι)\s+)?(?P<v>(?P<amends>τροποποιησ\w*)|(?P<replaces>αντικαταστασ\w*)|(?P<repeals>καταργησ\w*)|(?P<adds>προσθηκ\w*|συμπληρωσ\w*))"
)
HIST = re.compile(
    NB + r"(?:τροποποιηθ\w*|ετροποποιηθ\w*|αντικατασταθ\w*|αντικατεσταθ\w*|προστεθ\w*|προσετεθ\w*|καταργηθ\w*|κατηργηθ\w*|"
    r"συμπληρωθ\w*|συνεπληρωθ\w*|κυρωθ\w*|εκυρωθ\w*|κυρωνεται|κυρουται|ισχυει|ισχυουν|ισχυε|ισχυαν|ισχυσε|"
    r"τροποποιησ\w*|αντικαταστασ\w*|καταργησ\w*|προσθηκ\w*|συμπληρωσ\w*|τροποποιουμεν\w*|εκδοθ\w*|κωδικοποιηθ\w*|"
    r"διατηρ\w*|καταργουμεν\w*|θεσπισθ\w*|εισαχθ\w*|τροποποιοι\w*|τροποποιει|αντικαθιστα|καταργει|προβλεπ\w*|οριζ\w*|"
    r"κατ'\s?εξουσιοδοτηση|δυναμει|συμφωνα\s+με|σε\s+συνδυασμο)(?![α-ω])"
)
BOUND = re.compile(r"(?<![\s.(\-/][^\W\d_])[.;](?=\s+(?:[Α-ΩΆ-Ώ«\"]|\d{1,3}[.)]\s|[α-ω]{1,2}[')]\s))|\n\s*(?=\d{1,3}\.\s)|\n\s*(?=[ΆA]ρθρο\s)|\n\s*\n")


def _ord_value(s):
    total = 0
    for part in s.split():
        for p, v in _ORD:
            if re.match(p, part):
                total += v
                break
    return str(total) if total else None


def _norm_art(s):
    s = s.replace(" ", "").replace("-", "")
    m = re.match(r"(\d+)([α-θι]?)", s)
    if not m:
        return s
    n, suf = m.groups()
    n = n.lstrip("0") or "0"
    return n + suf.upper()


# --------------------------------------------------------------------------------------
# article groups
# --------------------------------------------------------------------------------------


def _parse_artgroup(ft, pos, need_word=True):
    """Parse an article phrase starting at pos (just after the article word, or at a bare list).
    Returns (end, [(article, paragraph), ...]); article ranges come out as '5-8'."""
    items = []          # [article, [paragraph values]]
    mode = "start"      # start | art_done | art_sep | sub | sub_done | sub_sep
    subkind = None      # 'par' | 'other'
    prev_sub = None     # last numeric sub value (int) or None (letter)
    pending_range = False
    last_end = pos
    p = pos
    n_tok = 0
    while n_tok < 60:
        m = TOK.match(ft, p)
        if not m or m.end() == p:
            break
        n_tok += 1
        g = m.lastgroup
        val = m.group(g)
        if g == "skip":
            p = m.end()
            continue
        if g == "num":
            iv = int(re.match(r"\d+", val).group())
            nxt = TOK.match(ft, m.end())
            next_is_sub = bool(nxt and nxt.lastgroup == "sub")
            as_article = False
            if mode in ("start", "art_sep"):
                as_article = True
            elif mode == "sub":
                if subkind == "par" and items:
                    items[-1][1].append(str(iv))
                prev_sub = iv
                mode = "sub_done"
            elif mode == "sub_sep":
                if pending_range:  # paragraph / sub range "παρ. 1-4"
                    if subkind == "par" and items:
                        items[-1][1].append(str(iv))
                    prev_sub = iv
                    pending_range = False
                    mode = "sub_done"
                elif (not next_is_sub) and prev_sub is not None and (
                        prev_sub < iv <= prev_sub + 2 or (subkind == "other" and iv <= 20)):
                    if subkind == "par" and items:
                        items[-1][1].append(str(iv))
                    prev_sub = iv
                    mode = "sub_done"
                else:
                    as_article = True
            elif mode == "sub_done":
                # "96 παρ. 1 104" (missing comma) -> new article when clearly not a sub value
                if next_is_sub or prev_sub is None or iv > prev_sub + 2:
                    as_article = True
                else:
                    break
            else:  # art_done followed directly by a number
                break
            if as_article:
                a = _norm_art(val)
                if pending_range and items and mode == "art_sep":
                    items[-1][0] = items[-1][0] + "-" + a
                else:
                    items.append([a, []])
                pending_range = False
                mode = "art_done"
                subkind, prev_sub = None, None
            last_end = m.end()
        elif g == "ord":
            if mode in ("start", "art_sep"):
                v = _ord_value(val)
                if not v:
                    break
                items.append([v, []])
                mode = "art_done"
                last_end = m.end()
            else:
                break
        elif g == "sub":
            if not items:
                break
            subkind = "par" if (val.startswith("παρ") or val.startswith("§")) else "other"
            mode = "sub"
            pending_range = False
            last_end = m.end()
        elif g in ("let", "let1"):
            if mode == "art_done" and items and re.fullmatch(r"[α-ι]'{1,2}", val) and not items[-1][1]:
                # "άρθρου 23 Α΄" = article 23Α
                if not re.search(r"[Α-ΩA-Z]$", items[-1][0]):
                    items[-1][0] += val[0].upper()
                last_end = m.end()
            elif mode in ("sub", "sub_sep", "sub_done") and items:
                mode = "sub_done"
                prev_sub = None
                pending_range = False
                last_end = m.end()
            else:
                break
        elif g == "sep":
            rng = val.startswith("εωσ") or val in ("-", "μεχρι")
            if mode == "art_done":
                mode = "art_sep"
                pending_range = rng
            elif mode == "sub_done":
                mode = "sub_sep"
                pending_range = rng
            else:
                break
        else:  # det
            break
        p = m.end()
    out = []
    for a, pars in items:
        out.append((a, ",".join(pars) if pars else None))
    return last_end, out


_PRE_PAR = re.compile(r"(?:παρ(?:αγρ\w*)?\.?|§)\s*(\d{1,3})(?:\s*(?:,|και|εωσ)\s*\d{1,3})*\s*(?:εδ\w*\.?\s*[α-ω0-9]{1,3}'?\s*)?(?:περ\w*\.?\s*[α-ω0-9]{1,3}'?\s*)?(?:του|τησ)\s*$")
# bare article list right before a code / after a previous citation: "914, 922, 334 Α.Κ."
_SUBV = r"(?:\s*(?:παρ\.?|εδ\.?|αρ\.|αριθ\.?|περ\.?|στοιχ\.?|§)\s*[\dα-ω]{1,3}'?(?:\s?-\s?\d{1,3})?)*"
_ITEM = r"\d{1,4}(?:[α-θι](?![α-ω]))?" + _SUBV
_BARE_LIST = re.compile(r"(?<![\d/.,\w])(" + _ITEM + r"(?:\s*(?:,|και)\s*" + _ITEM + r")*)\s*(?:του\s+)?$")
_SUB_BEFORE = re.compile(r"(?:παρ\w*|εδ\w*|περ\w*|αρ\w*|αριθ\w*|§|στοιχ\w*|υπ'?)\.?\s*$")
_BARE_AFTER_CITE = re.compile(r"^\s*(?:,|και|,\s*και)\s*(" + _ITEM + r"(?:\s*(?:,|και)\s*" + _ITEM + r")*)\s*(?:του\s+)?$")
_ART_AFTER_CODE = re.compile(r"\s*(?:αρθρ\w*\.?\s*)?(\d{1,4}(?:[α-θι](?![α-ω]))?)(?![\d/.]\d)(" + _SUBV + ")")
_LAW_CONT = re.compile(r"\s*(?:,|και|,\s*και|\(\s*(?:και\s+)?ηδη|\(|,?\s*και\s+ηδη)\s*(?:(?:του|τησ|των)\s+)?(\d{1,4})\s?[/)]\s?(\d{4}|\d{2})(?![\d/])(?!\s*(?:αποφασ|πραξ|γνωμ|βουλ|διαταγ|\(?εε|εκ\b|εοκ))")
_FEK_AFTER = re.compile(r"\s*,?\s*\(\s*(?:φ\.?\s?ε\.?\s?κ\.?\s*)?(?:τ\.?|τευχ\w*\.?)?\s*(?:(?P<s1>α|β|γ|δ|ααπ)'{0,2}\.?\s*(?P<n1>\d{1,4})(?:\s?/\s?(?P<d1>" + NUMDATE + r"|\d{4}))?|(?P<n2>\d{1,4})\s?/\s?(?:τ\.?\s?)?(?P<s2>α|β|γ|δ|ααπ)'{0,2}\.?(?:\s?/\s?(?P<d2>" + NUMDATE + r"|\d{4}))?|(?P<n3>\d{1,4})\s*(?P<s3>α|β|γ|δ)'{0,2})\s*[),;]")
_SER = {"α": "A", "β": "B", "γ": "Γ", "δ": "Δ", "ααπ": "ΑΑΠ", "υοδδ": "ΥΟΔΔ", "ασεπ": "ΑΣΕΠ", "νπδδ": "ΝΠΔΔ"}

PREFILTER = re.compile(r"\d")


def _fek_year(dstr, default=None):
    if not dstr:
        return default
    if re.fullmatch(r"\d{4}", dstr):
        return int(dstr)
    s, p = _parse_numdate(dstr)
    d = s or p
    return int(d[:4]) if d else default


# --------------------------------------------------------------------------------------
# clause / relation analysis
# --------------------------------------------------------------------------------------


def _quote_spans(text):
    spans = []
    depth = 0
    start = None
    first_close = None
    for m in re.finditer(r"[«»]", text):
        if m.group() == "«":
            if depth == 0:
                start = m.start()
            depth += 1
        else:
            if depth == 0:
                if not spans and first_close is None:
                    first_close = m.end()
                continue
            depth -= 1
            if depth == 0:
                spans.append((start, m.end()))
    if depth > 0 and start is not None:
        spans.append((start, len(text)))
    if first_close is not None and (not spans or spans[0][0] > first_close):
        # chunk starts inside a quotation only if there is no opening quote before it
        spans.insert(0, (0, first_close))
    return spans


def _in_spans(pos, spans):
    for a, b in spans:
        if a <= pos < b:
            return True
        if a > pos:
            break
    return False


_HEADN = re.compile(
    r"(?:^|\n|-\s|\.\s)\s*(?P<p>περι\s+)?(?P<v>(?P<amends>τροποποιησ\w*)|(?P<replaces>αντικαταστασ\w*)|(?P<repeals>καταργησ\w*)|(?P<adds>προσθηκ\w*|συμπληρωσ\w*))")
_INSTR = re.compile(r"(?<![α-ω])(?:απο|με|δια|βασει|συμφωνα|δυναμει)(?![α-ω])")
_OPER_PRE = re.compile(r"τροπο|αντι[κχ]|καταρ|προστι|προσθε|συμπλ|αναρι|παρεμβ|αναδι")
_HEAD_PRE = re.compile(r"τροποποιησ|αντικαταστασ|καταργησ|προσθηκ|συμπληρωσ")
_HIST_END = re.compile(r"[,;)«]|\s(?:ενω)\s(?=\S)")


class _Clauses:
    def __init__(self, text, ft):
        self.text = text
        self.ft = ft
        self.quotes = _quote_spans(text)
        b = [0]
        for m in BOUND.finditer(text):
            if not _in_spans(m.start(), self.quotes):
                b.append(m.end())
        b.append(len(text))
        self.bounds = b
        self.verbs = []
        for m in (OPER.finditer(ft) if _OPER_PRE.search(ft) else ()):
            if not _in_spans(m.start(), self.quotes):
                self.verbs.append((m.start(), next(k for k in ("amends", "replaces", "repeals", "adds") if m.group(k))))
        # heading nouns ("Τροποποίηση του άρθρου 5 του ν. ...", "- Προσθήκη παρ. 8 στο άρθρο 20 ...")
        self.heads = []
        cands = [(cs, HEAD.match(ft, cs, min(len(ft), cs + 60))) for cs in b[:-1]]
        if _HEAD_PRE.search(ft):
            cands += [(None, m) for m in _HEADN.finditer(ft)]
        seen = set()
        for cs, m in cands:
            if not m:
                continue
            vpos = m.start("v")
            if vpos in seen or _in_spans(vpos, self.quotes):
                continue
            if cs is not None and vpos - cs > 40:
                continue
            first = m.start("p") if m.group("p") else vpos
            if not text[first:first + 1].isupper():
                continue
            seen.add(vpos)
            self.heads.append((vpos, next(k for k in ("amends", "replaces", "repeals", "adds") if m.group(k))))
        self.heads.sort()
        head_pos = {p for p, _ in self.heads}
        self.hist = []
        for m in HIST.finditer(ft):
            if m.start() in head_pos or _in_spans(m.start(), self.quotes):
                continue
            e = m.end()
            # historical span runs to the next comma / closing paren / quote / operative verb (max 220)
            mm = _HIST_END.search(ft, e, min(len(ft), e + 220))
            end = mm.start() if mm else min(len(ft), e + 220)
            for vpos, _r in self.verbs:
                if e <= vpos < end:
                    end = vpos
                    break
            self.hist.append((m.start(), end))

    def clause_of(self, pos):
        i = bisect.bisect_right(self.bounds, pos) - 1
        return self.bounds[max(i, 0)], self.bounds[min(i + 1, len(self.bounds) - 1)]

    def relation(self, start, end):
        if _in_spans(start, self.quotes):
            return "cites"
        for a, b in self.hist:
            if a <= start < b:
                return "cites"
        cs, ce = self.clause_of(start)
        best = None
        for vpos, rel in self.verbs:
            if cs <= vpos < ce:
                if any(a <= vpos < b for a, b in self.hist):
                    continue
                if vpos < start and _INSTR.search(self.ft, vpos, start):
                    continue
                d = abs(vpos - start)
                if best is None or d < best[0]:
                    best = (d, rel)
        if best:
            return best[1]
        head = None
        for vpos, rel in self.heads:
            if cs <= vpos < start and start - vpos < 160:
                head = rel
        return head or "cites"


# --------------------------------------------------------------------------------------
# main extractor
# --------------------------------------------------------------------------------------


def _eu_key(kw, a, b, sfx, paren):
    kw = kw or ""
    if kw.startswith("καν"):
        typ = "reg"
    elif kw.startswith("αποφ"):
        typ = "dec"
    else:
        typ = "dir"
    a_i, b_i = int(a), int(b)
    if typ == "reg" and not (len(a) == 4 and 1950 <= a_i <= CUR_YEAR and paren in ("εε", "ευ") ) and not (len(a) == 4 and 1950 <= a_i <= CUR_YEAR and len(b) < 4 and not (1950 <= b_i <= CUR_YEAR)):
        # old style regulation number/year: 1408/71, 883/2004
        num, yr = a_i, b_i
    else:
        yr, num = a_i, b_i
    if yr < 100:
        yr = 1900 + yr if yr > CUR_YEAR % 100 else 2000 + yr
    if not (1950 <= yr <= CUR_YEAR):
        return None, None
    return "eu:%s:%d/%d" % (typ, yr, num), yr


def extract(text, self_law=None, doc_year=None, include_self=False):
    if not text or not PREFILTER.search(text):
        return []
    if _COMBINING.search(text):
        text = unicodedata.normalize("NFC", text)
    ft = fold(text)
    orig, segs = text, None
    if "-" in ft and _HYPH.search(ft):
        text, ft, segs = _dehyphenate(text, ft)
    anchors = []
    skip_to = 0
    amatch = ANCHOR.match
    for cm0 in _CAND.finditer(ft):
        pos = cm0.start()
        if pos < skip_to or (pos and ft[pos - 1].isalnum()):
            continue
        m = amatch(ft, pos)
        if not m:
            continue
        a = _anchor(m, text, ft, doc_year)
        if not a:
            continue
        anchors.append(a)
        skip_to = a.get("fek_end") or a["end"]
        if a.get("number") and a["kind"] in LAW_KINDS:
            # "των α.ν. 1676/39 και 2452/40", "των ν. 1234/2000, 2345/2001"
            p = skip_to
            while True:
                cm = _LAW_CONT.match(ft, p)
                if not cm:
                    break
                yr = _year(cm.group(2), a["kind"], cm.group(1), doc_year)
                if not yr:
                    break
                c = _law(a["kind"], cm.group(1), yr, cm.start(1), cm.end(2), text, ft)
                c["cont"] = True
                anchors.append(c)
                p = c.get("fek_end") or c["end"]
            skip_to = p
    if not anchors:
        return []
    # article groups
    groups = []
    gend = -1
    for m in ARTWORD.finditer(ft):
        if m.start() < gend:
            continue
        end, arts = _parse_artgroup(ft, m.end())
        if arts:
            gend = end
            st = m.start()
            pm = _PRE_PAR.search(ft, max(0, st - 60), st)
            if pm and len(arts) == 1 and not arts[0][1]:
                arts = [(arts[0][0], pm.group(1))]
                st = pm.start()
            groups.append([st, end, arts])
    clauses = _Clauses(text, ft)
    out = []
    last_by_family = {}
    attach = {}
    anchor_starts = [a["start"] for a in anchors]
    for gi, g in enumerate(groups):
        j = bisect.bisect_left(anchor_starts, g[1])
        if j < len(anchors):
            gap = ft[g[1]:anchors[j]["start"]]
            nxt_group_inside = gi + 1 < len(groups) and g[1] <= groups[gi + 1][0] < anchors[j]["start"]
            if len(gap) <= 110 and GAP_OK.match(gap) and not nxt_group_inside:
                attach[gi] = j
    # "άρθρο 202 του κυρωθέντος με το άρθρο πρώτο του ν. 3463/2006 Κώδικα"
    for gi in range(len(groups) - 1, 0, -1):
        if attach.get(gi) is not None and (gi - 1) not in attach:
            g, pg = groups[gi], groups[gi - 1]
            if KYRO_GAP.match(ft[pg[1]:g[0]]) and g[2] and g[2][0][0] == "1":
                attach[gi - 1] = attach[gi]
                attach[gi] = None
    # chains: "άρθρου 5 και το άρθρο 6 του ν. X"
    for gi in range(len(groups) - 2, -1, -1):
        if gi not in attach and attach.get(gi + 1) is not None:
            if CHAIN_OK.match(ft[groups[gi][1]:groups[gi + 1][0]]):
                attach[gi] = attach[gi + 1]
    by_anchor = {}
    for gi, j in attach.items():
        if j is not None:
            by_anchor.setdefault(j, []).append(gi)
    prev_end = -1
    for j, a in enumerate(anchors):
        arts = []
        start = a["start"]
        for gi in sorted(by_anchor.get(j, [])):
            arts.extend(groups[gi][2])
            start = min(start, groups[gi][0])
        if a.get("art") and not arts:
            arts = [(a["art"], a.get("par"))]
        kind = a["kind"]
        if a.get("ana"):
            prev = last_by_family.get(a["ana"])
            if not prev or not arts:
                continue
            a = dict(prev, start=a["start"], end=a["end"], fek=None)
            kind = a["kind"]
        if not arts and not a.get("cont"):
            # bare article list: right after the previous citation (", 20 παρ.1 ν. 1806/1988")
            # or, for codes, any bare list before the code ("914, 922, 334 Α.Κ.", "(258 ΠΚ)")
            lb = None
            if prev_end >= 0 and a["start"] - prev_end < 80:
                bm = _BARE_AFTER_CITE.match(ft[prev_end:a["start"]])
                if bm:
                    lb = prev_end + bm.start(1)
            if lb is None and (kind in ("κωδ", "Σ") or a.get("named") is not None) and not a.get("bare_s"):
                bm = _BARE_LIST.search(ft, max(0, a["start"] - 120), a["start"])
                if bm and not _SUB_BEFORE.search(ft, max(0, bm.start(1) - 14), bm.start(1)):
                    lb = bm.start(1)
            if lb is not None:
                _e, arts = _parse_artgroup(ft, lb)
                if arts:
                    start = lb
        if (kind in ("κωδ", "Σ") or (kind == "eu" and a.get("named") is not None)) and not arts:
            fm = _ART_AFTER_CODE.match(ft, a["end"])
            if fm and not a.get("named") and kind == "κωδ":
                arts = [(_norm_art(fm.group(1)), _first_par(fm.group(2)))]
                a = dict(a, end=fm.end())
            if not arts:
                continue
        key = a["key"]
        if kind in ("ν", "κν", "αν", "πνπ"):
            last_by_family["law"] = a
        elif kind in ("πδ", "νδ", "βδ", "νπδ"):
            last_by_family["δ"] = a
        elif kind == "κωδ":
            last_by_family["code"] = a
        prev_end = a.get("fek_end") or a["end"]
        is_self = bool(self_law) and key == self_law
        if is_self and not include_self:
            continue
        rel = "self" if is_self else clauses.relation(a["start"], a["end"])
        ostart, oend = (start, a["end"]) if segs is None else (_omap(segs, start), _omap(segs, a["end"] - 1) + 1)
        base = {"kind": kind, "number": a.get("number"), "year": a.get("year"), "key": key,
                "raw": orig[ostart:oend], "start": ostart, "end": oend, "relation": rel,
                "fek": a.get("fek"), "date": a.get("date")}
        if not arts:
            out.append(dict(base, article=None, paragraph=None))
        else:
            seen = set()
            for art, par in arts:
                if art in seen:
                    continue
                seen.add(art)
                out.append(dict(base, article=art, paragraph=par))
    return out


LAW_KINDS = ("ν", "κν", "πδ", "νδ", "αν", "βδ", "νπδ", "πνπ")
_HYPH = re.compile(r"(?<=[α-ω])-[ \t]*\n\s*(?=[α-ω])")


def _dehyphenate(text, ft):
    """Join words split by a line-end hyphen ('αντικα-\nθίσταται'). Returns the joined text,
    its fold and a segment map [(new_start, orig_start)] for mapping offsets back."""
    segs = []
    tp, fp = [], []
    last = 0
    newpos = 0
    for m in _HYPH.finditer(ft):
        segs.append((newpos, last))
        tp.append(text[last:m.start()])
        fp.append(ft[last:m.start()])
        newpos += m.start() - last
        last = m.end()
    segs.append((newpos, last))
    tp.append(text[last:])
    fp.append(ft[last:])
    return "".join(tp), "".join(fp), segs


def _omap(segs, pos):
    i = bisect.bisect_right(segs, (pos, float("inf"))) - 1
    ns, os_ = segs[max(i, 0)]
    return os_ + (pos - ns)


def _first_par(s):
    if not s:
        return None
    m = re.search(r"(?:παρ\.?|§)\s*(\d{1,3})", s)
    return m.group(1) if m else None


def _anchor(m, text, ft, doc_year):
    g = m.groupdict()
    s, e = m.start(), m.end()
    if g["fek"]:
        ser = g["fs1"] or g["fs2"] or g["fs3"]
        num = g["fn1"] or g["fn2"] or g["fn3"]
        d = g["fd1"] or g["fd2"] or g["fd3"]
        yr = _fek_year(d)
        if not yr:
            return None
        key = "fek%s:%d/%d" % (_SER[ser], yr, int(num))
        return {"kind": "fek", "key": key, "number": str(int(num)), "year": yr, "start": s, "end": e}
    if g["eukw"] or g["eua2"]:
        if g["eukw"]:
            kw, a, b, sfx, paren = g["eukw"], g["eua"], g["eub"], g["eus"], g["eup"]
            if kw.startswith(("καν", "αποφ")) and not (sfx or paren):
                return None
            if kw.startswith("οδηγ") and not (sfx or paren) and not len(a) in (2, 4):
                return None
        else:
            kw, a, b, sfx, paren = None, g["eua2"], g["eub2"], g["eus2"], None
            # look back for a keyword ("της Οδηγίας του Συμβουλίου 92/13/ΕΚ", "Απόφαση 2009/..")
            back = ft[max(0, s - 60):s]
            km = re.search(r"(οδηγι\w*|κανονισμ\w*|αποφασ\w*)[^.;]*$", back)
            if km:
                kw = km.group(1)
        key, yr = _eu_key(kw, a, b, sfx, paren)
        if not key:
            return None
        return {"kind": "eu", "key": key, "number": key[3:], "year": yr, "start": s, "end": e}
    if g["rn"]:
        rk = re.sub(r"[\s.]", "", g["rkw"])
        kind = {"νομου": "ν", "νομοσ": "ν", "νομο": "ν", "νομον": "ν", "ν": "ν", "νδ": "νδ", "πδ": "πδ",
                "βδ": "βδ", "αν": "αν"}.get(rk)
        if not kind:
            kind = "νδ" if rk.startswith("νομοθ") else ("πδ" if rk.startswith(("προεδ", "πδ")) else "ν")
        num = g["rn"].replace("ο", "0")
        yr = _year(g["ry"], kind, num, doc_year)
        if not yr:
            return None
        return _law(kind, num, yr, s, e, text, ft)
    if g["dd"]:
        pm = re.search(r"(?:απο|τησ)\s+$", ft[max(0, s - 8):s])
        if not pm:
            return None
        s = s - (len(ft[max(0, s - 8):s]) - pm.start())
        dk = re.sub(r"[\s.]", "", g["dkw"])
        kind = "βδ" if dk.startswith(("β",)) else "νδ" if dk.startswith(("νδ", "νομοθ")) else "πνπ" if dk.startswith(("πνπ", "πραξ")) else "πδ"
        return _dated(kind, g["dd"], s, e, doc_year)
    if g["kw"]:
        kind = next(_KW_KIND[k] for k in _KW_KIND if g.get(k))
        kwtxt = g["kw"]
        # bare uppercase-less "αν"/"κν" etc. need care: require original uppercase or dots
        if kind in ("αν", "κν") and not re.match(r"[ακ]\.", kwtxt) and " " not in kwtxt.strip():
            orig = text[m.start("kw"):m.end("kw")]
            if not orig[:1].isupper():
                return None
        if s >= 2 and re.search(r"(?:^|[^α-ω])[α-ω]\.\s?$", ft[max(0, s - 4):s]):
            return None  # part of a longer dotted abbreviation (Α.Β.Δ. = case-book number)
        if g["n"]:
            if len(g["y"]) == 2 and m.end() >= len(ft):
                return None  # possibly truncated year at the chunk end
            num = g["n"].replace("ο", "0")
            yr = _year(g["y"], kind, num, doc_year)
            if not yr:
                return None
            if kind == "ν" and kwtxt.startswith("νομ") is False and int(num) == 0:
                return None
            a = _law(kind, num, yr, s, e, text, ft)
            if a and g["cart"]:
                a["art"] = _norm_art(g["cart"])
                a["par"] = g["cpar"]
            return a
        if g["n2"]:
            sd, pd = (_parse_numdate(g["nd2"], doc_year) if g["nd2"][0].isdigit() and not re.search(_MONTH_RE, g["nd2"]) else (_parse_textdate(g["nd2"]), None))
            d = sd or pd
            if not d:
                return None
            return _law(kind, g["n2"], int(d[:4]), s, e, text, ft)
        if g["d"]:
            if kind == "ν":
                return None  # laws are never date-identified
            return _dated(kind, g["d"], s, e, doc_year)
        return None
    if g["ana"]:
        kw = g["anakw"] or g["anakw2"] or ("νομου" if "νομου" in g["ana"] else "κωδικα")
        fam = "code" if kw.startswith("κωδ") else ("δ" if ("διαταγ" in kw or "δ." in kw) else "law")
        return {"ana": fam, "kind": None, "key": None, "start": s, "end": e}
    if g["code"]:
        name = next(_CODE_NAME[k] for k in _CODE_NAME if g.get(k))
        ctext = g["code"]
        named = " " in ctext.strip() and len(ctext) > 8
        if name == "Σ":
            orig = text[s:e]
            bare = ctext.strip() in ("σ",)
            if bare and not orig.startswith("Σ"):
                return None
            yr = None
            ym = re.match(r"\s*(?:του\s+)?(1864|1911|1927|1952|1968|1975)(?!\d)", ft[e:e + 12])
            if ym and not bare:
                yr = int(ym.group(1))
            return {"kind": "Σ", "key": law_key("Σ", year=yr), "number": None, "year": yr,
                    "start": s, "end": e, "bare_s": bare}
        # short abbreviations must be written in capitals in the original
        if not named:
            orig = re.sub(r"[\s.]", "", text[s:e])
            if not orig or sum(1 for c in orig if c.isupper()) < max(1, len(orig) // 2):
                return None
        if name in _EU_TREATIES:
            return {"kind": "eu", "key": "eu:" + name, "number": name, "year": None,
                    "start": s, "end": e, "named": named}
        return {"kind": "κωδ", "key": "κωδ:" + name, "number": name, "year": None,
                "start": s, "end": e, "named": named}
    return None


def _kind_year_ok(kind, y):
    if y is None:
        return False
    k = "ν" if kind == "κν" else kind
    yrs = _years().get(k)
    if not yrs:
        return True
    return y in yrs or (y - 1) in yrs or (y + 1) in yrs


_YEARS = None


def _years():
    global _YEARS
    if _YEARS is None:
        _ranges()
        _YEARS = {k: set(v) for k, v in (_RANGES_YEARS or {}).items()}
    return _YEARS


def _law(kind, num, yr, s, e, text, ft):
    if not _kind_year_ok(kind, yr):
        return None
    num = str(int(num))
    key = law_key(kind if kind != "κν" else "κν", num, yr)
    a = {"kind": kind, "key": key, "number": num, "year": yr, "start": s, "end": e, "fek": None}
    fm = _FEK_AFTER.match(ft, e)
    if fm:
        ser = fm.group("s1") or fm.group("s2") or fm.group("s3")
        fnum = fm.group("n1") or fm.group("n2") or fm.group("n3")
        d = fm.group("d1") or fm.group("d2")
        fy = _fek_year(d, yr)
        a["fek"] = "fek%s:%d/%d" % (_SER[ser], fy, int(fnum))
        a["fek_end"] = fm.end()
    return a


def _dated(kind, dstr, s, e, doc_year):
    if re.search(_MONTH_RE, dstr):
        sd, pd = _parse_textdate(dstr), None
    else:
        sd, pd = _parse_numdate(dstr, doc_year)
    if not sd or not _kind_year_ok(kind, int(sd[:4])):
        return None
    return {"kind": kind, "key": law_key(kind, date=sd), "number": None, "year": int(sd[:4]),
            "date": sd, "pub_date": pd, "start": s, "end": e}


# --------------------------------------------------------------------------------------
# NSK compact provisions
# --------------------------------------------------------------------------------------
_NSK_KIND = {"Ν": "ν", "ΚΝ": "κν", "ΠΔ": "πδ", "ΝΔ": "νδ", "ΑΝ": "αν", "ΒΔ": "βδ", "ΠΝΠ": "πνπ", "ΝΠΔ": "νπδ"}
_NSK_CODES = {"ΑΚ": "ΑΚ", "ΚΠΟΛΔ": "ΚΠολΔ", "ΚΠΟΛΔΙΚ": "ΚΠολΔ", "ΚΠΟΙΝΔ": "ΚΠΔ", "ΠΟΙΝΚ": "ΠΚ",
              "ΕΜΠΝ": "ΕμπΝ", "ΕΙΣΝΑΚ": "ΕισΝΑΚ", "ΕΙΣΝΚΠΟΛΔ": "ΕισΝΚΠολΔ", "ΚανΒουλ": "ΚανΒουλ",
              "ΑΓΡΚ": "ΑγρΚ", "ΕΣΔ": "ΕΣΔΑ", "ΕΣΔΑ": "ΕΣΔΑ", "ΣΛΕΕ": "ΣΛΕΕ", "ΣΕΕ": "ΣΕΕ", "ΣυνθΕΚ": "ΣυνθΕΚ",
              "ΚΠΔ": "ΚΠΔ", "ΚΝΔΔ": "ΚΝΔΔ", "ΚΔΔ": "ΚΔΔ", "ΥΚ": "ΥΚ", "ΚΕΔΕ": "ΚΕΔΕ"}
_NSK_RE = re.compile(r"^\s*(Ν|ΚΝ|ΠΔ|ΝΔ|ΑΝ|ΒΔ|ΠΝΠ|ΝΠΔ)\s?(\d+(?:/\d+)?(?:[-./]\d+)*)((?:Α\d+[α-ωΑ-Ω]?(?:Π\d+[α-ω]?)?)*)\s*$")
_NSK_EU = re.compile(r"^\s*(ΚανΕΕ|ΚανΕΟΚ|ΚανΕΚ|Οδηγ)\s?(\d+)/(\d+)(?:/[\d\-.]+)?((?:Α\d+(?:Π\d+)?)*)\s*$")
_NSK_CODE = re.compile(r"^\s*(?:Σ(\d{4})|([Α-ΩA-Za-zα-ω]+?))Α(\d+[α-ωΑ-Ω]?)(?:Π(\d+))?")


def _refs(tail):
    return [(_norm_art(a), p or None) for a, p in re.findall(r"Α(\d+[α-ω]?)(?:Π(\d+[α-ω]?))?", tail or "")]


def parse_nsk_provision(s):
    """NSK compact provision -> {kind, key, article, paragraph, refs, date} or None."""
    if not s or len(s) > 80:
        return None
    s = s.strip()
    m = _NSK_RE.match(s)
    if m:
        kd, body, tail = m.groups()
        kind = _NSK_KIND[kd]
        refs = _refs(tail)
        art, par = (refs[0] if refs else (None, None))
        mm = re.fullmatch(r"(\d+)/(\d{4}|\d{2})", body)
        if mm:
            num, yr = mm.group(1), _year(mm.group(2), kind, mm.group(1))
            if not yr:
                return None
            return {"kind": kind, "key": law_key(kind, num, yr), "number": str(int(num)), "year": yr,
                    "article": art, "paragraph": par, "refs": refs, "date": None}
        # date-identified: 31-8/18-9-1935 ; 17-10-1923 ; 10/20-11-1939 ; 2-8/1927 ; 28/28-7/1978
        b = body.replace("/", "/")
        sd = None
        for cand in (b, re.sub(r"^(\d{1,2})/(\d{1,2})-(\d{1,2})/(\d{2,4})$", r"\1-\3/\4", b)):
            mm = re.fullmatch(NUMDATE, cand.replace("/", "/"))
            if mm:
                sd, _pd = _parse_numdate(cand)
                if sd:
                    break
        if not sd:
            mm = re.fullmatch(r"(\d{1,2})[-./](\d{1,2})[-./](\d{4})", b)
            if mm:
                sd = _mkdate(int(mm.group(3)), int(mm.group(2)), int(mm.group(1)))
        if not sd:
            return None
        return {"kind": kind, "key": law_key(kind, date=sd), "number": None, "year": int(sd[:4]),
                "article": art, "paragraph": par, "refs": refs, "date": sd}
    m = _NSK_EU.match(s)
    if m:
        kw, a, b, tail = m.groups()
        key, yr = _eu_key("καν" if kw.startswith("Καν") else "οδηγ", a, b, None, "εε" if kw == "ΚανΕΕ" and len(a) == 4 and int(a) >= 2015 else None)
        if not key:
            return None
        refs = _refs(tail)
        art, par = (refs[0] if refs else (None, None))
        return {"kind": "eu", "key": key, "number": key[3:], "year": yr, "article": art, "paragraph": par,
                "refs": refs, "date": None}
    m = _NSK_CODE.match(s)
    if m and " " not in s.split("Α")[0]:
        syear, code, art, par = m.groups()
        tail = s[m.start(3) - 1:]
        refs = _refs(tail)
        if syear:
            y = int(syear)
            return {"kind": "Σ", "key": law_key("Σ", year=y), "number": None, "year": y, "article": refs[0][0],
                    "paragraph": refs[0][1], "refs": refs, "date": None}
        c = _NSK_CODES.get(code, code)
        if not re.fullmatch(r"[Α-ΩA-Z][Α-ΩA-Zα-ω]{1,12}", code or ""):
            return None
        kind = "eu" if c in ("ΣΛΕΕ", "ΣΕΕ", "ΣυνθΕΚ") else "κωδ"
        key = ("eu:" + c) if kind == "eu" else ("κωδ:" + c)
        return {"kind": kind, "key": key, "number": c, "year": None, "article": refs[0][0], "paragraph": refs[0][1],
                "refs": refs, "date": None}
    # "ΑΚ 967", "ΑΚ648"
    m = re.match(r"^\s*(ΑΚ|ΚΠολΔ|ΚΠΟΛΔ|ΠΚ|ΚΠΔ)\s?(\d+)(?:Π(\d+))?\s*$", s)
    if m:
        c = _NSK_CODES.get(m.group(1).upper(), m.group(1))
        return {"kind": "κωδ", "key": "κωδ:" + c, "number": c, "year": None, "article": m.group(2),
                "paragraph": m.group(3), "refs": [(m.group(2), m.group(3))], "date": None}
    return None


def extract_nsk(provisions, doc_year=None):
    """NSK provisions list -> citations (compact format via parse_nsk_provision, free text via extract)."""
    out = []
    for p in provisions or []:
        if not isinstance(p, str) or len(p) > 400 or "function(" in p or "getValue" in p:
            continue
        r = parse_nsk_provision(p)
        if r:
            for art, par in (r["refs"] or [(None, None)]):
                out.append({"kind": r["kind"], "number": r["number"], "year": r["year"], "key": r["key"],
                            "article": art, "paragraph": par, "raw": p, "start": 0, "end": len(p),
                            "relation": "cites", "fek": None, "date": r["date"]})
        else:
            out.extend(extract(p, doc_year=doc_year))
    return out


# --------------------------------------------------------------------------------------
# ΦΕΚ resolution
# --------------------------------------------------------------------------------------
_TABLE = None


def build_tables(index_path=INDEX_JSONL, out_path=TABLE_PATH, ranges_path=RANGES_PATH):
    """Stream ../index.jsonl -> {key: [[label, stem, date, title], ...]} (+ date-keyed rows)."""
    keys = {}
    dates = {}
    ranges = {}
    years = {}
    with open(index_path, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            kind = CATALOGUE_KIND.get(r.get("_catalogue_name"))
            if not kind:
                continue
            dt = r.get("search_IssueDate") or ""
            try:
                mo, dd, yy = int(dt[0:2]), int(dt[3:5]), int(dt[6:10])
            except Exception:
                continue
            iso = "%04d-%02d-%02d" % (yy, mo, dd)
            years.setdefault(kind, set()).add(yy)
            try:
                grp = int(r.get("search_IssueGroupID") or 1)
                docn = int(r.get("search_DocumentNumber"))
            except Exception:
                continue
            stem = "%d%02d%05d" % (yy, grp, docn)
            label = r.get("search_PrimaryLabel") or ("Α %d/%d" % (docn, yy))
            title = (r.get("search_Description") or "").strip()[:160]
            row = [label, stem, iso, title]
            num = (r.get("search_LawProtocolNumber") or "").strip()
            if num.isdigit():
                k = law_key(kind, num, yy)
                lst = keys.setdefault(k, [])
                if row not in lst:
                    lst.append(row)
                n = int(num)
                rr = ranges.setdefault(kind, {}).setdefault(yy, [n, n])
                rr[0] = min(rr[0], n)
                rr[1] = max(rr[1], n)
            else:
                k = "%s@%s" % (kind, iso)
                lst = dates.setdefault(k, [])
                if row not in lst:
                    lst.append(row)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    tmp = out_path + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump({"keys": keys, "dates": dates}, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, out_path)
    ranges["_years"] = {k: sorted(v) for k, v in years.items()}
    with open(ranges_path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(ranges, f, separators=(",", ":"))
    os.replace(ranges_path + ".tmp", ranges_path)
    global _TABLE, _RANGES, _YEARS
    _TABLE = None
    _RANGES = None
    _YEARS = None
    return len(keys), len(dates)


def _table():
    global _TABLE
    if _TABLE is None:
        if not os.path.exists(TABLE_PATH):
            build_tables()
        with gzip.open(TABLE_PATH, "rt", encoding="utf-8") as f:
            _TABLE = json.load(f)
    return _TABLE


def _rows(rows):
    return [{"label": r[0], "stem": r[1], "date": r[2], "title": r[3]} for r in rows]


def resolve_fek(key):
    """Citation key -> list of ΦΕΚ Α issues {label:"Α 188/2016", stem, date, title}.
    Tries the exact (kind, number, year), then year±1 (laws published in early January / late
    December edge cases), κν -> ν. Date keys (βδ:1953-06-03) -> issues of that kind published
    0..45 days after the signing date (nearest first)."""
    if not key:
        return []
    t = _table()
    if key.startswith("fek"):
        m = re.match(r"fek([A-ZΑ-Ω]+):(\d{4})/(\d+)$", key)
        if not m or m.group(1) != "A":
            return []
        stem = "%s01%05d" % (m.group(2), int(m.group(3)))
        return [{"label": "Α %d/%s" % (int(m.group(3)), m.group(2)), "stem": stem, "date": None, "title": None}]
    m = re.match(r"([^:]+):(\d+)/(\d{4})$", key)
    if m:
        kind, num, yr = m.group(1), m.group(2), int(m.group(3))
        kinds = ["ν"] if kind == "κν" else [kind]
        if kind == "πνπ":
            kinds = ["πνπ"]
        # ±1 year only for continuously numbered acts (laws, ν.δ., α.ν.); π.δ./β.δ. restart yearly
        for dy in ((0, 1, -1) if kind in ("ν", "κν", "νδ", "αν") else (0,)):
            for k in kinds:
                rows = t["keys"].get("%s:%s/%d" % (k, num, yr + dy))
                if rows:
                    return _rows(rows)
        return []
    m = re.match(r"([^:]+):(\d{4})-(\d{2})-(\d{2})$", key)
    if m:
        kind = m.group(1)
        d0 = _dt.date(int(m.group(2)), int(m.group(3)), int(m.group(4)))
        res = []
        for off in range(0, 46):
            d = d0 + _dt.timedelta(days=off)
            rows = t["dates"].get("%s@%s" % (kind, d.isoformat()))
            if rows:
                res.extend(_rows(rows))
                if len(res) >= 3:
                    break
        return res
    return []


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "build":
        print(build_tables())
    else:
        txt = sys.stdin.read()
        for c in extract(txt):
            print(c)
