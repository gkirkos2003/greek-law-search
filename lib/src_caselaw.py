"""Case-law sources for the Greek-law search engine (see search/DESIGN.md).

Sources (keys):
  areios_pagos  caselaw/areios-pagos/decisions.jsonl     Άρειος Πάγος 1991-2026
  elsyn         caselaw/elsyn-tnp/decisions.jsonl        Ελεγκτικό Συνέδριο (TNP API) 2001-2026
  nsk           caselaw/nsk/opinions.jsonl + nsk/text/   Γνωμοδοτήσεις ΝΣΚ 1951-2026 (OCR still running)
  ste           caselaw/ste/decisions_937*.jsonl         ΣτΕ (only records that actually carry text)
  eisap         caselaw/eisap/opinions.jsonl + pdf/      Εισαγγελία ΑΠ: γνωμοδοτήσεις / εγκύκλιοι
  coa           caselaw/court-of-audit/*.pdf             curated Ελ.Συν. PDFs (deduped by md5)

API:  iter_units(sources=None) -> Unit(doc_id, source, fingerprint, load);  unit.load() -> (doc, chunks)

Cheapness: each jsonl gets a line index (offset, length, crc32 of the raw line, few keys) cached in
search/work/caselaw_idx/<name>.tsv and keyed on the jsonl's size+mtime_ns, so after the first pass
iter_units does not re-read the 2.6 GB of jsonl at all. load() reads its one line with os.pread
(fork-safe, no shared file offset). PDFs are pdftotext'ed once into search/work/{coa,eisap}_text/.

Read-only on everything outside search/.  Never touches the OCR service's files except reading.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
import zlib
from dataclasses import dataclass
from typing import Callable, Iterable, Iterator

ROOT = os.environ.get("GREEKLAW_ROOT", "data")  # corpus root
CL = ROOT + "/caselaw"
SEARCH = ROOT + "/search"
WORK = SEARCH + "/work"
IDX_DIR = WORK + "/caselaw_idx"
COA_TXT = WORK + "/coa_text"
EISAP_TXT = WORK + "/eisap_text"

AP_JSONL = CL + "/areios-pagos/decisions.jsonl"
ELSYN_JSONL = CL + "/elsyn-tnp/decisions.jsonl"
NSK_JSONL = CL + "/nsk/opinions.jsonl"
NSK_TEXT = CL + "/nsk/text"
NSK_PDF = CL + "/nsk/pdf"
NSK_DONE = CL + "/nsk/ocr_done.jsonl"
STE_DIR = CL + "/ste"
EISAP_JSONL = CL + "/eisap/opinions.jsonl"
EISAP_PDF = CL + "/eisap/pdf"
COA_DIR = CL + "/court-of-audit"

SOURCES = ("areios_pagos", "elsyn", "nsk", "ste", "eisap", "coa")
# bump a source's version when its parsing/cleaning changes -> fingerprints change -> re-index
PARSER_VERSION = {"areios_pagos": 1, "elsyn": 1, "nsk": 1, "ste": 1, "eisap": 1, "coa": 1}

TARGET_MIN, TARGET_MAX, HARD_MAX = 400, 1500, 2500
SPLIT_ABOVE = 2000      # paragraphs longer than this are cut at sentence ends into <= TARGET_MAX pieces


@dataclass
class Unit:
    doc_id: str
    source: str
    fingerprint: str
    load: Callable


def _fp(source: str, *parts) -> str:
    return f"v{PARSER_VERSION[source]}:" + ":".join(str(p) for p in parts)


# ----------------------------------------------------------------------------------------------
# small text utilities
# ----------------------------------------------------------------------------------------------
_ACC = str.maketrans("άέήίόύώϊΐϋΰΆΈΉΊΌΎΏΪΫ\u00b5\u2206\u2126", "αεηιουωιιυυαεηιουωιυμδω")


def fold1(s: str) -> str:
    """Length-preserving accent/case fold (for regex matching with offsets into the original)."""
    return s.translate(_ACC).lower()


_WS = re.compile(r"[ \t  -   　]+")
_ZW = re.compile(r"[​-‍⁠﻿­]")


_LOOKALIKE = str.maketrans({"\u00b5": "μ", "\u2206": "Δ", "\u2126": "Ω"})


def norm_line(s: str) -> str:
    """Collapse whitespace, drop zero-width chars, map PDF look-alikes (µ micro -> μ, ∆ -> Δ, Ω ohm -> Ω)."""
    return _WS.sub(" ", _ZW.sub("", s.translate(_LOOKALIKE))).strip()


_MONTHS = {
    "ιανουαριου": 1, "φεβρουαριου": 2, "μαρτιου": 3, "απριλιου": 4, "μαιου": 5, "ιουνιου": 6,
    "ιουλιου": 7, "αυγουστου": 8, "σεπτεμβριου": 9, "οκτωβριου": 10, "νοεμβριου": 11,
    "δεκεμβριου": 12, "ιανουαριος": 1, "φεβρουαριος": 2, "μαρτιος": 3, "απριλιος": 4, "μαιος": 5,
    "ιουνιος": 6, "ιουλιος": 7, "αυγουστος": 8, "σεπτεμβριος": 9, "οκτωβριος": 10,
    "νοεμβριος": 11, "δεκεμβριος": 12,
}
_MONTHS.update({"ι/ριου": 1, "φ/ριου": 2, "σ/ριου": 9, "ο/ριου": 10, "ν/ριου": 11, "δ/ριου": 12,
                "ιαν.": 1, "φεβρ.": 2, "φεβ.": 2, "μαρτ.": 3, "απρ.": 4, "ιουν.": 6, "ιουλ.": 7, "αυγ.": 8,
                "σεπτ.": 9, "οκτ.": 10, "οκτωβρ.": 10, "νοεμβρ.": 11, "νοεμ.": 11, "δεκ.": 12, "δεκεμβρ.": 12})
_DATE_WORD = re.compile(r"(?<!\d)(\d{1,2})\s*(?:η|ης|ην|ος|ου)?\s*(" + "|".join(re.escape(m) for m in sorted(_MONTHS, key=len, reverse=True))
                        + r")\s*(?:του\s+(?:ετους\s+)?)?(\d{4})(?!\d)")
_DATE_NUM = re.compile(r"(?<!\d)(\d{1,2})\s?[./-]\s?(\d{1,2})\s?[./-]\s?(\d{4}|\d{2})(?!\d)")
_DATE_KW = [
    ("published", re.compile(r"δημοσιευ[θτ]ηκε|δημοσιευθηκε|εδημοσιευθη")),
    ("decided", re.compile(r"κριθηκε[\s,]+(?:και\s+)?αποφασισ[θτ]ηκε|αποφασισ[θτ]ηκε|εκριθη|απεφασισθη")),
    ("hearing", re.compile(r"συνεδριασε|συνηλθε|συνεδριασ(?:ις|η|ης|εως)\s+της|συνεδριασεως")),
]


def _mkdate(d, m, y, year_hint=None):
    try:
        d, m, y = int(d), int(m), int(y)
    except ValueError:
        return None
    if y < 100:
        y += 2000 if y < 40 else 1900
    if not (1 <= m <= 12 and 1 <= d <= 31 and 1830 <= y <= 2035):
        return None
    if year_hint and abs(y - year_hint) > 2:
        return None
    return f"{y:04d}-{m:02d}-{d:02d}"


def _first_date(f: str, year_hint=None):
    """First date in folded text f (word or numeric form), whichever comes first."""
    best = None
    for rx, word in ((_DATE_WORD, True), (_DATE_NUM, False)):
        for mt in rx.finditer(f):
            if word:
                iso = _mkdate(mt.group(1), _MONTHS[mt.group(2)], mt.group(3), year_hint)
            else:
                iso = _mkdate(mt.group(1), mt.group(2), mt.group(3), year_hint)
            if iso:
                if best is None or mt.start() < best[0]:
                    best = (mt.start(), iso)
                break
    return best[1] if best else None


def find_decision_date(text: str, year_hint=None):
    """-> (iso_date, kind) where kind in published|decided|hearing, or (None, None)."""
    f = fold1(text)
    for kind, rx in _DATE_KW:
        hits = list(rx.finditer(f))
        if not hits:
            continue
        order = reversed(hits) if kind != "hearing" else hits
        for h in order:
            iso = _first_date(f[h.end(): h.end() + 220], year_hint)
            if iso:
                return iso, kind
    return None, None


# ----------------------------------------------------------------------------------------------
# paragraph extraction (paragraph-per-line text, hard-wrapped text, pdftotext -layout, OCR)
# ----------------------------------------------------------------------------------------------
_PAGENUM = re.compile(r"^[-–—\s]*(?:σελ(?:ίδα|\.)?\s*)?\d{1,4}(?:\s*(?:/|από)\s*\d{1,4})?[-–—\s]*$", re.I)
_CODE_LINE = re.compile(r"^[\w.\-]{1,14}(?:/[\w.\-\s]{0,30}){2,}$")      # Σ.Ν./apf/tm.ΕΒΔΟΜΟ 2026/...
_TYPIST = re.compile(r"(?:^|[\s/])(?:apf|prk|olm|es|tm\d*|k\d+[\w.]*)\s*/|/(?:apf|prk|olm|es)\b")
_LETTER_SPACED = re.compile(r"^(?:\S ){3,}\S$")
_TERMINAL = re.compile(r"[.;:!·»\)\]]$")
_NUMBERED = re.compile(r"^(?:\d{1,3}\s*[.)]\s|[IVXΙΧ]{1,5}\s*[.)]\s|[α-ωΑ-Ω]{1,2}[΄'’]?\s*\)\s|Επειδή|ΓΙΑ ΤΟΥΣ ΛΟΓΟΥΣ|Για τους λόγους)")
_CONT_DIGIT = re.compile(r"^\d+\s+(?:[α-ωά-ώ]|του|της|των|και)")
_GREEK = re.compile(r"[Ͱ-Ͽἀ-῿]")
_LATIN = re.compile(r"[A-Za-z]")
_KNOWN_SPACED = {
    "αφουμελετησετασχετικαεγγραφα": "Αφού μελέτησε τα σχετικά έγγραφα",
    "σκεφθηκεκατατονομο": "Σκέφθηκε κατά τον Νόμο",
    "σκεφθηκεκατατονομον": "Σκέφθηκε κατά τον νόμον",
    "σκεφθηκεκατατοννομο": "Σκέφθηκε κατά τον Νόμο",
    "σκεφτηκεκατατονομο": "Σκέφτηκε κατά τον Νόμο",
}
_DROP_EXACT = {
    "ελληνικη δημοκρατια", "το ελεγκτικο συνεδριο", "ελεγκτικο συνεδριο", "ελεγκτικο συνεδριο:",
    "το δικαστηριο του αρειου παγου", "το συμβουλιο της επικρατειας", "συμβουλιο της επικρατειας",
    "αρειος παγος", "δημοσια", "ανωνυμοποιημενη", "ακριβες αντιγραφο",
}
_SIG = re.compile(r"^(?:Ο|Η|ΟΙ|ΤΑ|ΤΟ)\s+(?:ΠΡΟΕΔΡ|ΑΝΤΙΠΡΟΕΔΡ|ΓΡΑΜΜΑΤ|ΕΙΣΗΓΗΤ|ΠΡΟΕΔΡΕΥ|ΣΥΜΒΟΥΛ|ΠΑΡΕΔΡ|ΑΡΕΟΠΑΓΙΤ|ΜΕΛΗ|ΕΠΙΤΡΟΠ|ΓΕΝΙΚ|ΕΙΣΑΓΓΕΛ|ΑΝΤΕΙΣΑΓΓΕΛ)")


_SPACED_WORD = re.compile(r"(?<!\S)((?:[^\W\d_] ){4,}[^\W\d_])(?=[\s,.;:]|$)")


def _collapse_spaced(s: str) -> str:
    if not _LETTER_SPACED.match(s):
        if " " in s and _SPACED_WORD.search(s):
            s = _SPACED_WORD.sub(lambda m: m.group(1).replace(" ", ""), s)
        return s
    joined = s.replace(" ", "")
    return _KNOWN_SPACED.get(fold1(joined).strip(".:"), joined)


def _is_upper_line(s: str) -> bool:
    letters = [c for c in s if c.isalpha()]
    return len(letters) >= 3 and all(not c.islower() for c in letters)


def _pct(vals, q):
    if not vals:
        return 0
    v = sorted(vals)
    return v[min(len(v) - 1, int(q * len(v)))]


def _repeat_keys(lines: list[str]) -> set:
    """Keys of running headers/footers: short lines that occur >=2 times (digits wildcarded)."""
    from collections import Counter
    cnt = Counter(_rkey(s) for s in lines if s and len(s) < 90)
    out = set()
    for k, c in cnt.items():
        if c >= 2 and len(k.split()) <= 12 and not k.startswith(
                ("για τους λογους", "σκεφθηκε", "σκεφτηκε", "αφου μελετησε", "επειδη", "αποφασιζει")):
            out.add(k)
    return out


def _rkey(s: str) -> str:
    return re.sub(r"\d+", "#", fold1(s))


def _junk_line(s: str, ocr: bool) -> bool:
    if not s:
        return True
    if _PAGENUM.match(s):
        return True
    if _CODE_LINE.match(s) and _LATIN.search(s) and len(s) < 100:
        return True
    if len(s) < 120 and _TYPIST.search(s):
        return True
    letters = sum(1 for c in s if c.isalpha())
    if letters < 2:
        return True
    if ocr:
        g = len(_GREEK.findall(s))
        lat = len(_LATIN.findall(s))
        if len(s) <= 14 and lat >= g:
            return True
        if len(s) <= 6 and letters <= 3:
            return True
        toks = s.split()
        bad = sum(1 for t in toks if _bad_ocr_token(t))
        if len(toks) >= 3 and bad / len(toks) > 0.4:
            return True
        if len(s) < 25 and bad and g < 6:
            return True
    return False


_OK_MIXED = re.compile(r"^[(«\"']?(?:\d+(?:ος|ον|ης|η|ου|α|ας|ων|ο|ην)|[Α-ΩA-Z]{1,4}\.?\d+[\w./-]*|\d+[./-]\d+[\w./-]*|\d+[Α-Ωα-ω]?)[)»\"',.;:·]*$")


def _bad_ocr_token(t: str) -> bool:
    if re.search(r"[+*|<>=~^#@$%{}\\]", t):
        return True
    has_g = bool(_GREEK.search(t))
    has_l = bool(_LATIN.search(t))
    if has_g and has_l:
        return True
    if re.search(r"\d", t) and re.search(r"[^\W\d_]", t) and not _OK_MIXED.match(t):
        return True
    return False


def paragraphs(text: str, mode: str = "auto", ocr: bool = False) -> list[str]:
    """Split raw text into clean paragraphs.

    mode: 'lines'   -> each non-empty line is a paragraph (paragraph-per-line sources)
          'wrapped' -> hard-wrapped lines are re-joined into paragraphs
          'layout'  -> like wrapped, but leading indentation (pdftotext -layout) marks new paragraphs
          'auto'    -> 'lines' unless the text looks hard-wrapped
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    raw = text.split("\n")
    # keep indent info for layout mode, normalise each line
    items = []  # (indent, line) ; blank lines -> (None, "")
    for r in raw:
        if "\f" in r:
            items.append((None, ""))
            r = r.replace("\f", "")
        r2 = _ZW.sub("", r.replace("\t", "    ").replace(" ", " "))
        st = r2.strip()
        if not st:
            items.append((None, ""))
            continue
        ind = len(r2) - len(r2.lstrip(" "))
        items.append((ind, _collapse_spaced(norm_line(st))))
    rk = _repeat_keys([s for _, s in items if s])
    items = [(i, s) for i, s in items
             if s == "" or not (len(s) < 90 and _rkey(s) in rk and not _NUMBERED.match(s))]
    items = [(i, s) for i, s in items if s == "" or not _junk_line(s, ocr)]
    lens = [len(s) for _, s in items if s]
    if not lens:
        return []
    if mode == "auto":
        long_share = sum(1 for n in lens if n > 160) / len(lens)
        mode = "lines" if (long_share > 0.08 or len(lens) < 6) else "wrapped"
    if mode == "lines":
        out = []
        for _, s in items:
            if not s:
                continue
            if out and not _TERMINAL.search(out[-1]) and (s[:1].islower() or _CONT_DIGIT.match(s)):
                if out[-1].endswith("-") and out[-1][-2:-1].isalpha() and s[:1].islower():
                    out[-1] = out[-1][:-1] + s
                else:
                    out[-1] += " " + s
            else:
                out.append(s)
        return out

    width = _pct(lens, 0.85) or 80
    trans = blank_trans = 0
    prev_blank = False
    for ind, s in items:
        if not s:
            prev_blank = True
            continue
        if prev_blank:
            blank_trans += 1
        trans += 1
        prev_blank = False
    blanks_meaningful = trans and blank_trans / trans < 0.6
    base = None
    if mode == "layout":
        inds = [i for i, s in items if s]
        from collections import Counter
        base = Counter(inds).most_common(1)[0][0] if inds else 0

    paras: list[str] = []
    cur = ""
    cur_last = ""     # last physical line of cur
    pending_blank = False
    for ind, s in items:
        if not s:
            pending_blank = True
            continue
        if not cur:
            cur, cur_last, pending_blank = s, s, False
            continue
        term = bool(_TERMINAL.search(cur_last))
        short_prev = len(cur_last) < 0.8 * width
        new = False
        if blanks_meaningful and pending_blank:
            # a blank line breaks paragraphs, except a page break inside a sentence
            new = term or not s[:1].islower()
        if mode == "layout" and base is not None and ind is not None and ind >= base + 3 and (term or short_prev):
            new = True
        if term and short_prev:
            new = True
        if term and _NUMBERED.match(s):
            new = True
        if _is_upper_line(cur_last) and len(cur_last) < 80 and not s[:1].islower():
            new = True
        if _is_upper_line(s) and len(s) < 80 and (term or short_prev):
            new = True
        pending_blank = False
        if new:
            paras.append(cur)
            cur, cur_last = s, s
        else:
            if cur.endswith("-") and len(cur) > 1 and cur[-2].isalpha() and s[:1].islower():
                cur = cur[:-1] + s
            else:
                cur = cur + " " + s
            cur_last = s
    if cur:
        paras.append(cur)
    return paras


