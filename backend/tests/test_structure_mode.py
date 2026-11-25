"""Papers without theorem-like environments: structure units, numeric cross-check, claim review (no network)."""

import asyncio
import re
from pathlib import Path

from paper_checkup.deterministic import analyze_structure
from paper_checkup.llm.provider import BaseProvider, LLMClient, LLMConfig
from paper_checkup.pipeline import run_pipeline

FIXTURE = Path(__file__).parent / "fixtures" / "empirical"
SAMPLE = Path(__file__).parent / "fixtures" / "sample"


def _edges(ctx):
    return {(e.source, e.target): e for e in ctx.edges}


def test_structure_units_and_edges():
    ctx = analyze_structure(FIXTURE)
    assert ctx.gb.mode == "structure" and ctx.meta.mode == "structure"
    kinds = {n.id: n.kind for n in ctx.nodes.values()}
    assert kinds["tab-1"] == "table" and kinds["fig-1"] == "figure" and kinds["eq-1"] == "equation"
    assert kinds["abstract"] == "section" and kinds["sec-3"] == "section"
    e = _edges(ctx)
    assert e[("tab-1", "sec-3")].kind == "explicit"            # Table~\ref in Results
    assert e[("sec-2", "sec-3")].kind == "explicit"            # Results points back to Method
    assert ("sec-3", "sec-1") not in e                         # forward pointer from the intro is navigation only
    assert e[("eq-1", "sec-2")].via == "eqref"
    assert e[("fig-1", "sec-3")].via == "text-number"          # "(Figure~1)" without \ref
    sym = next(s for s in ctx.symbols if s.latex == "d_{in}")
    assert sym.defined_in_node == "sec-2"
    assert ("sec-2", "sec-3") in e


def test_numeric_candidate():
    ctx = analyze_structure(FIXTURE)
    num = [r for r in ctx.risks if r.category == "numeric"]
    assert len(num) == 1
    r = num[0]
    assert "81.2" in r.title and "80.4" in r.title and r.severity == "low" and r.status == "flagged"
    assert "表格中出现了 80.4" in r.description and "没有出现 81.2" in r.description
    assert set(r.node_ids) == {"abstract", "sec-3"}


def test_theorem_papers_stay_in_theorem_mode():
    ctx = analyze_structure(SAMPLE)
    assert ctx.gb.mode == "theorem"
    assert not any(n.kind in ("section", "table") for n in ctx.nodes.values())


class StructFake(BaseProvider):
    provider = "fake"

    def __init__(self):
        super().__init__("fake-model")
        self.calls = []

    async def complete_json(self, system, blocks, schema, schema_name, max_tokens, role):
        self.calls.append((role, schema_name))
        if schema_name == "prewarm":
            return {"ok": True}, {}
        if schema_name == "symbol_meanings":
            return {"symbols": []}, {}
        if schema_name in ("symbol_author", "symbol_reviewer"):
            return ({"responses": []} if schema_name == "symbol_author" else {"verdicts": []}), {}
        if schema_name == "claims":
            assert "没有定理类环境" in system
            return {"claims": [
                {"line": 7, "quote": "WidgetNet reaches a top-1 accuracy of 81.2\\% while being 2.0 times faster than ResNet-50",
                 "kind": "result", "importance": "core", "summary": "精度与速度", "evidence_ids": ["tab-1", "fig-1"]},
                {"line": 3, "quote": "this sentence is not in the paper", "kind": "other", "importance": "supporting",
                 "summary": "幻觉", "evidence_ids": []}]}, {}
        if schema_name == "claim_steps":
            assert "tab-1" in blocks[1]
            return {"steps": [{"step_id": "S1", "line": 27, "quote": "WidgetNet & 80.4", "claim": "表 1",
                               "justification": "j", "uses": ["tab-1"], "hypotheses_check": "无"}]}, {}
        if schema_name == "claim_challenges":
            return {"challenges": [{"challenge_id": "C1", "step_id": "S1", "category": "numeric", "line": 7,
                                    "quote": "top-1 accuracy of 81.2\\%", "concern": "摘要 81.2 与 tab-1 的 80.4 不一致",
                                    "affects": "statement", "error_type": "logic", "severity": "high"}]}, {}
        if schema_name == "claim_rebuttal":
            return {"responses": [{"challenge_id": "C1", "stance": "concede", "response": "摘要写错", "evidence_quote": ""}]}, {}
        if schema_name == "claim_verdict":
            return {"verdicts": [{"challenge_id": "C1", "verdict": "unresolved", "severity": "high",
                                  "title": "摘要精度与表格不符", "final_comment": "以表格为准"}]}, {}
        if schema_name == "numeric_check":
            ids = re.findall(r"id=(S\d+)", blocks[-1])
            return {"items": [{"id": i, "verdict": "inconsistent", "severity": "medium", "title": "精度数字不一致",
                               "comment": "摘要 81.2%，表与正文 80.4%"} for i in ids]}, {}
        if schema_name == "meta_review":
            ids = re.findall(r"^### (\S+?)（", "\n".join(blocks), re.M)
            return {"decisions": [{"risk_id": i, "action": "keep", "merge_into": None,
                                   "severity": "high" if i.startswith("Q") else "medium", "rationale": "核心论断"} for i in ids]}, {}
        raise AssertionError(schema_name)


def test_structure_pipeline_with_fake_llm(monkeypatch, tmp_path):
    fake = StructFake()
    monkeypatch.setattr(LLMClient, "provider_for", lambda self, role: fake)
    cfg = LLMConfig(enabled=True, provider="fake", models={"extract": "fake", "author": "fake", "reviewer": "fake"},
                    cache_dir=tmp_path)
    rep = asyncio.run(run_pipeline(FIXTURE, config=cfg, use_cache=False))
    st = {s.name: s for s in rep.pipeline.stages}
    assert st["review"].status == "skipped" and st["claims"].status == "done" and st["numeric_check"].status == "done"
    claims = [n for n in rep.nodes if n.kind == "assertion"]
    assert len(claims) == 1 and claims[0].parent == "abstract"          # the hallucinated claim is dropped
    edges = {(e.source, e.target) for e in rep.edges}
    assert ("tab-1", "as-1") in edges and ("fig-1", "as-1") in edges
    q = [r for r in rep.risks if r.id.startswith("Q")]
    assert q and q[0].severity == "high" and q[0].category == "numeric" and "tab-1" in q[0].node_ids
    num = [r for r in rep.risks if r.category == "numeric" and r.id.startswith("S")]
    assert num and num[0].source == "review" and num[0].status == "unresolved" and num[0].severity == "medium"
    assert not any(name in ("author_steps", "reviewer_challenges") for _, name in fake.calls)
