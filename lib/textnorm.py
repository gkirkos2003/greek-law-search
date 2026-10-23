"""Greek text folding + stemming for the FTS index.

SQLite's unicode61 tokenizer does NOT strip Greek accents (tested: 'αρθρο' does not
match 'άρθρου'), so we feed FTS5 an already-normalised token stream:
  fold   : NFD, drop combining marks, lowercase, final ς -> σ
  fix    : OCR Latin look-alikes inside Greek words (Α/A, Ο/O ...) -> Greek
  stem   : Snowball Greek (PyStemmer) for Greek words, so νόμος/νόμου/νόμων -> νομ
  stop   : very frequent function words dropped (same at index and query time,
           so phrase queries still line up)
Numbers are kept as tokens; "4412/2016" -> "4412 2016" (adjacent -> phrase-matchable).
"""
import re
import unicodedata
from functools import lru_cache

import Stemmer

_stem = Stemmer.Stemmer("greek")

# Latin capital/small letters that OCR confuses with Greek ones
_LAT2GR = str.maketrans({
    "A": "Α", "B": "Β", "E": "Ε", "Z": "Ζ", "H": "Η", "I": "Ι", "K": "Κ", "M": "Μ",
    "N": "Ν", "O": "Ο", "P": "Ρ", "T": "Τ", "Y": "Υ", "X": "Χ",
    "a": "α", "o": "ο", "v": "ν", "u": "υ", "i": "ι", "k": "κ", "x": "χ", "y": "γ",
    "p": "ρ", "t": "τ", "e": "ε", "n": "η",
})
_GREEK = re.compile(r"[Ͱ-Ͽἀ-῿]")
_LATIN = re.compile(r"[A-Za-z]")
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_ALL_LOOKALIKE = re.compile(r"[ABEZHIKMNOPTYX]+")
# omicron (Greek or Latin, any case) written for zero inside a number: 4ο12 -> 4012
_NUMRUN = re.compile(r"\d[\dοΟoO]*\d")


def _zero_fix(m):
    return re.sub(r"[οΟoO]", "0", m.group(0))

STOP = set("""
και κι η ο το οι τα του της των τον την τη τους τις στο στη στην στον στα στις στους στου
σε με για απο απ ως οτι να θα δε δεν μη μην ειναι εινε ηταν εις εν επι προς παρα υπο κατα
μετα δια περι αυτου αυτης αυτων αυτο αυτη αυτα αυτον αυτην ενα ενας μια μιας ενος που πως
οπως ή ητοι ουτε ουδε αλλα ομως εαν αν τε δη ον οντα ουσα ειτε τοις ταις ταυτα ταυτης
the of and to in a is for on by with
""".split())


def strip_accents(s: str) -> str:
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return unicodedata.normalize("NFC", s)


def fold(s: str) -> str:
    """Accent/case fold, ς -> σ. Keeps everything else."""
    return strip_accents(s).lower().replace("ς", "σ")


def fix_mixed(tok: str) -> str:
    """A token mixing Greek and Latin letters is an OCR artefact: map Latin -> Greek."""
    if _LATIN.search(tok) and _GREEK.search(tok):
        return tok.translate(_LAT2GR)
    # all-caps token made only of Greek look-alike Latin capitals ("IANOYAPIOY")
    if len(tok) >= 3 and tok.isupper() and _ALL_LOOKALIKE.fullmatch(tok):
        return tok.translate(_LAT2GR)
    return tok


def _norm_token_uncached(tok: str) -> str:
    """One raw token -> index token ('' = drop)."""
    tok = fix_mixed(tok)
    if tok.isdigit():
        return tok.lstrip("0") or "0"
    f = fold(tok)
    if f in STOP:
        return ""
    if _GREEK.search(f):
        s = _stem.stemWord(f)
        return fold(s) if s else f
    return f


_CACHE = {}


def _norm_token(tok: str) -> str:
    r = _CACHE.get(tok)
    if r is None:
        if len(_CACHE) > 3_000_000:
            _CACHE.clear()
        r = _CACHE[tok] = _norm_token_uncached(tok)
    return r


# math/compat symbols used for Greek letters in some 2000s PDFs (∆ U+2206, µ U+00B5, Ω U+2126)
_SYM = str.maketrans({"\u2206": "Δ", "\u00b5": "μ", "\u2126": "Ω", "\u03c2": "σ"})


NORM_VERSION = 2      # 2: dotted abbreviations collapsed (Γ.Ε.ΜΗ. -> ΓΕΜΗ, Ε.Φ.Κ.Α. -> ΕΦΚΑ)
_ABBR = re.compile(r"(?<![^\W\d_])(?:[^\W\d_]{1,2}\.){2,}(?:[^\W\d_]{1,2}(?![^\W\d_]))?")


def _abbr(m):
    return m.group(0).replace(".", "")


def tokens(text: str):
    text = _NUMRUN.sub(_zero_fix, text.translate(_SYM))
    if "." in text:
        text = _ABBR.sub(_abbr, text)
    get = _CACHE.get
    out = []
    for tok in _TOKEN.findall(text):
        r = get(tok)
        if r is None:
            r = _norm_token(tok)
        if r:
            out.append(r)
    return out


def fts_text(text: str) -> str:
    """String to store in the FTS5 column (tokenizer = unicode61 on already-normalised text)."""
    return " ".join(tokens(text))


def fts_query_terms(q: str):
    return tokens(q)
