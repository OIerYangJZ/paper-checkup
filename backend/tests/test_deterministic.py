from pathlib import Path

import pytest

from paper_checkup.analysis.graph import reading_path, strongly_connected_components
from paper_checkup.analysis.symbols import canon, defined_symbols_from_math
from paper_checkup.deterministic import analyze_structure, assemble_report
from paper_checkup.latex.source import load_project, strip_comment
from paper_checkup.latex.structure import parse_document

FIXTURE = Path(__file__).parent / "fixtures" / "sample"


@pytest.fixture(scope="module")
def ctx():
    return analyze_structure(FIXTURE)


def test_strip_comment():
    assert strip_comment(r"a 50\% b % comment") == r"a 50\% b "
    assert strip_comment(r"\\% x") == "\\\\"


def test_flatten_and_source_map():
    flat = load_project(FIXTURE)
    assert flat.main_file == "main.tex"
    i = flat.text.index("\\begin{definition}[Weight]")
    assert flat.loc(i) == ("sections/prelim.tex", 4)
    assert "lem:hidden" not in flat.text          # \iffalse block removed
    assert "commented-out-label" not in flat.text


def test_numbering_and_labels(ctx):
    nodes = ctx.nodes
    assert nodes["thm-1.1"].labels == ["thm:main"]
    assert nodes["def-2.1"].title == "Weight"
    assert nodes["lem-2.2"].labels == ["lem:bound"]
    assert nodes["claim-1"].labels == ["cl:one"]          # own counter, not shared
    assert nodes["lem-A.1"].labels == ["lem:app"]         # appendix numbering
    labels = ctx.gb.labels
    assert labels["eq:wt-bound"].display == "(2.1)"
    assert labels["eq:app1"].display == "(A.1)"
    assert labels["eq:app2"].display == "(A.2)"          # \nonumber line skipped
    assert labels["sec:prelim"].kind == "section"


def test_proof_ownership(ctx):
    thm = ctx.nodes["thm-1.1"]
    assert [p.source for p in thm.proofs] == ["section"]   # "Proof of Theorem~\ref{...}" section
    assert ctx.nodes["claim-1"].proofs                     # nested proof goes to the claim
    assert not ctx.nodes["cor-4.3"].proofs


def edge(ctx, s, t):
    return next((e for e in ctx.edges if e.source == s and e.target == t), None)


def test_edges(ctx):
    assert edge(ctx, "lem-2.2", "thm-1.1").kind == "explicit"            # via \lemref macro
    assert edge(ctx, "eq-2.1", "thm-1.1").via == "eqref"                 # standalone equation node
    assert edge(ctx, "lem-3.1", "thm-1.1").via == "contained"            # lemma inside the proof section
    assert edge(ctx, "claim-1", "lem-4.2").via == "nested"
    assert edge(ctx, "def-2.1", "lem-3.1").kind == "explicit"
    assert edge(ctx, "ext-KMM", "lem-3.1").via == "cite"
    e = edge(ctx, "lem-2.3", "thm-1.1")                                  # hard-coded "Lemma 2.3"
    assert e.kind == "inferred" and e.via == "text-number"
    assert edge(ctx, "lem-A.1", "lem-A.1") is None


def test_checks(ctx):
    cats = {(r.category, tuple(r.node_ids)) for r in ctx.risks}
    assert ("cycle", ("lem-4.1", "lem-4.2")) in cats
    assert any(r.category == "reference" and "lem:missing" in r.title for r in ctx.risks)
    assert ("unsupported", ("cor-4.3",)) in cats
    assert "lem-2.2" in ctx.checker.gap_hints                           # "It is easy to see"


def test_symbols(ctx):
    by_latex = {s.latex: s for s in ctx.symbols}
    assert "\\Zw" in by_latex and by_latex["\\Zw"].macro_expansion == "\\mathbb{Z}[\\omega]"
    term = next(s for s in ctx.symbols if s.kind == "term" and s.latex.lower() == "weight")
    assert term.defined_in_node == "def-2.1"
    assert canon(r"\mathcal D_{n}") == canon(r"\mathcal{D}_n")
    assert ["".join(p) for p in defined_symbols_from_math(r"x, y \in X")] == ["x", "y"]
    assert ["".join(p) for p in defined_symbols_from_math(r"f(x) := x^2")] == ["f"]


def test_reading_path(ctx):
    path = reading_path(ctx.nodes, ctx.edges, "thm-1.1", mode="understand")
    ids = [p["id"] for p in path]
    assert ids[-1] == "thm-1.1"
    assert "lem-2.2" in ids and "def-2.1" in ids
    # In "understand" mode, prerequisites' proofs are not required.
    assert next(p for p in path if p["id"] == "lem-2.2")["read"] == "statement"
    full = reading_path(ctx.nodes, ctx.edges, "thm-1.1", mode="verify")
    assert next(p for p in full if p["id"] == "lem-2.2")["read"] == "statement+proof"
    # topological: def-2.1 before lem-3.1 (lem-3.1 uses it)
    assert ids.index("def-2.1") < ids.index("lem-3.1")


def test_scc():
    comps = strongly_connected_components(["a", "b", "c"], [("a", "b"), ("b", "a"), ("b", "c")])
    assert sorted(map(sorted, comps)) == [["a", "b"], ["c"]]


def test_report_serializes(ctx):
    rep = assemble_report(ctx)
    js = rep.model_dump_json()
    assert '"schema_version":1' in js
    assert rep.macros["\\Zw"] == "\\mathbb{Z}[\\omega]"
    assert "main.tex" in rep.sources and "sections/prelim.tex" in rep.sources


def test_severity_caps():
    from paper_checkup.llm.stages import cap_severity
    assert cap_severity("high", "pseudocode_or_typesetting") == "medium"
    assert cap_severity("low", "pseudocode_or_typesetting") == "low"
    assert cap_severity("medium", "typo_or_notation") == "low"
    assert cap_severity("high", "degenerate_or_convention") == "info"
    assert cap_severity("high", "logic") == "high"
    assert cap_severity("medium", None) == "medium"
