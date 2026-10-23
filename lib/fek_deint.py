"""De-interleave PaddleOCR two-column ΦΕΚ pages (OCR lines were saved without box coordinates).

Paddle sorts boxes top-to-bottom, so a two-column page comes out as L,R,L,R,... (with drift).
We re-assign each line to column L or R, keeping order within each column, by beam search over
a continuation score between the previous line of a column and the candidate line:
  * line ends with a hyphen and   last-half + first-word   is a corpus word   (strong)
  * the cross-line word pair is a frequent within-line bigram in the corpus
  * punctuation / case agreement (sentence end -> capital; no end -> lowercase)
The two-column reading is accepted only if it clearly beats the single-column reading
(gates measured on synthetic data, see notes/fek.md); otherwise the page is left untouched.

Tables: hashed counts (2^24 buckets) of whole words and within-line bigrams, built from the OCR
corpus itself (katharevousa + OCR spelling) by tools/build_deint_tables.py ->
search/work/fek_deint_tables.npz.  No tables file -> de-interleaving is off.
"""
import os
import zlib

import numpy as np

HB = 1 << 24
TABLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "work", "fek_deint_tables.npz")
GATE_SW = 0.45       # min fraction of L<->R switches (synthetic: 2-col q05 0.62, 1-col q95 0.39)
GATE_GAIN = 0.5      # min score gain per line   (synthetic: 2-col q05 1.05, 1-col q95 0.32)
MARGIN = 3.0
BEAM = 8
DOUBLE_PEN = float(os.environ.get("FEK_DEINT_DOUBLE", "1.0"))   # cost of two lines in a row in one column
TERM = tuple(".:;»·!")
_STRIP = ".,;:·()«»\"'!;-"


def _h(s):
    return zlib.crc32(s.encode("utf-8")) & (HB - 1)


def _fold(w):
    import unicodedata
    s = unicodedata.normalize("NFD", w)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower().replace("ς", "σ")
    return s.strip(_STRIP)


class Tables:
    def __init__(self, uni=None, bi=None):
        self.uni = uni if uni is not None else np.zeros(HB, dtype=np.uint32)
        self.bi = bi if bi is not None else np.zeros(HB, dtype=np.uint32)

    def add_line(self, line):
        ws = [_fold(w) for w in line.split()]
        ws = [w for w in ws if w]
        if line.rstrip().endswith("-") and ws:
            ws = ws[:-1]
        for w in ws:
            self.uni[_h(w)] += 1
        for a, b in zip(ws, ws[1:]):
            self.bi[_h(a + " " + b)] += 1

    def word(self, w):
        return int(self.uni[_h(w)])

    def bigram(self, a, b):
        return int(self.bi[_h(a + " " + b)])

    def save(self, path=TABLES):
        tmp = path + ".part.npz"
        np.savez_compressed(tmp, uni=self.uni, bi=self.bi)
        os.replace(tmp, path)

    @classmethod
    def load(cls, path=TABLES):
        z = np.load(path)
        return cls(z["uni"], z["bi"])


_T = None


def tables():
    """Lazy singleton; None when the tables file does not exist."""
    global _T
    if _T is None:
        _T = Tables.load() if os.path.exists(TABLES) else False
    return _T or None


def cont_score(t, p, x):
    if p is None:
        return 0.0
    ps, xs = p.rstrip(), x.lstrip()
    if not ps or not xs:
        return 0.0
    pw = ps.split()[-1]
    xw = xs.split()[0]
    sc = 0.0
    if ps.endswith("-") and len(pw) > 1:
        joined = _fold(pw[:-1] + xw)
        if joined and t.word(joined) >= 2:
            sc += 4.0
        elif xs[:1].islower():
            sc += 0.3
        else:
            sc -= 1.5
        return sc
    a, b = _fold(pw), _fold(xw)
    if a and b:
        c = t.bigram(a, b)
        if c >= 2:
            sc += min(3.0, 0.8 + 0.5 * float(np.log1p(c)))
    if ps.endswith(TERM):
        sc += 0.5 if (xs[:1].isupper() or xs[:1].isdigit() or xs[:1] in "(«\"'") else -1.0
    else:
        sc += 0.8 if xs[:1].islower() else -0.3
    return sc


def deinterleave(t, lines, beam=BEAM, stats=None):
    """-> (lines in reading order, fired: bool)."""
    n = len(lines)
    if n < 12 or t is None:
        return lines, False
    single = sum(cont_score(t, lines[i - 1] if i else None, lines[i]) for i in range(n))
    states = [(0.0, -1, -1, ())]
    for i, x in enumerate(lines):
        best = {}
        cache = {}
        for sc, lL, lR, asg in states:
            for col, last in ((0, lL), (1, lR)):
                if last not in cache:
                    cache[last] = cont_score(t, lines[last] if last >= 0 else None, x)
                s = sc + cache[last] - (0.5 if (col == 1 and last < 0) else 0.0) \
                    - (DOUBLE_PEN if (asg and asg[-1] == col) else 0.0)
                k = (i, lR) if col == 0 else (lL, i)
                if k not in best or best[k][0] < s:
                    best[k] = (s, k[0], k[1], asg + (col,))
        states = sorted(best.values(), key=lambda s: -s[0])[:beam]
    sc, _, _, asg = states[0]
    sw = sum(1 for a, b in zip(asg, asg[1:]) if a != b) / max(1, n - 1)
    gain = (sc - single) / n
    minor = min(asg.count(0), asg.count(1))
    if stats is not None:
        stats.append((sw, gain, minor / n))
    if sc < single + MARGIN or minor < 4 or sw < GATE_SW or gain < GATE_GAIN:
        return lines, False
    global LAST_SPLIT
    # orientation: PaddleOCR puts the left box first within a row, so the stream that leads more
    # often in adjacent (A,B) cross-stream pairs (row = R then next row's L is ambiguous) is L
    lead = sum(1 for k in range(0, n - 1) if asg[k] != asg[k + 1] and (k == 0 or asg[k - 1] == asg[k + 1])
               and asg[k] == 0) - sum(1 for k in range(0, n - 1) if asg[k] != asg[k + 1] and
                                      (k == 0 or asg[k - 1] == asg[k + 1]) and asg[k] == 1)
    if lead < 0:
        asg = tuple(1 - a for a in asg)
    L = [lines[i] for i in range(n) if asg[i] == 0]
    LAST_SPLIT = len(L)
    return L + [lines[i] for i in range(n) if asg[i] == 1], True


LAST_SPLIT = None
