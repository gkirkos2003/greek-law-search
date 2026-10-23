"""Would boosting article headings help? For each ΦΕΚ eval question: BM25 rank of the gold article
chunk, and its rank after re-ranking BM25 top-200 by bm25norm + h·(query-term share in the heading)."""
import json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.searcher import Searcher
from lib.index import unpack
from lib import textnorm

HEAD = re.compile(r"^\W*[ΆΑA]ρ[θ9]ρ(?:ο|ον)\s+[\w΄']+\.?\s*(.{0,200}?)(?=\s(?:1\s*[.)]|\(1\)|Ι\.)\s|$)", re.S)

def heading(text):
    m = HEAD.match(text)
    return m.group(1).strip() if m else ""

s = Searcher(dense=False)
for l in open("eval/questions.jsonl", encoding="utf-8"):
    q = json.loads(l)
    g = q["gold"][0]
    if g["source"] != "fek_a" or q["lang"] != "el":
        continue
    bm = s.bm25(q["question"], k=200)
    ids = [c for c, _ in bm]
    rows = {r[0]: r[1:] for r in s.con.execute(
        f"SELECT c.id, c.law_key, c.article, c.text, c.doc, c.seq FROM chunks c WHERE c.id IN ({','.join('?'*len(ids))})", ids)}
    qt = set(textnorm.fts_query_terms(q["question"]))
    # heading of each candidate = heading of the first chunk of its article
    heads = {}
    for c in ids:
        lk, art, blob, doc, seq = rows[c]
        if art is None:
            heads[c] = ""; continue
        r = s.con.execute("SELECT text FROM chunks WHERE doc=? AND article=? ORDER BY seq LIMIT 1", (doc, art)).fetchone()
        heads[c] = heading(unpack(r[0]))
    b = dict(bm); lo, hi = min(b.values()), max(b.values()); rng = (hi - lo) or 1
    def rank(order):
        for i, c in enumerate(order, 1):
            if (rows[c][0], rows[c][1]) == (g["law_key"], str(g["article"])):
                return i
    base = rank(ids)
    out = [q["id"], "bm25", base]
    for h in (0.3, 0.6, 1.0):
        sc = {c: (b[c] - lo) / rng + h * (len(qt & set(textnorm.fts_query_terms(heads[c]))) / max(1, len(qt))) for c in ids}
        out += [f"h{h}", rank(sorted(ids, key=lambda c: -sc[c]))]
    gold_c = [c for c in ids if (rows[c][0], rows[c][1]) == (g["law_key"], str(g["article"]))]
    out.append("head=" + (heads[gold_c[0]][:60] if gold_c else "-"))
    print(*out)