def clean_paragraphs(paras: list[str]) -> list[str]:
    """Drop boilerplate paragraphs: signature blocks, bare court headers, name-only lines at the end."""
    out = []
    in_operative = False
    for p in paras:
        f = fold1(p).strip()
        if f.startswith("για τους λογους αυτους") or f.startswith("δια ταυτα") or f.startswith("δια τους λογους τουτους"):
            in_operative = True
        if f.strip(" .:") in _DROP_EXACT:
            continue
        if _SIG.match(p) and len(p) < 160 and _is_upper_line(p):
            continue
        if in_operative and _is_upper_line(p) and len(p) < 120 and len(p.split()) <= 8 and not re.search(r"\d", p) \
                and not f.startswith("για τους λογους"):
            continue      # names under the signatures
        out.append(p)
    return out


# ----------------------------------------------------------------------------------------------
# chunking
# ----------------------------------------------------------------------------------------------
_SKEPSI = re.compile(r"^(\d{1,3})\s*\.\s+(?=\S)")
_ROMAN = re.compile(r"^((?:X{0,1}(?:IX|IV|V?I{1,3}|V))|(?:Χ{0,1}(?:ΙΧ|ΙV|V?Ι{1,3}|V)))\s*[.)]\s+")
_BODY_START = re.compile(r"^(?:σκε[φπ][θτ]ηκε|σκεφθεν|αφου μελετησε|μετα απο μελετη|αφου ακουσε την εισηγηση|σκεψεις)")
_OPERATIVE = re.compile(r"^(?:για τους λογους αυτους|δια ταυτα|δια τους λογους τουτους|για τους λογους αυτου)")


