"""End-to-end pipeline with a scripted fake LLM provider (no network)."""

import asyncio
import re
from pathlib import Path

from paper_checkup.llm.provider import BaseProvider, LLMClient, LLMConfig
from paper_checkup.pipeline import run_pipeline

FIXTURE = Path(__file__).parent / "fixtures" / "sample"


class FakeProvider(BaseProvider):
    provider = "fake"

    def __init__(self):
        super().__init__("fake-model")
        self.calls = []

    async def complete_json(self, system, blocks, schema, schema_name, max_tokens, role):
        self.calls.append((role, schema_name))
        task = blocks[-1]
        if schema_name == "implicit_dependencies":
            items = []
            if "[thm-1.1]" in task:
                items.append({"node_id": "thm-1.1", "dependencies": [
                    {"target_id": "lem-2.3", "quote": "the previous lemma gives the claim", "where": "proof",
                     "confidence": 0.8, "reason": "指前一个引理"},
                    {"target_id": "lem-4.1", "quote": "this text does not exist in the paper", "where": "proof",
                     "confidence": 0.9, "reason": "幻觉，应被丢弃"},
                ], "external": [
                    {"name": "Euclidean algorithm", "quote": "the weight satisfies", "where": "proof",
                     "description": "欧几里得算法"}]})
            return {"items": items}, {"input_tokens": 10, "output_tokens": 5}
        if schema_name == "symbol_meanings":
            return {"symbols": [{"id": "sym-1", "meaning": "测试含义"}]}, {}
        if schema_name == "symbol_author":
            return {"responses": []}, {}
        if schema_name == "symbol_reviewer":
            return {"verdicts": []}, {}
        if schema_name == "support_author":
            return {"support_found": False, "kind": "none", "line": 0, "quote": "", "argument": "找不到"}, {}
        if schema_name == "support_reviewer":
            return {"verdict": "unresolved", "severity": "medium", "title": "推论无证明", "comment": "需要补证明"}, {}
        if schema_name == "author_steps":
            return {"steps": [{"step_id": "S1", "line": 3, "quote": "Use Definition", "claim": "c",
                               "justification": "j", "uses": ["def-2.1"], "hypotheses_check": "无"}]}, {}
        if schema_name == "reviewer_challenges":
            if "# 待审查条目 [lem-2.2]" in blocks[1]:
                return {"challenges": [{"challenge_id": "C1", "step_id": "S1", "category": "gap", "line": 17,
                                        "quote": "It is easy to see that the bound holds",
                                        "concern": "此步省略了非平凡论证", "affects": "proof", "severity": "medium"},
                                       {"challenge_id": "C2", "step_id": None, "category": "symbol", "line": 16,
                                        "quote": "Use Definition", "concern": "记号可以更明确",
                                        "affects": "proof", "severity": "medium"}]}, {}
            return {"challenges": []}, {}
        if schema_name == "author_rebuttal":
            return {"responses": [{"challenge_id": "C1", "stance": "concede", "response": "确实省略", "evidence_quote": ""},
                                  {"challenge_id": "C2", "stance": "minor", "response": "只是表述", "evidence_quote": ""}]}, {}
        if schema_name == "reviewer_verdict":
            return {"verdicts": [{"challenge_id": "C1", "verdict": "unresolved", "severity": "medium",
                                  "title": "界的推导缺失", "final_comment": "读者需自行验证该界"},
                                 {"challenge_id": "C2", "verdict": "minor", "severity": "medium",
                                  "title": "记号表述", "final_comment": "不影响结论"}]}, {}
        if schema_name == "proof_owner":
            return {"proofs": []}, {}
        if schema_name == "prewarm":
            return {"ok": True}, {}
        if schema_name == "meta_review":
            ids = re.findall(r"^### (\S+?)（", "\n".join(blocks), re.M)
            return {"decisions": [{"risk_id": i, "action": "keep", "merge_into": None, "severity": "low",
                                   "rationale": "局部可修正"} for i in ids]}, {}
        if schema_name == "external_merge":
            return {"groups": []}, {}
        raise AssertionError(schema_name)


def test_pipeline_with_fake_llm(monkeypatch, tmp_path):
    fake = FakeProvider()
    monkeypatch.setattr(LLMClient, "provider_for", lambda self, role: fake)
    cfg = LLMConfig(enabled=True, provider="fake", models={"extract": "fake", "author": "fake", "reviewer": "fake"},
                    cache_dir=tmp_path)
    events = []
    rep = asyncio.run(run_pipeline(FIXTURE, config=cfg, use_cache=False, progress=events.append))
    edges = {(e.source, e.target): e for e in rep.edges}
    # The located implicit dependency upgrades nothing explicit; text-number edge already existed -> evidence merged.
    assert edges[("lem-2.3", "thm-1.1")].kind == "inferred"
    # Hallucinated quote is dropped.
    assert ("lem-4.1", "thm-1.1") not in edges
    assert any(n.id == "ext-k-euclidean-algorithm" for n in rep.nodes)
    # Review produced an unresolved, conceded risk with a debate transcript.
    review = [r for r in rep.risks if r.source == "review" and r.category == "gap"]
    assert review and review[0].status == "conceded" and len(review[0].debate) == 4
    assert review[0].loc.file == "sections/prelim.tex"
    # Unsupported corollary was debated and stays as a review risk.
    assert any(r.category == "unsupported" and r.source == "review" for r in rep.risks)
    assert not any(r.category == "unsupported" and r.source == "structural" for r in rep.risks)
    # Meta-review regraded the gap risk and attached the editor's rationale.
    assert review[0].severity == "low" and review[0].original_severity == "medium"
    assert review[0].editor_note == "局部可修正"
    lem = next(n for n in rep.nodes if n.id == "lem-2.2")
    assert lem.review["status"] == "issues" and lem.review["minor"] == 1
    # A "minor" verdict is kept as a minor-status risk capped at low severity (before meta-review regrading).
    minor = [r for r in rep.risks if r.status == "minor"]
    assert minor and minor[0].title == "记号表述" and minor[0].original_severity in (None, "low")
    assert rep.pipeline.llm_enabled and events
    assert all(s.status in ("done", "skipped") for s in rep.pipeline.stages)


def test_llm_disabled_per_run_is_reported(tmp_path):
    # The server has an LLM configured, but this run asked for deterministic analysis only.
    cfg = LLMConfig(enabled=True, provider="fake", models={"extract": "fake", "author": "fake", "reviewer": "fake"},
                    cache_dir=tmp_path)
    rep = asyncio.run(run_pipeline(FIXTURE, config=cfg, use_llm=False, use_cache=False))
    assert rep.pipeline.llm_enabled is False and rep.pipeline.models == {}
