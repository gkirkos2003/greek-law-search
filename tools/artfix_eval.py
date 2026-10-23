"""Compare ΦΕΚ Α΄ chunking with FEK_ARTFIX=0/1 on a fixed sample. Writes work/artfix/<flag>.json"""
import collections, json, os, random, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import src_fek
units = [u for u in src_fek.iter_units({"fek_a"})]
random.Random(11).shuffle(units)
sample = units[:int(sys.argv[1]) if len(sys.argv) > 1 else 300]
out = {}
for u in sample:
    doc, ch = u.load()
    if doc is None:
        continue
    per = collections.Counter((c["law"], c["article"]) for c in ch if c["article"])
    out[u.doc_id] = {"year": doc["year"], "n": len(ch), "with_art": sum(1 for c in ch if c["article"]),
                     "arts": len(per), "maxper": max(per.values()) if per else 0,
                     "keys": sorted(f"{l}|{a}" for l, a in per)}
json.dump(out, open(f"work/artfix/{os.environ.get('FEK_ARTFIX','1')}.json", "w"), ensure_ascii=False)
print(len(out))