def _split_long(p: str, maxlen: int = TARGET_MAX) -> list[str]:
    """Split one long paragraph at sentence ends, else at whitespace; never mid-word."""
    if len(p) <= SPLIT_ABOVE:
        return [p]
    sents = re.split(r"(?<=[.;·!])\s+(?=[«\"(\[]?[Α-ΩΆ-ΏA-Z0-9])", p)
    pieces, cur = [], ""
    for s in sents:
        while len(s) > HARD_MAX:          # monster sentence: cut at a space
            cut = s.rfind(" ", 0, maxlen)
            if cut < maxlen // 2:
                cut = s.find(" ", maxlen)
                if cut < 0:
                    break
            head, s = s[:cut], s[cut + 1:]
            if cur:
                pieces.append(cur)
                cur = ""
            pieces.append(head)
        if cur and len(cur) + 1 + len(s) > maxlen:
            pieces.append(cur)
            cur = s
        else:
            cur = (cur + " " + s) if cur else s
    if cur:
        pieces.append(cur)
    return pieces


def _range(label: str, nums: list) -> str:
    if not nums:
        return ""
    a, b = nums[0], nums[-1]
    if a == b:
        return f"{label} {a}"
    return f"σκέψεις {a}-{b}" if label == "σκέψη" else f"{label} {a}-{b}"


def chunk_decision(paras: list[str], first_seq: int = 0, pre_label: str = "εισαγωγή") -> list[dict]:
    """Group paragraphs into chunks along σκέψεις / Επειδή / sections / operative part."""
    numbered = sum(1 for p in paras if _SKEPSI.match(p))
    epeidi_style = numbered < 2
    # 1) label every paragraph: (zone, section, skepsi_no, body_par_no)
    labelled = []
    zone = "pre"
    section = None
    last_n = 0
    sk = None
    body_par = 0
    epeidi_n = 0
    for p in paras:
        f = fold1(p)
        if _OPERATIVE.match(f):
            zone = "op"
        elif zone == "pre" and (_BODY_START.match(f) or _SKEPSI.match(p) and _SKEPSI.match(p).group(1) == "1"
                                or (epeidi_style and f.startswith("επειδη"))):
            zone = "body"
        if zone == "body":
            m = _SKEPSI.match(p)
            if m and not epeidi_style:
                n = int(m.group(1))
                if last_n < n <= last_n + 3 or n == 1:
                    last_n = n
                    sk = n
            elif epeidi_style and f.startswith("επειδη"):
                epeidi_n += 1
                sk = epeidi_n
            mr = _ROMAN.match(p)
            if mr and len(p) < 400 or (mr and not m):
                section = mr.group(1)
            body_par += 1
        labelled.append((p, zone, section, sk if zone == "body" else None, body_par if zone == "body" else None))

    # 2) explode over-long paragraphs
    units = []
    for p, z, sec, n, bp in labelled:
        for piece in _split_long(p):
            units.append((piece, z, sec, n, bp))

    # 3) greedy grouping
    chunks = []
    cur = []

    def flush():
        if not cur:
            return
        text = "\n".join(u[0] for u in cur)
        zs = cur[0][1]
        if zs == "pre":
            loc = pre_label
        elif zs == "op":
            loc = "διατακτικό"
        else:
            nums = sorted(set(u[3] for u in cur if u[3] is not None))
            if nums:
                loc = _range("σκέψη", nums)
            else:
                bps = [u[4] for u in cur if u[4] is not None]
                loc = _range("§", sorted(set(bps))) if bps else "σκέψεις"
            secs = [u[2] for u in cur if u[2]]
            if secs:
                loc = f"{secs[0]}. {loc}"
        chunks.append({"locator": loc, "text": text, "_zone": zs})
        cur.clear()

    size = 0
    for u in units:
        piece, z, sec, n, bp = u
        if cur:
            prev = cur[-1]
            zone_change = z != prev[1]
            new_sk = n is not None and n != prev[3]
            if zone_change and size >= 150:
                flush(); size = 0
            elif new_sk and size >= TARGET_MIN:
                flush(); size = 0
            elif size + len(piece) + 1 > TARGET_MAX and size >= TARGET_MIN:
                flush(); size = 0
            elif size + len(piece) + 1 > HARD_MAX:
                flush(); size = 0
        cur.append(u)
        size += len(piece) + 1
    flush()

    # 4) merge a tiny tail into its predecessor
    out = []
    for c in chunks:
        if out and len(c["text"]) < 200 and len(out[-1]["text"]) + len(c["text"]) + 1 <= HARD_MAX \
                and c["_zone"] == out[-1]["_zone"]:
            out[-1]["text"] += "\n" + c["text"]
            continue
        if out and len(out[-1]["text"]) < 150 and len(out[-1]["text"]) + len(c["text"]) + 1 <= HARD_MAX:
            c["text"] = out[-1]["text"] + "\n" + c["text"]
            c["locator"] = out[-1]["locator"] if out[-1]["_zone"] == c["_zone"] else c["locator"]
            out.pop()
        out.append(c)
    if len(out) > 1 and all(c["_zone"] == "pre" for c in out):     # no structure found at all
        for i, c in enumerate(out):
            c["locator"] = f"{pre_label} {i + 1}/{len(out)}"
    res = []
    for i, c in enumerate(out):
        res.append({"seq": first_seq + i, "locator": c["locator"], "law": None, "article": None,
                    "text": c["text"]})
    return res


def _text_chunks(text_items: list[tuple[str, str]], first_seq: int = 0) -> list[dict]:
    """Chunk a list of (locator, text) blocks (metadata / short documents) to the size rules."""
    res = []
    seq = first_seq
    for loc, t in text_items:
        t = t.strip()
        if not t:
            continue
        for piece in (_split_long(t) if len(t) > HARD_MAX else [t]):
            res.append({"seq": seq, "locator": loc, "law": None, "article": None, "text": piece})
            seq += 1
    return res


def _doc(**kw) -> dict:
    keys = ("doc_id", "source", "citation", "title", "date", "year", "number", "court", "path",
            "ocr_conf", "meta")
    d = {k: kw.get(k) for k in keys}
    if d["meta"] is None:
        d["meta"] = {}
    return d


# ----------------------------------------------------------------------------------------------
# jsonl line index (cached)
# ----------------------------------------------------------------------------------------------
_FD = {}


def _pread(path: str, off: int, ln: int) -> bytes:
    key = (os.getpid(), path)
    fd = _FD.get(key)
    if fd is None:
        fd = os.open(path, os.O_RDONLY)
        _FD[key] = fd
    return os.pread(fd, ln, off)


def _jsonl_index(path: str, name: str, extract: Callable[[dict], dict]) -> list[tuple]:
    """[(offset, length, crc, extra_dict)], cached in IDX_DIR/name.tsv keyed by size+mtime_ns."""
    st = os.stat(path)
    stamp = f"{st.st_size}:{st.st_mtime_ns}:{PARSER_VERSION.get(name.split('.')[0], 1)}"
    cache = os.path.join(IDX_DIR, name + ".tsv")
    try:
        with open(cache, encoding="utf-8") as fh:
            head = fh.readline().rstrip("\n")
            if head == "#" + stamp:
                rows = []
                for line in fh:
                    a, b, c, e = line.rstrip("\n").split("\t", 3)
                    rows.append((int(a), int(b), int(c), json.loads(e)))
                return rows
    except (OSError, ValueError):
        pass
    rows = []
    off = 0
    with open(path, "rb", buffering=1 << 20) as fh:
        for line in fh:
            ln = len(line)
            body = line.rstrip(b"\r\n")
            if body.strip():
                try:
                    rec = json.loads(body)
                    rows.append((off, len(body), zlib.crc32(body), extract(rec)))
                except ValueError:
                    pass           # truncated last line of a growing file: skip
            off += ln
    os.makedirs(IDX_DIR, exist_ok=True)
    tmp = cache + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("#" + stamp + "\n")
        for a, b, c, e in rows:
            fh.write(f"{a}\t{b}\t{c}\t{json.dumps(e, ensure_ascii=False)}\n")
    os.replace(tmp, cache)
    return rows


