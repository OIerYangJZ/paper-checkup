import sys
from pathlib import Path
from paper_checkup.latex.source import load_project
from paper_checkup.latex.structure import parse_document

for d in sys.argv[1:]:
    flat = load_project(Path(d))
    p = parse_document(flat)
    print("=====", d, "main:", flat.main_file, "class:", p.info.documentclass)
    print("title:", p.info.title[:100], "| authors:", p.info.authors)
    print("theorem specs:", {k: (v.display, v.counter) for k, v in list(p.info.theorems.items())[:12]})
    print("counter_within:", p.info.counter_within, "alias:", p.info.counter_alias)
    print("sections:", [(s.number, s.title[:30]) for s in p.sections if s.level <= 1])
    print("blocks:", len(p.blocks), "proofs:", len(p.proofs), "eqs labeled:", len(p.equations), "labels:", len(p.labels), "refs:", len(p.refs), "cites:", len(p.cites), "math:", len(p.math))
    for b in p.blocks[:60]:
        prf = [(p.proofs[i].source, p.proofs[i].how) for i in b.proofs]
        print(f"  {b.id:12s} {b.display_name():22s} title={str(b.title)[:25]!r:28s} labels={b.labels} proofs={prf} in={b.contained_in_proof_of}")
    unowned = [(pp.source, (pp.title or '')[:40], flat.loc(pp.outer_start)) for pp in p.proofs if pp.owner is None]
    print("unowned proofs:", unowned[:10])
    print("eqs:", [(e.number, e.labels, e.owner_block) for e in p.equations[:15]])
    from collections import Counter
    print("ref contexts:", Counter(r.context for r in p.refs))
    undefined = sorted({k for r in p.refs for k in r.keys if k not in p.labels})
    print("undefined refs:", undefined[:20])
    print("dups:", p.duplicate_labels[:5])
