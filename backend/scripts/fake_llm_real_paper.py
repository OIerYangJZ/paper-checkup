"""Exercise all LLM stages on a real paper with a fake provider that quotes real text."""
import asyncio, re, sys, time
from pathlib import Path
from paper_checkup.llm.provider import BaseProvider, LLMClient, LLMConfig
from paper_checkup.pipeline import run_pipeline

class Fake(BaseProvider):
    provider = "fake"
    def __init__(self): super().__init__("fake")
    async def complete_json(self, system, blocks, schema, name, max_tokens, role):
        task = blocks[-1] if len(blocks) < 3 else blocks[1]
        lines = [l for l in re.findall(r"L(\d+)\| (.{30,})", task)]
        if name == "implicit_dependencies":
            ids = re.findall(r"### 条目 \[([^\]]+)\]", task)
            items = []
            for nid in ids:
                sec = task.split(f"### 条目 [{nid}]", 1)[1].split("### 条目", 1)[0]
                body = sec.split("【陈述】", 1)[-1]
                q = re.sub(r"\s+", " ", body.strip().split("\n")[0])[:40]
                items.append({"node_id": nid, "dependencies": [{"target_id": ids[0], "quote": q, "where": "statement", "confidence": 0.7, "reason": "fake"}] if nid != ids[0] else [], "external": []})
            return {"items": items}, {}
        if name == "symbol_meanings":
            return {"symbols": [{"id": i, "meaning": "含义"} for i in re.findall(r"id=(sym-\d+)", task)]}, {}
        if name == "symbol_author":
            return {"responses": [{"id": i, "position": "issue", "argument": "a", "quote": ""} for i in re.findall(r"id=(sym-\d+)", task)]}, {}
        if name == "symbol_reviewer":
            return {"verdicts": [{"id": i, "verdict": "unresolved", "severity": "low", "title": "t", "comment": "c"} for i in re.findall(r"id=(sym-\d+)", blocks[1])]}, {}
        if name == "support_author":
            return {"support_found": True, "kind": "proof_in_text", "line": 1, "quote": "", "argument": "x"}, {}
        if name == "support_reviewer":
            return {"verdict": "resolved", "severity": "low", "title": "t", "comment": "ok"}, {}
        if name == "author_steps":
            return {"steps": [{"step_id": "S1", "line": int(lines[0][0]) if lines else 1, "quote": lines[0][1][:50] if lines else "", "claim": "c", "justification": "j", "uses": [], "hypotheses_check": "无"}]}, {}
        if name == "reviewer_challenges":
            if not lines: return {"challenges": []}, {}
            ln, txt = lines[min(2, len(lines)-1)]
            return {"challenges": [{"challenge_id": "C1", "step_id": "S1", "category": "precondition", "line": int(ln), "quote": txt[:45], "concern": "前提未验证", "severity": "medium"}]}, {}
        if name == "author_rebuttal":
            return {"responses": [{"challenge_id": "C1", "stance": "defend", "response": "r", "evidence_quote": ""}]}, {}
        if name == "reviewer_verdict":
            return {"verdicts": [{"challenge_id": "C1", "verdict": "minor", "severity": "medium", "title": "前提未验证", "final_comment": "f"}]}, {}
        if name == "proof_owner":
            return {"proofs": []}, {}
        if name == "prewarm":
            return {"ok": True}, {}
        if name == "external_merge":
            return {"groups": []}, {}
        if name == "meta_review":
            return {"decisions": [{"risk_id": i, "action": "keep", "merge_into": None, "severity": "medium", "rationale": "r"}
                                  for i in re.findall(r"^### (\S+?)（", "\n".join(blocks), re.M)]}, {}
        raise AssertionError(name)

fake = Fake()
LLMClient.provider_for = lambda self, role: fake
cfg = LLMConfig(enabled=True, provider="fake", models={"extract": "f", "author": "f", "reviewer": "f"}, cache_dir=Path("/tmp/pc_fake_cache"))
t = time.time()
rep = asyncio.run(run_pipeline(Path(sys.argv[1]), config=cfg, use_cache=False, review_max=40))
review = [r for r in rep.risks if r.source == "review" and r.category == "precondition"]
located = [r for r in review if r.quote]
print(f"{time.time()-t:.1f}s stages:", [(s.name, s.status) for s in rep.pipeline.stages])
print("inferred edges:", rep.stats["edges_inferred"], "| review risks:", len(review), "located quotes:", len(located))
bad = [(r.node_ids[0], r.loc.file, r.loc.line_start) for r in located if not (rep.sources.get(r.loc.file) or "").split("\n")[r.loc.line_start-1].strip()]
print("risks whose located line is blank:", bad[:5])
for r in located[:3]:
    line = rep.sources[r.loc.file].split("\n")[r.loc.line_start-1]
    print(f"  {r.node_ids[0]} -> {r.loc.file}:{r.loc.line_start} | quote={r.quote[:40]!r} | line={line.strip()[:60]!r}")