def _rec(path: str, off: int, ln: int) -> dict:
    return json.loads(_pread(path, off, ln))


# ----------------------------------------------------------------------------------------------
# Άρειος Πάγος
# ----------------------------------------------------------------------------------------------
_AP_HDR = re.compile(r"^Απόφαση\s+(\S+)\s*/\s*(\d{4})\s*\(([^)]*)\)\s*(?:Αριθμός\s+\S+)?\s*")
_AP_CH = re.compile(r"^(ΟΛΟΜΕΛΕΙΑ|Α\d?|Β\d?|Γ|Δ|Ε|ΣΤ|Ζ|Η|Θ)$")
_GREEK_ORD = {"Α": "Α΄", "Β": "Β΄", "Γ": "Γ΄", "Δ": "Δ΄", "Ε": "Ε΄", "ΣΤ": "ΣΤ΄", "Ζ": "Ζ΄", "Η": "Η΄", "Θ": "Θ΄"}


def _ap_chamber(raw: str):
    """'Γ, ΠΟΛΙΤΙΚΕΣ' -> (chamber, short, division)."""
    parts = [p.strip() for p in raw.split(",")]
    div = None
    if parts and parts[-1] in ("ΠΟΛΙΤΙΚΕΣ", "ΠΟΙΝΙΚΕΣ"):
        div = "civil" if parts[-1] == "ΠΟΛΙΤΙΚΕΣ" else "criminal"
        parts = parts[:-1]
    code = ", ".join(parts).strip()
    if not code or code.lower().startswith("αγνωστο"):
        return None, None, div
    if code == "ΟΛΟΜΕΛΕΙΑ":
        return "Ολομέλεια", "Ολ", div
    m = re.match(r"^(ΣΤ|[ΑΒΓΔΕΖΗΘ])(\d?)$", code)
    if m:
        letter, sub = m.groups()
        ordn = letter + (sub or "") + "΄"
        if div == "civil":
            return f"{ordn} Πολιτικό Τμήμα", f"{ordn} Πολ.", div
        if div == "criminal":
            return f"{ordn} Ποινικό Τμήμα", f"{ordn} Ποιν.", div
        return f"{ordn} Τμήμα", ordn, div
    m = re.match(r"^([ΑΒ]) Ποιν\. Διακ\.$", code)
    if m:
        return f"{m.group(1)}΄ Ποινικό Τμήμα Διακοπών", f"{m.group(1)}΄ Ποιν. Διακ.", div
    return code, code, div


_AP_CHROME = {
    "αυτοματη μεταφραση (google translate)", "συνδεσμος αποφασης - link", "επιστροφη - back",
    "περιληψη με chatgpt ai", "περιληψη με claude ai", "περιληψη με deepseek ai", "περιληψη με grok ai",
    "ακυρωση", "συνεχεια",
}


def _ap_strip_chrome(text: str):
    """-> (body_text, header_raw or None, subject or None, summary or None)"""
    lines = text.split("\n")
    start = 0
    head = None
    for i, l in enumerate(lines[:80]):
        s = l.strip()
        if s == "Συνέχεια":
            start = i + 1
            break
    else:
        # fallback: skip known chrome lines at the top
        i = 0
        while i < len(lines) and (not lines[i].strip() or fold1(lines[i].strip()) in _AP_CHROME
                                  or "google" in lines[i].lower() or "τεχνητής νοημοσύνης" in lines[i]
                                  or lines[i].startswith(("Καμία Ευθύνη", "Μη Επίσημο", "Προστασία Δεδομένων",
                                                          "Μόνη αυθεντική", "Η παρούσα λειτουργία", "!"))):
            i += 1
        start = i
    rest = lines[start:]
    subject = summary = None
    # header line "Απόφαση 1230 / 1991 (Γ, ΠΟΛΙΤΙΚΕΣ) Αριθμός 1230/1991"  [+ "Θέμα" / "Περίληψη:" block]
    for j, l in enumerate(rest[:6]):
        m = _AP_HDR.match(l.strip())
        if m:
            head = m.group(3)
            tail = l.strip()[m.end():].strip()
            rest = rest[:j] + ([tail] if tail else []) + rest[j + 1:]
            # official keywords/summary block (≈3k decisions): Θέμα <kw> Περίληψη: <text> ... Αριθμός N/Y
            k = j
            blk = []
            while k < len(rest) and k < j + 40:
                ls = rest[k].strip()
                if re.match(r"^(αριθμος\s+\d|το δικαστηριο|το συμβουλιο)", fold1(ls)):
                    break
                blk.append(ls)
                k += 1
            btxt = re.sub(r"\s*(?:ΑΡΙΘΜΟΣ|Αριθμός)\s+\d+\s*/\s*\d{4}\s*$", "", "\n".join(blk).strip())
            if btxt.startswith(("Θέμα", "Περίληψη")) and k < len(rest):
                mt = re.match(r"^Θέμα\s*:?\s*(.*?)\s*(?:Περίληψη\s*:?\s*(.*))?$", btxt, re.S)
                if mt:
                    subject = norm_line(mt.group(1).replace("\n", " ")) or None
                    summary = norm_line((mt.group(2) or "").replace("\n", " ")) or None
                else:
                    summary = norm_line(re.sub(r"^Περίληψη\s*:?", "", btxt).replace("\n", " ")) or None
                rest = rest[:j] + rest[k:]
            break
    # drop a bare "Αριθμός 84/2019" line near the top
    for j, l in enumerate(rest[:6]):
        if re.match(r"^\s*Αριθμός\s+\d+\s*/\s*\d{4}\s*$", l):
            rest = rest[:j] + rest[j + 1:]
            break
    return "\n".join(rest), head, subject, summary


def _ap_units() -> Iterator[Unit]:
    rows = _jsonl_index(AP_JSONL, "areios_pagos", lambda r: {"id": r.get("id")})
    for off, ln, crc, ex in rows:
        rid = ex.get("id") or ""
        num, _, yr = rid.partition("_")
        doc_id = f"ap:{num}/{yr}"
        yield Unit(doc_id, "areios_pagos", _fp("areios_pagos", f"{crc:08x}"),
                   (lambda off=off, ln=ln, doc_id=doc_id: _ap_load(off, ln, doc_id)))


def _ap_load(off, ln, doc_id):
    r = _rec(AP_JSONL, off, ln)
    num, yr = str(r.get("number") or ""), str(r.get("year") or "")
    year = int(yr) if yr.isdigit() else None
    body, head, subject, summary = _ap_strip_chrome(r.get("text") or "")
    chamber = short = div = None
    if head:
        chamber, short, div = _ap_chamber(head)
    if chamber is None:     # try the text: "Γ" Πολ. Τμήμα:" / "ΣΤ' ΠΟΙΝΙΚΟ ΤΜΗΜΑ"
        m = re.search(r"^\s*(ΣΤ|[ΑΒΓΔΕΖΗ])\d?[΄'\"’]?\s*(Πολ|ΠΟΛ|Ποιν|ΠΟΙΝ)", body[:1500], re.M)
        if m:
            div = "civil" if m.group(2).lower().startswith("πολ") else "criminal"
    plenary = chamber == "Ολομέλεια" or bool(re.search(r"^\s*(?:ΣΕ\s+)?ΟΛΟΜΕΛΕΙΑ", body[:600], re.M))
    if plenary and chamber is None:
        chamber, short = "Ολομέλεια", "Ολ"
    if chamber == "Ολομέλεια":
        citation = f"ΟλΑΠ {num}/{yr}"
    else:
        citation = f"ΑΠ {num}/{yr}" + (f" ({short})" if short else "")
    date, dkind = find_decision_date(body[-2500:], year)
    if not date:
        date, dkind = find_decision_date(body, year)
    paras = clean_paragraphs(paragraphs(body, "auto"))
    chunks = []
    if subject or summary:
        chunks = _text_chunks([("περίληψη", "\n".join(
            [citation] + ([f"Θέμα: {subject}"] if subject else []) + ([f"Περίληψη: {summary}"] if summary else [])))])
    chunks += chunk_decision(paras, first_seq=len(chunks))
    doc = _doc(doc_id=doc_id, source="areios_pagos", citation=citation,
               title=(subject or f"Άρειος Πάγος {num}/{yr}" + (f" — {chamber}" if chamber else "")),
               date=date, year=year, number=num, court="Άρειος Πάγος",
               path=f"{AP_JSONL}#{r.get('id')}",
               meta={"chamber": chamber, "chamber_raw": head, "division": div, "url": r.get("url"),
                     "date_kind": dkind, "subject": subject, "summary": summary})
    return doc, chunks


