import sys, json, time
from pathlib import Path
from collections import Counter
from paper_checkup.deterministic import analyze_structure, assemble_report

for d in sys.argv[1:]:
    t = time.time()
    ctx = analyze_structure(Path(d))
    rep = assemble_report(ctx)
    dt = time.time() - t
    print("=====", d, f"{dt:.2f}s", "json size KB:", len(rep.model_dump_json()) // 1024)
    print("stats:", rep.stats)
    print("edge vias:", Counter((e.kind, e.via) for e in rep.edges))
    print("risks:", Counter((r.category, r.severity) for r in rep.risks))
    for r in rep.risks[:12]:
        print("   -", r.severity, r.category, r.title[:90])
    print("symbols (first 25):")
    for s in rep.symbols[:25]:
        print(f"   {s.kind:6s} {s.latex[:30]:30s} uses={s.uses:3d} defs={[ (d.scope, d.pattern, d.loc.line_start) for d in s.definitions][:3]} flags={s.flags}")
    print("symbol kinds:", Counter(s.kind for s in rep.symbols))
