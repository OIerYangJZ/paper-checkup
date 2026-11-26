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
        if schema_name == "guide":
            return {"summary": "概括", "audience": "基础", "organization": "按内容", "mindmap": [
                {"key": "n1", "parent": None, "label": "研究问题", "note": "", "ids": ["sec-1", "no-such-id"]},
                {"key": "n2", "parent": "n1", "label": "子节点", "note": "说明", "ids": []},
                {"key": "n3", "parent": "n9", "label": "悬空父节点", "note": "", "ids": []}],
                "sections": [{"id": "sec-1", "summary": "第一节", "key_points": ["要点"]}, {"id": "bogus", "summary": "x", "key_points": []}],
                "concepts": [{"term": "术语", "explanation": "解释", "ids": ["sec-1"]}],
                "prerequisites": [{"name": "线性代数", "why": "到处用", "level": "basic"}],
                "questions": [{"question": "问题？", "hint": "看第一节", "ids": ["sec-1"]}]}, {}
        if schema_name == "derivation_steps":
            assert "待审查推导" in blocks[1]
            return {"steps": [{"step_id": "S1", "line": 20, "quote": "H = \\hbar\\omega", "claim": "代入 a",
                               "justification": "定义", "uses": ["eq-1"], "hypotheses_check": "无"}]}, {}
        if schema_name == "derivation_challenges":
            return {"challenges": [{"challenge_id": "C1", "step_id": "S1", "category": "algebra", "line": 28,
                                    "quote": "E_n = \\hbar\\omega", "concern": "从对易关系到能级跳过了基态论证",
                                    "affects": "proof", "error_type": "logic", "severity": "medium"}]}, {}
        if schema_name == "derivation_rebuttal":
            return {"responses": [{"challenge_id": "C1", "stance": "minor", "response": "标准结果", "evidence_quote": ""}]}, {}
        if schema_name == "derivation_verdict":
            return {"verdicts": [{"challenge_id": "C1", "verdict": "minor", "severity": "medium",
                                  "title": "能级推导跳步", "final_comment": "补一句基态论证"}]}, {}
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
    g = rep.guide
    assert g and [m["key"] for m in g["mindmap"]] == ["n1", "n2", "n3"]
    assert g["mindmap"][0]["ids"] == ["sec-1"]                 # unknown ids are dropped
    assert g["mindmap"][2]["parent"] is None                   # dangling parent -> first-level branch
    assert [x["id"] for x in g["sections"]] == ["sec-1"]
    assert st["guide"].status == "done"


def test_figure_images_from_source_files(tmp_path):
    from paper_checkup.analysis.figures import build_figures

    ctx = analyze_structure(FIXTURE)
    figs = {f.id: f for f in build_figures(ctx, tmp_path / "figs", render_latex=False)}
    fig = figs["fig-1"]
    assert fig.images == ["fig-1-1.png"] and fig.source == "files"
    assert (tmp_path / "figs" / "fig-1-1.png").read_bytes().startswith(b"\x89PNG")
    assert figs["tab-1"].images == [] and "LaTeX" in (figs["tab-1"].note or "")   # tables need LaTeX
    assert "sec-3" in figs["tab-1"].referenced_by                                  # Table~\\ref in Results
    # Without an output directory the report still lists the floats (captions, references), without images.
    assert {f.id for f in build_figures(ctx)} == {"tab-1", "fig-1"}


def test_uploaded_latex_cannot_read_outside_files(tmp_path):
    """TeX's paranoid mode lets absolute paths through; the OS sandbox must stop them (macOS + pdflatex only)."""
    import pytest

    from paper_checkup.analysis import figures as F

    if not F.sandbox_available() or not F._tool("pdflatex"):
        pytest.skip("needs macOS sandbox-exec and pdflatex")
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET")
    paper = tmp_path / "paper"
    paper.mkdir()
    (paper / "local.tex").write_text("LOCALOK")
    (paper / "main.tex").write_text(
        "\\documentclass{article}\\begin{document}\n"
        f"\\begin{{table}}\\caption{{a}}\\begin{{tabular}}{{l}}\\input{{{secret}}}\\end{{tabular}}\\end{{table}}\n"
        "\\begin{table}\\caption{b}\\begin{tabular}{l}\\input{local}\\end{tabular}\\end{table}\n"
        "\\end{document}\n")
    ctx = analyze_structure(paper)
    figs = {f.id: f for f in F.build_figures(ctx, tmp_path / "out", render_latex=True, sandbox=True)}
    assert figs["tab-1"].images == []                 # the absolute path outside the paper is not readable
    assert figs["tab-2"].images == ["tab-2.png"]      # the paper's own files are


