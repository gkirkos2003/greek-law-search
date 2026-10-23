import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _gold_labels import L, H
here = os.path.dirname(os.path.abspath(__file__))
rows = []
for fn, labels, split in (("xref_samples.jsonl", L, "dev"), ("xref_samples_targeted.jsonl", L, "dev"),
                          ("xref_samples_holdout.jsonl", H, "holdout")):
    for line in open(os.path.join(here, fn), encoding="utf-8"):
        r = json.loads(line)
        if r["id"] not in labels:
            continue
        r["split"] = split
        r["self_law"] = None
        r["labels"] = [list(x) for x in labels[r["id"]]]
        rows.append(r)
with open(os.path.join(here, "xref_gold.jsonl"), "w", encoding="utf-8") as f:
    for r in rows:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
for sp in ("dev", "holdout"):
    rs = [r for r in rows if r["split"] == sp]
    print(sp, len(rs), "snippets,", sum(len(r["labels"]) for r in rs), "labels")