# ----------------------------------------------------------------------------------------------
# Ελεγκτικό Συνέδριο (TNP API)
# ----------------------------------------------------------------------------------------------
def _elsyn_units() -> Iterator[Unit]:
    rows = _jsonl_index(ELSYN_JSONL, "elsyn", lambda r: {"id": r.get("id")})
    for off, ln, crc, ex in rows:
        doc_id = f"elsyn:{ex.get('id')}"
        yield Unit(doc_id, "elsyn", _fp("elsyn", f"{crc:08x}"),
                   (lambda off=off, ln=ln, doc_id=doc_id: _elsyn_load(off, ln, doc_id)))


def _elsyn_number(aa: str) -> str:
    aa = (aa or "").strip()
    if aa.isdigit():
        return str(int(aa))
    return aa


def _elsyn_subject_from_filename(fn: str, aa: str):
    if not fn:
        return None
    stem = re.sub(r"\.(docx?|pdf|rtf|odt)$", "", fn, flags=re.I)
    s = re.sub(r"^\s*[\d]+\s*[-_ ]?\s*", "", stem)
    s = s.replace("_", " ").strip(" -")
    if aa and s.startswith(aa):
        s = s[len(aa):].strip(" -")
    if len(re.sub(r"[^Α-Ωα-ωA-Za-zΆ-ώ]", "", s)) < 4:
        return None
    return s


def _elsyn_load(off, ln, doc_id):
    r = _rec(ELSYN_JSONL, off, ln)
    num = _elsyn_number(r.get("aaApof"))
    yr = str(r.get("yrApof") or "")
    year = int(yr) if yr.isdigit() else None
    dtype = r.get("docType")
    form = r.get("judicialFormation")
    prefix = {"Απόφαση": "ΕλΣυν", "Πράξη": "ΕλΣυν Πράξη", "Πρακτικό": "ΕλΣυν Πρακτ.",
              "Απόφαση σε Συμβούλιο": "ΕλΣυν (Συμβ.)"}.get(dtype, "ΕλΣυν")
    citation = f"{prefix} {num}/{yr}" + (f" ({form.replace(chr(39), '΄')})" if form else "")
    subj = _elsyn_subject_from_filename(r.get("filename") or "", r.get("aaApof") or "")
    title = f"{dtype or 'Έγγραφο'} {num}/{yr}" + (f" {form}" if form else "") + (f" — {subj}" if subj else "")
    text = r.get("text") or ""
    date, dkind = find_decision_date(text[-3000:], year)
    if not date:
        date, dkind = find_decision_date(text, year)
    paras = clean_paragraphs(paragraphs(text, "auto"))
    head_bits = []
    for lab, key in (("Αποτέλεσμα", "result"), ("Εισαγωγικό δικόγραφο", "introductoryType"),
                     ("Σύνθεση", "composition")):
        if r.get(key):
            head_bits.append(f"{lab}: {r[key]}")
    if r.get("abdCaseNumbers"):
        head_bits.append("Αριθμ. κατάθεσης: " + ", ".join(r["abdCaseNumbers"]))
    chunks = []
    if head_bits:
        head = f"{citation} — {dtype or ''}".strip(" —") + (f" — {subj}" if subj else "") + "\n" + "\n".join(head_bits)
        chunks = _text_chunks([("στοιχεία", head)])
    chunks += chunk_decision(paras, first_seq=len(chunks))
    meta = {k: r.get(k) for k in ("docType", "judicialFormation", "composition", "result",
                                  "introductoryType", "abdCaseNumbers", "president", "filename")}
    meta["rapporteur"] = r.get("eisigitis")
    meta["uploaded"] = r.get("docDate")
    meta["date_kind"] = dkind
    meta["chamber"] = form
    doc = _doc(doc_id=doc_id, source="elsyn", citation=citation, title=title, date=date, year=year,
               number=num, court="Ελεγκτικό Συνέδριο", path=f"{ELSYN_JSONL}#{r.get('id')}", meta=meta)
    return doc, chunks


# ----------------------------------------------------------------------------------------------
# ΝΣΚ γνωμοδοτήσεις
# ----------------------------------------------------------------------------------------------
_NSK_UNKNOWN = "Για την αποδοχή ή μη επικοινωνήστε με τον Σχ. Επιστ. Δραστηριοτήτων κ Δημοσίων Σχέσεων"
_NSK_STATUS = re.compile(r"^(Μερικώς αποδεκτή|Μερικώς Αποδεκτή|Μη αποδεκτή|Μη Αποδεκτή|Αποδεκτή|Εκκρεμεί αποδοχή|Δεν έγινε αποδεκτή)\b")
_NSK_STATUS_MAP = {"αποδεκτη": "accepted", "εκκρεμει αποδοχη": "pending", "μη αποδεκτη": "rejected",
                   "δεν εγινε αποδεκτη": "rejected", "μερικως αποδεκτη": "partly_accepted"}
_NSK_EMBED = re.compile(
    r"^(?:\((?P<tm>[^)]{1,30})\)\s*)?Προεδρε[υύ]ων\s*[:,]\s*(?P<p>.{2,90}?)\s*[,.]?\s*(?P<rl>Εισηγ\w*|Γνωμοδοτ\w*)\s*[:,]\s*(?P<r>[^:]{2,90}?,\s*"
    r"(?:Νομικ\w+\s+Σ[υύ]μβουλο\w*(?:\s+του\s+Κράτους)?|Πάρεδρο\w*|Αντιπρόεδρο\w*|Πρόεδρο\w*|Δικαστικ\w+\s+\w+))\s*[.,]?\s+")
_PROV = re.compile(r"^(?P<kind>[Α-ΩA-Z][Α-ΩA-Zα-ω]{0,5})\s?(?P<num>[0-9][0-9Α-Ω]*)/(?P<year>\d{4})"
                   r"(?:Α(?P<art>\d+[Α-Ω]?))?(?:Π(?P<par>[0-9Α-Ωα-ω]+))?$")
_PROV_CODE = re.compile(r"^(?P<kind>[Α-Ω][Α-Ωα-ω]{0,6}?)Α(?P<art>\d+[Α-Ω]?)(?:Π(?P<par>[0-9Α-Ωα-ω]+))?$")


def parse_nsk_provisions(items: list) -> dict:
    """Clean the scraped `provisions` list -> provisions, president, rapporteur, status, summary."""
    raw = ", ".join(str(x) for x in (items or []))
    for cut in ("-->ΛΗΨΗ ΑΡΧΕΙΟΥ", "$(document)", "<div", "Χρήσιμα Αρχική"):
        k = raw.find(cut)
        if k >= 0:
            raw = raw[:k]
    raw = raw.strip()
    m = re.search(r"Πρόεδρος/Προεδρεύων\s*:(?P<p>.*?)Εισηγητής/Γνωμοδοτών\s*:(?P<r>.*?)Κατάσταση\s*:(?P<rest>.*)$",
                  raw, re.S)
    if m:
        prov_part, pres, rap, rest = raw[:m.start()], m.group("p").strip(), m.group("r").strip(), m.group("rest").strip()
    else:
        prov_part, pres, rap, rest = raw, "", "", ""
    status = "unknown"
    if rest.startswith(_NSK_UNKNOWN):
        rest = rest[len(_NSK_UNKNOWN):].strip()
    else:
        ms = _NSK_STATUS.match(rest)
        if ms:
            status = _NSK_STATUS_MAP.get(fold1(ms.group(1)), "unknown")
            rest = rest[ms.end():].strip()
    rest = re.sub(r"\s*-\s*$", "", rest).strip()
    me = _NSK_EMBED.match(rest)
    formation = None
    if me:
        formation = me.group("tm")
        if not pres:
            pres = me.group("p").strip(" ,")
        if not rap:
            rap = me.group("r").strip(" ,")
        rest = rest[me.end():].strip()
    summary = rest if rest and rest not in ("-", ".") else None
    provs, parsed = [], []
    for it in prov_part.split(","):
        it = it.strip()
        if not it or len(it) > 80:
            continue
        provs.append(it)
        pm = _PROV.match(it) or _PROV_CODE.match(it)
        if pm:
            gd = pm.groupdict()
            parsed.append({"raw": it, "kind": gd.get("kind"), "number": gd.get("num"), "year": gd.get("year"),
                           "article": gd.get("art"), "par": gd.get("par")})
        else:
            parsed.append({"raw": it})
    return {"provisions": provs, "provisions_parsed": parsed, "president": pres or None,
            "rapporteur": rap or None, "status": status, "summary": summary, "formation": formation}


def _nsk_done() -> dict:
    """pdf name -> (chars, conf) from the OCR log (last entry wins). Read-only."""
    out = {}
    try:
        with open(NSK_DONE, "rb") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                    out[d["pdf"]] = (d.get("chars"), d.get("conf"))
                except (ValueError, KeyError):
                    pass      # partially written last line
    except OSError:
        pass
    return out


def _nsk_text_listing() -> dict:
    """year -> set(txt names). listdir only (stat on this fuse mount costs ~5 ms/file)."""
    out = {}
    try:
        years = os.listdir(NSK_TEXT)
    except OSError:
        return out
    for y in years:
        try:
            out[y] = set(n for n in os.listdir(os.path.join(NSK_TEXT, y)) if n.endswith(".txt"))
        except OSError:
            pass
    return out


