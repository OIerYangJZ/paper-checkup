import sys
from pathlib import Path
from paper_checkup.deterministic import analyze_structure
from paper_checkup.llm.context import digest
from paper_checkup.llm.stages import _implicit_item, review_context, select_review_targets, IMPLICIT_TARGET_KINDS

for d, rmax in zip(sys.argv[1::2], map(int, sys.argv[2::2])):
    ctx = analyze_structure(Path(d))
    dig = digest(ctx)
    targets = [n.id for n in ctx.nodes.values() if n.kind in IMPLICIT_TARGET_KINDS and n.order >= 0]
    items = sum(len(_implicit_item(ctx, t)) for t in targets)
    rev = select_review_targets(ctx, rmax)
    rcs = [len(review_context(ctx, t)) for t in rev]
    # rough: 1 token ~ 3.2 chars for LaTeX-heavy English
    tok = lambda c: int(c / 3.2)
    n_impl_batches = max(1, items // 14000 + 1)
    review_in = sum(4 * (len(dig) + r) + 6000 for r in rcs)   # 4 calls per target, plus growing debate text
    total_in = n_impl_batches * len(dig) + items + review_in
    print(f"{d}: digest={len(dig)} chars (~{tok(len(dig))} tok); implicit items={len(targets)} ({items} chars, ~{n_impl_batches} batches); "
          f"review targets={len(rev)} avg ctx={sum(rcs)//max(1,len(rcs))} chars max={max(rcs or [0])}; est. total input ≈ {tok(total_in)/1e3:.0f}k tokens")
    if rev:
        print("   sample review context head:\n" + "\n".join("     | " + l for l in review_context(ctx, rev[0]).splitlines()[:14]))
