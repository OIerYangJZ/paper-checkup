"""Print the LLM-derived parts of a report for manual quality review."""
import json
import sys
from collections import Counter

r = json.load(open(sys.argv[1]))
p = r["pipeline"]
print(f"== {r['paper']['title']}")
print(f"models={p['models']} calls={p['llm_calls']} cached={p['llm_cached']} in={p['tokens_in']} out={p['tokens_out']} "
      f"reasoning={p.get('tokens_reasoning')} cached_tokens={p.get('tokens_cached')}")
cost = (p["tokens_in"] - (p.get("tokens_cached") or 0)) * 8e-6 + (p.get("tokens_cached") or 0) * 0.4e-6 + p["tokens_out"] * 40e-6
print(f"≈ ${cost:.2f} (gpt-6.2-astra list price)")
for s in p["stages"]:
    print(f"  [{s['status']}] {s['detail']} ({s.get('seconds')}s)")
nodes = {n["id"]: n for n in r["nodes"]}
print("\n-- review status:", Counter((n.get("review") or {}).get("status") for n in r["nodes"] if n.get("review")))
for n in r["nodes"]:
    rv = n.get("review")
    if rv and rv.get("status") in ("passed", "issues", "error"):
        print(f"   {n['id']:10s} {rv}")
print("\n-- inferred edges (llm):")
for e in r["edges"]:
    if e["via"] == "llm":
        print(f"   {e['source']} -> {e['target']} conf={e.get('confidence')} | {e.get('reason')}\n        quote: {e['evidence'][0]['text'][:120]!r}")
print("\n-- external knowledge (llm):")
for n in r["nodes"]:
    if n["id"].startswith("ext-k-"):
        print(f"   {n['title']} — {n['statement'][:100]}")
print("\n-- risks:")
for k in r["risks"]:
    loc = f"{k['loc']['file']}:{k['loc']['line_start']}" if k.get("loc") else "-"
    print(f"\n [{k['severity']}] {k['category']} / {k['source']} / {k.get('status')} | {k['title']} | nodes={k['node_ids']} @ {loc}")
    if k.get("quote"):
        print(f"    quote: {k['quote'][:200]!r}")
    print(f"    desc: {k['description'][:500]}")
    for t in k.get("debate") or []:
        print(f"    · {t['role']}/{t['stage']}: {t['content'][:400]}")
print("\n-- symbols (sample):")
for s in r["symbols"][:25]:
    print(f"   {s['latex'][:28]:28s} {s['kind']:6s} {s.get('meaning')!s:.80s} flags={s['flags']}")