def _nsk_units() -> Iterator[Unit]:
    rows = _jsonl_index(NSK_JSONL, "nsk", lambda r: {"id": r.get("id"), "n": str(r.get("number")),
                                                      "y": str(r.get("year")), "c": str(r.get("consultId"))})
    listing = _nsk_text_listing()
    done = _nsk_done()
    for off, ln, crc, ex in rows:
        stem = f"{ex['n']}_{ex['y']}__{ex['c']}"
        txt = None
        tfp = "-"
        conf = None
        if stem + ".txt" in listing.get(ex["y"], ()):
            txt = os.path.join(NSK_TEXT, ex["y"], stem + ".txt")
            d = done.get(stem + ".pdf")
            if d:
                tfp = f"{d[0]}:{d[1]}"
                conf = d[1]
            else:
                # written but not yet logged by the OCR runner (its log is buffered): wait for the
                # log line unless the file is old (log lost) -> then fall back to size+mtime
                try:
                    st = os.stat(txt)
                    if time.time() - st.st_mtime > 6 * 3600:
                        tfp = f"{st.st_size}:{st.st_mtime_ns}"
                    else:
                        txt, tfp = None, "-"
                except OSError:
                    txt, tfp = None, "-"
        doc_id = f"nsk:{ex['id']}"
        yield Unit(doc_id, "nsk", _fp("nsk", f"{crc:08x}", tfp),
                   (lambda off=off, ln=ln, doc_id=doc_id, txt=txt, conf=conf, stem=stem:
                    _nsk_load(off, ln, doc_id, txt, conf, stem)))


def _nsk_load(off, ln, doc_id, txt, conf, stem):
    r = _rec(NSK_JSONL, off, ln)
    num = str(r.get("number"))
    year = r.get("year")
    year = int(year) if str(year).isdigit() else None
    pv = parse_nsk_provisions(r.get("provisions"))
    subject = (r.get("subject") or "").strip()
    if subject in ("-", "", "-."):
        subject = None
    subject = re.sub(r"\s*-\.\s*$", "", subject).strip() if subject else None
    lem = [x for x in (r.get("lemmata") or []) if x]
    citation = f"ΓνωμΝΣΚ {num}/{year}"
    meta_lines = [citation]
    if subject:
        meta_lines.append("Θέμα: " + subject)
    if lem:
        meta_lines.append("Λήμματα: " + "; ".join(lem))
    if pv["summary"]:
        meta_lines.append("Περίληψη: " + pv["summary"])
    if pv["provisions"]:
        meta_lines.append("Διατάξεις: " + ", ".join(pv["provisions"]))
    chunks = []
    if len(meta_lines) > 1:
        chunks = _text_chunks([("περίληψη", "\n".join(meta_lines))])
    date = dkind = None
    path = f"{NSK_JSONL}#{r.get('id')}"
    has_text = False
    if txt:
        try:
            with open(txt, encoding="utf-8", errors="replace") as fh:
                body = fh.read()
            has_text = True
            path = txt
        except OSError:
            body = ""
        if body:
            paras = clean_paragraphs(paragraphs(body, "wrapped", ocr=True))
            chunks += chunk_decision(paras, first_seq=len(chunks), pre_label="κείμενο")
            date, dkind = find_decision_date(body[:2500], year)
            if not date:
                date = _first_date(fold1(body[:500]), year)
                dkind = "header" if date else None
    placeholder = False
    if not chunks:   # nothing at all (pre-1986 opinion with no subject and no OCR yet)
        placeholder = True
        chunks = _text_chunks([("περίληψη", citation + " (μόνο στοιχεία· κείμενο δεν έχει ακόμη αναγνωσθεί)")])
    meta = {"consultId": r.get("consultId"), "subject": subject, "lemmata": lem,
            "provisions": pv["provisions"], "provisions_parsed": pv["provisions_parsed"],
            "president": pv["president"], "rapporteur": pv["rapporteur"], "status": pv["status"],
            "summary": pv["summary"], "formation": pv["formation"], "has_fulltext": has_text,
            "placeholder": placeholder,
            "pdf": os.path.join(NSK_PDF, str(r.get("year")), stem + ".pdf"),
            "jsonl": f"{NSK_JSONL}#{r.get('id')}", "date_kind": dkind}
    doc = _doc(doc_id=doc_id, source="nsk", citation=citation, title=subject or citation, date=date,
               year=year, number=num, court="Νομικό Συμβούλιο του Κράτους", path=path,
               ocr_conf=conf if has_text else None, meta=meta)
    return doc, chunks


# ----------------------------------------------------------------------------------------------
# ΣτΕ
# ----------------------------------------------------------------------------------------------
def _ste_units() -> Iterator[Unit]:
    import glob
    best = {}
    for fn in sorted(glob.glob(os.path.join(STE_DIR, "decisions_937*.jsonl"))):
        rows = _jsonl_index(fn, "ste." + os.path.basename(fn),
                            lambda r: {"id": r.get("id"), "n": r.get("number"), "y": r.get("year"),
                                       "tl": len(r.get("text") or "")})
        for off, ln, crc, ex in rows:
            if ex["tl"] < 200:      # pdf-only / empty record: nothing to index
                continue
            key = ex["id"]
            if key not in best or ex["tl"] > best[key][3]["tl"]:
                best[key] = (fn, off, ln, ex, crc)
    for key, (fn, off, ln, ex, crc) in best.items():
        doc_id = f"ste:{ex['n']}/{ex['y']}"
        yield Unit(doc_id, "ste", _fp("ste", f"{crc:08x}"),
                   (lambda fn=fn, off=off, ln=ln, doc_id=doc_id: _ste_load(fn, off, ln, doc_id)))


def _ste_load(fn, off, ln, doc_id):
    r = _rec(fn, off, ln)
    numraw = str(r.get("number") or "")
    num = re.sub(r"^[Α-ΩA-Z]+", "", numraw)
    yr = str(r.get("year") or "")
    year = int(yr) if yr.isdigit() else None
    ch = r.get("chamber")
    if ch in ("-", "") or (ch and ("******" in ch or ch.startswith("Σύνθεση"))):
        ch = None
    if ch == "Ολομέλεια":
        citation = f"ΣτΕ Ολ {num}/{yr}"
    else:
        short = re.sub(r"\s*Τμήμα$", " Τμ.", ch).replace("'", "΄") if ch else None
        citation = f"ΣτΕ {num}/{yr}" + (f" ({short})" if short else "")
    date = None
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", str(r.get("date") or ""))
    dkind = None
    if m:
        date = _mkdate(m.group(1), m.group(2), m.group(3))
        dkind = "listing"
    text = r.get("text") or ""
    if not date:
        date, dkind = find_decision_date(text, year)
    paras = clean_paragraphs(paragraphs(text, "auto"))
    # drop the leading 'Ν.Σ.(m)' style typist marks and the bare 'Αριθμός 4691/2015'
    paras = [p for p in paras[:4] if not re.match(r"^(?:[Α-ΩA-Z.]{1,8}\(\w\)|Αριθμός\s+\d+\s*/\s*\d{4})$", p)] + paras[4:]
    chunks = chunk_decision(paras)
    meta = {"chamber": ch, "ecli": r.get("ecli"), "number_raw": numraw, "date_kind": dkind}
    doc = _doc(doc_id=doc_id, source="ste", citation=citation,
               title=f"Συμβούλιο της Επικρατείας {num}/{yr}" + (f" — {ch}" if ch else ""),
               date=date, year=year, number=num, court="Συμβούλιο της Επικρατείας",
               path=f"{fn}#{r.get('id')}", meta=meta)
    return doc, chunks


# ----------------------------------------------------------------------------------------------
# PDF text cache (court-of-audit, eisap)
# ----------------------------------------------------------------------------------------------
def _pdftotext(path: str, mode: str = "-layout") -> str:
    try:
        p = subprocess.run(["nice", "-n", "19", "ionice", "-c3", "pdftotext", mode, "-enc", "UTF-8",
                            path, "-"], capture_output=True, timeout=300)
        return p.stdout.decode("utf-8", "replace")
    except (subprocess.TimeoutExpired, OSError):
        return ""


def _spaced_out(text: str) -> bool:
    """pdftotext -layout on some justified PDFs emits 'Α θ ή ν α , 2 8 Μα ΐ ο υ': detect it."""
    toks = [t for t in text.split() if any(c.isalpha() for c in t)]
    if len(toks) < 30:
        return False
    one = sum(1 for t in toks if len(t) == 1)
    return one / len(toks) > 0.25


def _extract_pdf(path: str):
    t = _pdftotext(path, "-layout")
    if _spaced_out(t):
        return _pdftotext(path, "-raw"), "raw"
    return t, "layout"


