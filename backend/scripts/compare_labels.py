"""Side-by-side view of a new report's review risks and an earlier labeled set, grouped by item."""
import json
import sys
from collections import Counter, defaultdict

labels = json.load(open(sys.argv[1]))["labels"]
rep = json.load(open(sys.argv[2]))
order = ["high", "medium", "low", "info"]
new = [r for r in rep["risks"] if r["source"] == "review"]
p = rep["pipeline"]
cost = (p["tokens_in"] - (p.get("tokens_cached") or 0)) * 8e-6 + (p.get("tokens_cached") or 0) * 0.4e-6 + p["tokens_out"] * 40e-6
print(f"calls={p['llm_calls']} cached_calls={p['llm_cached']} in={p['tokens_in']} cached_tokens={p.get('tokens_cached')} "
      f"out={p['tokens_out']} -> ≈${cost:.2f} (counts include cached responses)")
for s in p["stages"]:
    print(f"  [{s['status']}] {s['detail']}")
print("\nnew review risks by severity:", dict(Counter(r["severity"] for r in new)),
      "| by status:", dict(Counter(r["status"] for r in new)))
print("labeled expectation:", dict(Counter(l["expected_severity"] or "invalid" for l in labels)))

by_node_new = defaultdict(list)
for r in new:
    by_node_new[r["node_ids"][0]].append(r)
by_node_lab = defaultdict(list)
for l in labels:
    by_node_lab[l["node"]].append(l)
for node in sorted(set(by_node_new) | set(by_node_lab)):
    print(f"\n===== {node}")
    for l in by_node_lab.get(node, []):
        print(f"  LABEL  {l['risk_id']:4s} expect={l['expected_severity'] or 'INVALID':7s} | {l['title']}")
    for r in sorted(by_node_new.get(node, []), key=lambda r: order.index(r["severity"])):
        orig = f" (reviewer {r['original_severity']})" if r.get("original_severity") else ""
        print(f"  NEW    {r['severity']:6s}{orig} [{r['status']}] {r['category']} | {r['title']}")
        if r.get("editor_note"):
            print(f"           主审: {r['editor_note'][:160]}")
reviews = {n["id"]: n.get("review") for n in rep["nodes"] if n.get("review")}
print("\nreview status:", {k: (v or {}).get("status") for k, v in reviews.items()})
print("unsupported nodes notes:", [(n["id"], n["notes"]) for n in rep["nodes"] if any("未找到形式化证明" in x for x in n.get("notes", []))])
