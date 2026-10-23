"""Debug one eval question: dense rank / cosine of gold chunks vs top hits."""
import json, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OMP_NUM_THREADS", "1")
from lib.searcher import Searcher
from lib.index import unpack
qid = sys.argv[1]
q = next(json.loads(l) for l in open("eval/questions.jsonl") if json.loads(l)["id"] == qid)
s = Searcher(dense=True, threads=1)
gold = [g["doc_id"] for g in q["gold"]]
print(q["question"]); print("terms:", s.query_terms(q["question"])[:40])
gids = [r[0] for g in gold for r in s.con.execute(
    "SELECT c.id FROM chunks c JOIN docs d ON d.id=c.doc WHERE d.doc_id=? ORDER BY c.seq", (g,))]
print("gold chunks:", len(gids))
qv = s.dense.encode_query(q["question"])
dh = s.dense.search(q["question"], k=400)
rank = {c: i for i, (c, _) in enumerate(dh)}
for c in gids[:8]:
    v = s.dense.vector(c)
    print("  gold", c, "stored" if v is not None else "-", None if v is None else round(float(v @ qv), 3), "dense rank", rank.get(c))
print("dense top:")
for c, sc in dh[:8]:
    r = s.con.execute("SELECT d.citation, c.locator FROM chunks c JOIN docs d ON d.id=c.doc WHERE c.id=?", (c,)).fetchone()
    print("  ", round(sc, 3), r)
bm = s.bm25(q["question"], k=200)
br = {c: i for i, (c, _) in enumerate(bm)}
print("gold bm25 ranks:", sorted(br[c] for c in gids if c in br)[:5])