def test_numeric_rules_for_statistics(tmp_path):
    from paper_checkup.analysis.checks import _same_value

    v = lambda raw, cmp=None: {"value": float(raw.rstrip("%")), "raw": raw, "cmp": cmp}
    assert _same_value(v("2"), v("2.0")) and _same_value(v("1.98"), v("2"))    # coarser precision decides
    assert not _same_value(v("41.8"), v("41.0"))
    assert _same_value(v("0.05", "<"), v("0.03")) and not _same_value(v("0.05", "<"), v("0.07"))
    (tmp_path / "main.tex").write_text(r"""\documentclass{article}
\begin{document}
\begin{abstract}
In the TRIAL-7 cohort, treatment reduced readmission (OR 2.1, 95\% CI 1.4--3.0, $p<0.05$).
\end{abstract}
\section{Introduction}
Readmission matters.
\section{Results}
In the TRIAL-7 cohort, treatment reduced readmission (OR 2.1, 95\% CI 1.4--3.1, $p=0.03$).
\end{document}
""")
    ctx = analyze_structure(tmp_path)
    titles = [r.title for r in ctx.risks if r.category == "numeric"]
    assert any("3.0" in t and "3.1" in t for t in titles), titles      # the CI bound changed
    assert not any("0.05" in t or "0.03" in t for t in titles), titles  # p < 0.05 and p = 0.03 agree


DERIV = Path(__file__).parent / "fixtures" / "derivation"


def test_derivation_targets_and_edges():
    from paper_checkup.llm.stages import select_derivation_targets

    ctx = analyze_structure(DERIV)
    assert ctx.gb.mode == "structure"
    assert select_derivation_targets(ctx) == ["sec-3"]          # 3 displays; the Model section has only 1
    e = _edges(ctx)
    assert ("eq-1", "eq-2") in e and ("eq-2", "eq-4") in e and ("eq-3", "eq-4") in e   # "Combining (2) and (3), ..."
    assert select_derivation_targets(analyze_structure(FIXTURE)) == []                 # the empirical paper has none


def test_derivation_review_with_fake_llm(monkeypatch, tmp_path):
    fake = StructFake()
    monkeypatch.setattr(LLMClient, "provider_for", lambda self, role: fake)
    cfg = LLMConfig(enabled=True, provider="fake", models={"extract": "fake", "author": "fake", "reviewer": "fake"},
                    cache_dir=tmp_path)
    rep = asyncio.run(run_pipeline(DERIV, config=cfg, use_cache=False))
    st = {s.name: s for s in rep.pipeline.stages}
    assert st["derivation_review"].status == "done"
    d = [r for r in rep.risks if r.id.startswith("D")]
    # a "minor" verdict is capped at low before the (fake) meta-review regrades it
    assert d and d[0].category == "algebra" and d[0].status == "minor" and (d[0].original_severity or d[0].severity) == "low"
    assert d[0].node_ids[0] == "sec-3"
    sec = next(n for n in rep.nodes if n.id == "sec-3")
    assert sec.review and sec.review["kind"] == "derivation"
    # the empirical paper: no derivation stage calls at all
    fake2 = StructFake()
    monkeypatch.setattr(LLMClient, "provider_for", lambda self, role: fake2)
    rep2 = asyncio.run(run_pipeline(FIXTURE, config=cfg, use_cache=False))
    assert {s.name: s for s in rep2.pipeline.stages}["derivation_review"].status == "skipped"
    assert not any(n.startswith("derivation") for _, n in fake2.calls)
