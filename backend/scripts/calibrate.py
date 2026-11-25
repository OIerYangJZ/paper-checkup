"""Measure real token usage on a small slice of one paper (results are cached and reused later)."""
import asyncio, json, sys, time
from pathlib import Path
from paper_checkup.settings import load_env
load_env()
from paper_checkup.deterministic import analyze_structure
from paper_checkup.llm.provider import LLMClient, LLMConfig
from paper_checkup.llm import stages
from paper_checkup.llm.context import digest

async def main(root, review_node):
    ctx = analyze_structure(Path(root))
    cfg = LLMConfig.from_env()
    client = LLMClient(cfg)
    t = time.time()
    s1 = await stages.stage_implicit(ctx, client)
    st = client.stats
    print(f"implicit: {time.time()-t:.0f}s calls={st.calls} in={st.tokens_in} out={st.tokens_out} (reasoning {st.reasoning_tokens}) cached={st.cache_read_tokens} -> {s1}")
    base = (st.calls, st.tokens_in, st.tokens_out, st.reasoning_tokens)
    t = time.time()
    risks = await stages._review_one(ctx, client, review_node, digest(ctx, max_statement=160))
    print(f"review {review_node}: {time.time()-t:.0f}s calls={st.calls-base[0]} in={st.tokens_in-base[1]} out={st.tokens_out-base[2]} "
          f"(reasoning {st.reasoning_tokens-base[3]}) cached_total={st.cache_read_tokens} review={ctx.nodes[review_node].review}")
    for r in risks:
        print("  RISK", r.severity, r.category, r.status, "|", r.title, "|", r.loc.file if r.loc else None, r.loc.line_start if r.loc else None)
        print("     concern:", r.description[:300])
        print("     final:", (r.reviewer_position or "")[:300])
    new_edges = [e for e in ctx.edges if e.via == "llm"]
    for e in new_edges[:12]:
        print("  EDGE", e.source, "->", e.target, e.confidence, "|", (e.reason or "")[:80], "|", e.evidence[0].text[:70])
    ext = [n for n in ctx.nodes.values() if n.id.startswith("ext-k-")]
    print("  EXTERNAL:", [n.title for n in ext])
    cost = (st.tokens_in - st.cache_read_tokens) * 8e-6 + st.cache_read_tokens * 0.4e-6 + st.tokens_out * 40e-6
    print(f"TOTAL calls={st.calls} in={st.tokens_in} out={st.tokens_out} reasoning={st.reasoning_tokens} cached={st.cache_read_tokens} ≈ ${cost:.2f}")

asyncio.run(main(sys.argv[1], sys.argv[2]))