def _md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _pdf_manifest(pdf_dir: str, cache_dir: str) -> dict:
    """filename -> {size, mtime_ns, md5, chars}; text cached as cache_dir/<md5>.txt."""
    os.makedirs(cache_dir, exist_ok=True)
    mpath = os.path.join(cache_dir, "manifest.json")
    try:
        with open(mpath, encoding="utf-8") as fh:
            man = json.load(fh)
    except (OSError, ValueError):
        man = {}
    changed = False
    present = set()
    for e in os.scandir(pdf_dir):
        if not e.is_file() or not e.name.lower().endswith(".pdf"):
            continue
        present.add(e.name)
        st = e.stat()
        old = man.get(e.name)
        if old and old["size"] == st.st_size and old["mtime_ns"] == st.st_mtime_ns and "mode" in old \
                and os.path.exists(os.path.join(cache_dir, old["md5"] + ".txt")):
            if "q" not in old:
                with open(os.path.join(cache_dir, old["md5"] + ".txt"), encoding="utf-8") as fh:
                    old["q"] = _greek_ratio(fh.read())
                changed = True
            continue
        md5 = _md5(e.path)
        tpath = os.path.join(cache_dir, md5 + ".txt")
        same = [m for m in man.values() if m.get("md5") == md5 and "mode" in m and "q" in m]
        if same and os.path.exists(tpath):
            chars, mode, q = same[0]["chars"], same[0]["mode"], same[0]["q"]
        else:
            t, mode = _extract_pdf(e.path)
            with open(tpath + ".tmp", "w", encoding="utf-8") as fh:
                fh.write(t)
            os.replace(tpath + ".tmp", tpath)
            chars, q = len(t), _greek_ratio(t)
        man[e.name] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "md5": md5, "chars": chars, "mode": mode,
                       "q": q}
        changed = True
    for k in list(man):
        if k not in present:
            del man[k]
            changed = True
    if changed:
        with open(mpath + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(man, fh, ensure_ascii=False, indent=0)
        os.replace(mpath + ".tmp", mpath)
    return man


def _greek_ratio(t: str) -> float:
    """Share of Greek letters among non-space chars: ~0.75 for real text, 0.0 for PDFs whose fonts
    lack a ToUnicode map (pdftotext then emits symbol garbage)."""
    ns = re.sub(r"\s", "", t)
    if not ns:
        return 0.0
    return round(len(_GREEK.findall(ns)) / len(ns), 3)


def _group_by_md5(man: dict, min_chars: int, min_q: float = 0.3):
    groups = {}
    for name, m in man.items():
        if m["chars"] < min_chars or m.get("q", 1.0) < min_q:
            continue
        groups.setdefault(m["md5"], []).append(name)
    for md5, names in groups.items():
        names.sort(key=lambda n: ("_EN" in n or re.search(r"[-_ ]1\.pdf$", n) is not None, len(n), n))
        yield md5, names[0], names[1:], man[names[0]].get("mode", "layout")


# ----------------------------------------------------------------------------------------------
# court-of-audit curated PDFs
# ----------------------------------------------------------------------------------------------
_COA_FORM = [
    (r"ολομ|ολελ|^ολ\d|ολ\.", "Ολομέλεια"), (r"μειζ", "Τμήμα Μείζονος Σύνθεσης"),
    (r"ειδ(?:ικη)?\s*συν", "Ολομέλεια (ειδική συνεδρίαση)"), (r"γενικη\s+συνεδριαση", "Ολομέλεια (γενική συνεδρίαση)"),
    (r"πρωτο|\bi\b(?! )", "Πρώτο Τμήμα"), (r"δευτερο|\bii\b", "Δεύτερο Τμήμα"), (r"τριτο|\biii\b", "Τρίτο Τμήμα"),
    (r"τεταρτο|\biv\b|ιv", "Τέταρτο Τμήμα"), (r"πεμπτο|\bv\b", "Πέμπτο Τμήμα"), (r"εκτο|\bvi\b", "Έκτο Τμήμα"),
    (r"εβδομο|εβδ|vii", "Έβδομο Τμήμα"), (r"δεκατο|dekato", "Δέκατο Τμήμα"),
    (r"κλιμ|κλμ", "Κλιμάκιο"),
]


_ORDS = {"πρωτο": "Πρώτο", "δευτερο": "Δεύτερο", "τριτο": "Τρίτο", "τεταρτο": "Τέταρτο", "πεμπτο": "Πέμπτο",
         "εκτο": "Έκτο", "εβδομο": "Έβδομο", "ογδοο": "Όγδοο", "ενατο": "Ένατο", "δεκατο": "Δέκατο"}


def _canon_formation(f: str) -> str:
    """folded 'εκτο τμημα' / 'ελασσονα ολομελεια' / 'ε΄ κλιμακιο' -> canonical accented label."""
    f = f.strip()
    if f == "ολομελεια":
        return "Ολομέλεια"
    if f == "ελασσονα ολομελεια":
        return "Ελάσσονα Ολομέλεια"
    if f.startswith("τμημα μειζονος"):
        return "Τμήμα Μείζονος Σύνθεσης"
    m = re.match(r"^([α-ωiv]+) τμημα$", f) or re.match(r"^τμημα ([α-ωiv]+)$", f)
    if m and m.group(1) in _ORDS:
        return f"{_ORDS[m.group(1)]} Τμήμα"
    if m:
        rom = m.group(1).replace("ι", "i")
        if re.fullmatch(r"(?:i{1,3}|iv|vi{0,3}|ix|x)", rom):
            return f"{rom.upper()} Τμήμα"
    m = re.match(r"^([α-ω]{1,2})[΄'´]?\s*κλιμακιο$", f)
    if m:
        return f"{m.group(1).upper()}΄ Κλιμάκιο"
    return f[:1].upper() + f[1:]


def _coa_meta_from(stem: str, text: str):
    head = text[:1500]
    num = year = None
    kind = "Απόφαση"
    m = re.search(r"(Απόφαση|ΑΠΟΦΑΣΗ|Πράξη|ΠΡΑΞΗ|Πρακτικά|ΠΡΑΚΤΙΚΑ)\s+(?:αριθ\.?\s*)?(\d{1,5})\s*/\s*(\d{4})", head)
    if m:
        kind = {"απόφαση": "Απόφαση", "πραξη": "Πράξη", "πρακτικα": "Πρακτικά"}.get(fold1(m.group(1)), "Απόφαση")
        num, year = str(int(m.group(2))), m.group(3)
    else:
        m = re.search(r"(?<!\d)(\d{1,5})\s*[-_./ ]\s*(?:-\s*)?(20\d\d|19\d\d|\d\d)(?!\d)", stem)
        if m:
            num = str(int(m.group(1)))
            y = m.group(2)
            year = y if len(y) == 4 else ("20" + y)
        if re.search(r"πραξη", fold1(stem)):
            kind = "Πράξη"
        elif re.search(r"πρακτικ|συνεδριαση", fold1(stem)):
            kind = "Πρακτικά"
    form = None
    fh = fold1(head)
    mh = re.search(r"^\s*(?:σε\s+)?(ολομελεια|ελασσονα ολομελεια|[α-ωiv]+ τμημα|τμημα [α-ωiv΄']+|[α-ω΄'´]{1,3}\s*κλιμακιο|τμημα μειζονος[^\n]*)\s*$", fh, re.M)
    if mh:
        form = _canon_formation(mh.group(1))
    else:
        fs = fold1(stem)
        for rx, lab in _COA_FORM:
            if re.search(rx, fs):
                form = lab
                break
    return kind, num, year, form


def _coa_units() -> Iterator[Unit]:
    man = _pdf_manifest(COA_DIR, COA_TXT)
    for md5, name, dups, mode in _group_by_md5(man, 200):
        stem = os.path.splitext(name)[0].strip()
        doc_id = f"coa:{stem}"
        yield Unit(doc_id, "coa", _fp("coa", md5, mode),
                   (lambda md5=md5, name=name, dups=tuple(dups), doc_id=doc_id, mode=mode:
                    _coa_load(md5, name, dups, doc_id, mode)))


def _coa_load(md5, name, dups, doc_id, mode="layout"):
    with open(os.path.join(COA_TXT, md5 + ".txt"), encoding="utf-8") as fh:
        text = fh.read()
    stem = os.path.splitext(name)[0].strip()
    kind, num, year, form = _coa_meta_from(stem, text)
    yi = int(year) if year and year.isdigit() else None
    ms = re.search(r"(\d{1,2})\s*[ηΗ]?\s+(ΓΕΝΙΚΗ|ΕΙΔΙΚΗ)\s+ΣΥΝΕΔΡΙΑΣΗ\s+(\d{4})", stem) \
        or re.search(r"(?:Γ\.Σ\.|ΕΙΔ\s+ΣΥΝ)\s*(\d{1,2})[ΑΒA-B]?\s+(?:()?)(\d{4})", stem)
    if ms and (kind == "Πρακτικά" or not (num and year)):
        kind = "Πρακτικά"
        n_, typ, y_ = ms.group(1), (ms.group(2) or ""), ms.group(3)
        typ = "Γεν." if (typ == "ΓΕΝΙΚΗ" or stem.startswith("Γ.Σ.")) else "Ειδ."
        num, year, yi = str(int(n_)), y_, int(y_)
        form = "Ολομέλεια"
        citation = f"ΕλΣυν Ολ. Πρακτ. {num}ης {typ} Συν./{year}"
    elif num and year:
        pre = {"Πράξη": "ΕλΣυν Πράξη", "Πρακτικά": "ΕλΣυν Πρακτ."}.get(kind, "ΕλΣυν")
        citation = f"{pre} {num}/{year}" + (f" ({form})" if form else "")
    else:
        citation = f"ΕλΣυν — {stem}"
    date, dkind = find_decision_date(text[-3000:], yi)
    if not date:
        date, dkind = find_decision_date(text, yi)
    paras = clean_paragraphs(paragraphs(text, "layout" if mode == "layout" else "wrapped"))
    chunks = chunk_decision(paras)
    doc = _doc(doc_id=doc_id, source="coa", citation=citation, title=f"{kind} {stem}", date=date, year=yi,
               number=num, court="Ελεγκτικό Συνέδριο", path=os.path.join(COA_DIR, name),
               meta={"kind": kind, "chamber": form, "md5": md5, "duplicates": list(dups),
                     "date_kind": dkind, "text_cache": os.path.join(COA_TXT, md5 + ".txt")})
    return doc, chunks


# ----------------------------------------------------------------------------------------------
# Εισαγγελία Αρείου Πάγου (eisap.gr)
# ----------------------------------------------------------------------------------------------
_EISAP_JUNK = {"loading...", "taking too long?", "reload document", "|", "open in new tab", "αποθηκευση αρχειου",
               "800x600", "normal", "0", "false", "true", "el", "x-none", "microsoftinternetexplorer4"}
_EISAP_TITLE = re.compile(r"(Γνωμοδότηση|Γνωμοδοτηση|Εγκύκλιος|Εγκυκλιος)\s*(?:υπ[’'΄]?\s*)?(?:αρ(?:ιθ)?μ?\.?\s*)?(\d{1,4})\s*/\s*(\d{4}|\d{2})")
_EISAP_PRESS = re.compile(r"ΔΕΛΤΙΟ|Δελτίο|Δελτι_ο|_DT|ΔΤ_|Ομιλ|ΤΗΛΕΦΩΝΑ|Πρόγραμμα|ΠΡΟΣΚΛΗΣΗ|Πρόσκληση|EJTN|Λουξεμβούργο|Παράταση-Προθεσμίας|Συμμετοχή|επίσκεψη|Επίσκεψη|Διοργάνωση", re.I)


def _eisap_extract(r: dict) -> dict:
    title = r.get("title") or ""
    kind = r.get("kind")
    if kind == "other":
        m = _EISAP_TITLE.search(title)
        if m and title.lstrip().lower().startswith(("γνωμοδ", "εγκύκλ", "εγκυκλ")):
            kind = "gnomodotisi" if fold1(m.group(1)).startswith("γνωμ") else "egkyklios"
        elif "Γνωμοδοτήσεις" in (r.get("categories") or []):
            kind = "gnomodotisi"
    return {"id": r.get("id"), "kind": kind}


def _eisap_units() -> Iterator[Unit]:
    rows = _jsonl_index(EISAP_JSONL, "eisap", _eisap_extract)
    for off, ln, crc, ex in rows:
        if ex["kind"] not in ("gnomodotisi", "egkyklios"):
            continue          # news, obituaries, visits, old Drupal 'about' pages: not legal content
        doc_id = f"eisap:{ex['id']}"
        yield Unit(doc_id, "eisap", _fp("eisap", f"{crc:08x}"),
                   (lambda off=off, ln=ln, doc_id=doc_id, kind=ex["kind"]: _eisap_load(off, ln, doc_id, kind)))
    try:
        man = _pdf_manifest(EISAP_PDF, EISAP_TXT)
    except OSError:
        return
    for md5, name, dups, mode in _group_by_md5(man, 500):
        if _EISAP_PRESS.search(name):
            continue
        stem = os.path.splitext(name)[0].strip()
        doc_id = f"eisap:pdf:{stem}"
        yield Unit(doc_id, "eisap", _fp("eisap", md5, mode),
                   (lambda md5=md5, name=name, dups=tuple(dups), doc_id=doc_id, mode=mode:
                    _eisap_pdf_load(md5, name, dups, doc_id, mode)))


def _eisap_pdf_candidates(kind: str, num: str, yr: str) -> list:
    """Best-effort link post -> scanned PDF by filename (e.g. '10-20-ΓΝΩΜΟΔΟΤΗΣΗ.pdf', '13-2020-ΕΓΚΥΚΛΙΟΣ.pdf')."""
    try:
        names = os.listdir(EISAP_PDF)
    except OSError:
        return []
    word = "ΓΝΩΜ" if kind == "gnomodotisi" else "ΕΓΚ"
    yy = yr[-2:]
    out = []
    for n in names:
        if word not in n.upper():
            continue
        m = re.match(r"^(\d{1,4})[-_](\d{2}|\d{4})[-_.]", n)
        if m and m.group(1).lstrip("0") == num.lstrip("0") and m.group(2)[-2:] == yy:
            out.append(os.path.join(EISAP_PDF, n))
    return out


def _eisap_load(off, ln, doc_id, kind):
    r = _rec(EISAP_JSONL, off, ln)
    title = (r.get("title") or "").strip()
    lines = []
    for l in (r.get("text") or "").split("\n"):
        s = norm_line(l)
        if not s or fold1(s) in _EISAP_JUNK or re.fullmatch(r"[\d\s.,]+", s):
            continue
        lines.append(s)
    m = _EISAP_TITLE.search(title)
    num = yr = None
    if m:
        num = str(int(m.group(2)))
        yr = m.group(3) if len(m.group(3)) == 4 else (("20" if int(m.group(3)) < 40 else "19") + m.group(3))
    year = int(yr) if yr else (r.get("year") if isinstance(r.get("year"), int) else None)
    lab = "Γνωμ. ΕισΑΠ" if kind == "gnomodotisi" else "Εγκ. ΕισΑΠ"
    citation = f"{lab} {num}/{yr}" if num else f"{lab} — {title[:60]}"
    subtitle = lines[0] if lines else ""
    full_title = title + (f" — {subtitle}" if subtitle and len(subtitle) < 400 else "")
    body = "\n".join(lines)
    chunks = []
    if len(body) < HARD_MAX:
        chunks = _text_chunks([("κείμενο", (title + "\n" + body).strip())])
    else:
        paras = clean_paragraphs(paragraphs(body, "lines"))
        chunks = chunk_decision([title] + paras, pre_label="κείμενο")
    date = None
    dkind = None
    if body:
        date, dkind = find_decision_date(body, year)
    meta = {"kind": kind, "categories": r.get("categories"), "link": r.get("link"), "posted": r.get("date"),
            "subtitle": subtitle or None, "date_kind": dkind,
            "pdf_candidates": _eisap_pdf_candidates(kind, num, yr) if num and yr else [],
            "fulltext_in_post": len(body) > 1200}
    doc = _doc(doc_id=doc_id, source="eisap", citation=citation, title=full_title, date=date, year=year,
               number=num, court="Εισαγγελία Αρείου Πάγου", path=f"{EISAP_JSONL}#{r.get('id')}", meta=meta)
    return doc, chunks


def _eisap_pdf_load(md5, name, dups, doc_id, mode="layout"):
    with open(os.path.join(EISAP_TXT, md5 + ".txt"), encoding="utf-8") as fh:
        text = fh.read()
    stem = os.path.splitext(name)[0].strip()
    m = _EISAP_TITLE.search(text[:1500])
    kind = "pdf"
    num = yr = None
    if m:
        kind = "gnomodotisi" if fold1(m.group(1)).startswith("γνωμ") else "egkyklios"
        num, yr = str(int(m.group(2))), m.group(3)
    year = int(yr) if yr and len(yr) == 4 else None
    date, dkind = find_decision_date(text[:3000], year)
    if not date:
        date = _first_date(fold1(text[:600]), year)
        dkind = "header" if date else None
    if not year and date:
        year = int(date[:4])
    lab = {"gnomodotisi": "Γνωμ. ΕισΑΠ", "egkyklios": "Εγκ. ΕισΑΠ"}.get(kind, "ΕισΑΠ")
    citation = f"{lab} {num}/{yr}" if num else f"ΕισΑΠ — {stem}"
    paras = clean_paragraphs(paragraphs(text, "layout" if mode == "layout" else "wrapped"))
    chunks = chunk_decision(paras, pre_label="κείμενο")
    doc = _doc(doc_id=doc_id, source="eisap", citation=citation, title=stem.replace("-", " "), date=date,
               year=year, number=num, court="Εισαγγελία Αρείου Πάγου", path=os.path.join(EISAP_PDF, name),
               meta={"kind": kind, "md5": md5, "duplicates": list(dups), "date_kind": dkind,
                     "text_cache": os.path.join(EISAP_TXT, md5 + ".txt")})
    return doc, chunks


# ----------------------------------------------------------------------------------------------
_ITERS = {"areios_pagos": _ap_units, "elsyn": _elsyn_units, "nsk": _nsk_units, "ste": _ste_units,
          "eisap": _eisap_units, "coa": _coa_units}


def iter_units(sources: Iterable[str] | None = None) -> Iterator[Unit]:
    """Yield a Unit for every case-law document available now. sources: subset of SOURCES."""
    want = [s for s in SOURCES if sources is None or s in set(sources)]
    for s in want:
        yield from _ITERS[s]()


if __name__ == "__main__":       # quick smoke: python -m lib.src_caselaw [source] [n]
    import sys
    src = sys.argv[1] if len(sys.argv) > 1 else None
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    k = 0
    for u in iter_units({src} if src else None):
        d, ch = u.load()
        print(json.dumps(d, ensure_ascii=False)[:600])
        for c in ch[:4]:
            print("  ", c["seq"], c["locator"], len(c["text"]), c["text"][:160].replace("\n", " | "))
        k += 1
        if k >= n:
            break
