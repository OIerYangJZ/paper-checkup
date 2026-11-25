"""The deterministic half of the pipeline: everything that does not need an LLM."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .analysis.bibliography import build_bibliography
from .analysis.checks import Checker
from .analysis.figures import build_figures
from .analysis.graph import STRUCT_KINDS, GraphBuilder
from .analysis.symbols import extract_symbols
from .latex.preamble import katex_macros
from .latex.scan import strip_latex_to_text
from .latex.source import FlatDocument, load_project
from .latex.structure import ParsedPaper, parse_document
from .models import Edge, Node, PaperMeta, PipelineInfo, Report, Risk, Symbol


@dataclass
class AnalysisContext:
    root: Path
    flat: FlatDocument
    paper: ParsedPaper
    gb: GraphBuilder
    nodes: dict[str, Node]
    edges: list[Edge]
    symbols: list[Symbol]
    risks: list[Risk]
    checker: Checker
    meta: PaperMeta
    extra: dict = field(default_factory=dict)

    def node_name(self, nid: str) -> str:
        n = self.nodes[nid]
        if n.kind == "external":
            return f"文献 {n.title}"
        if n.kind == "equation":
            return f"公式 ({n.number})"
        return f"{n.display} {n.number}" if n.number else (f"{n.display}（{strip_latex_to_text(n.title)}）" if n.title else n.display)


def slugify(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-").lower()
    return s or "paper"


def analyze_structure(root: Path, paper_id: str | None = None, arxiv_id: str | None = None) -> AnalysisContext:
    flat = load_project(Path(root))
    paper = parse_document(flat)
    bib = build_bibliography(flat.text, flat.files)
    gb = GraphBuilder(paper, bib).build()
    nodes = gb.nodes
    symbols, sym_edges = extract_symbols(paper, gb)
    for e in sym_edges:
        if (e.source, e.target) not in gb.edges:
            gb.edges[(e.source, e.target)] = e
    edges = list(gb.edges.values())
    checker = Checker(paper, gb, nodes, edges, symbols)
    risks = checker.run()

    abstract = None
    if paper.abstract:
        abstract = re.sub(r"\s+", " ", flat.text[paper.abstract[0]:paper.abstract[1]]).strip()
    title = paper.info.title or Path(flat.main_file).stem
    pid = paper_id or slugify(arxiv_id or Path(flat.main_file).stem)
    meta = PaperMeta(id=pid, title=title, authors=paper.info.authors, abstract=abstract, arxiv_id=arxiv_id,
                     main_file=flat.main_file, documentclass=paper.info.documentclass, mode=gb.mode)
    return AnalysisContext(root=Path(root), flat=flat, paper=paper, gb=gb, nodes=nodes, edges=edges,
                           symbols=symbols, risks=risks, checker=checker, meta=meta)


def collect_sources(ctx: AnalysisContext, nodes: list[Node], risks: list[Risk]) -> dict[str, str]:
    """Only ship source files that some location in the report points into (skips figure data files)."""
    used = {ctx.flat.main_file}
    for n in nodes:
        if n.loc:
            used.add(n.loc.file)
        for p in n.proofs:
            used.add(p.loc.file)
    for s in ctx.symbols:
        for d in s.definitions:
            used.add(d.loc.file)
        if s.first_use:
            used.add(s.first_use.file)
    for r in risks:
        if r.loc:
            used.add(r.loc.file)
    for sec in ctx.gb.sections:
        if sec.loc:
            used.add(sec.loc.file)
    return {f: c for f, c in ctx.flat.files.items() if f in used and len(c) < 600_000}


def assemble_report(ctx: AnalysisContext, pipeline: PipelineInfo | None = None) -> Report:
    nodes = list(ctx.nodes.values())
    kind_rank = {"external": 2, "equation": 1} if ctx.gb.mode == "theorem" else {"external": 2}
    nodes.sort(key=lambda n: (kind_rank.get(n.kind, 0), n.order if n.order >= 0 else 0, n.id))
    edges = ctx.edges
    for i, e in enumerate(edges):
        e.id = f"e{i + 1}"
    risks = ctx.risks
    sev_rank = {"high": 0, "medium": 1, "low": 2, "info": 3}
    risks.sort(key=lambda r: (sev_rank.get(r.severity, 9), 0 if r.source == "review" else 1, r.id))
    sources = collect_sources(ctx, nodes, risks)
    blocks = [n for n in nodes if n.kind not in ("external", "equation") and n.kind not in STRUCT_KINDS]
    stats = {
        "sections": sum(1 for n in nodes if n.kind == "section"),
        "figures": sum(1 for n in nodes if n.kind in ("figure", "pseudocode", "listing")),
        "tables": sum(1 for n in nodes if n.kind == "table"),
        "claims": sum(1 for n in nodes if n.kind == "assertion"),
        "nodes": len(blocks),
        "definitions": sum(1 for n in blocks if n.kind == "definition"),
        "results": sum(1 for n in blocks if n.kind in ("theorem", "lemma", "proposition", "corollary", "claim", "fact")),
        "theorems": sum(1 for n in blocks if n.kind == "theorem"),
        "lemmas": sum(1 for n in blocks if n.kind == "lemma"),
        "proofs": sum(1 for n in blocks if n.proofs),
        "equations": sum(1 for n in nodes if n.kind == "equation"),
        "externals": sum(1 for n in nodes if n.kind == "external"),
        "edges_explicit": sum(1 for e in edges if e.kind == "explicit"),
        "edges_inferred": sum(1 for e in edges if e.kind == "inferred"),
        "edges_symbol": sum(1 for e in edges if e.kind == "symbol"),
        "symbols": len(ctx.symbols),
        "risks": len(risks),
        "risks_review": sum(1 for r in risks if r.source == "review"),
        "total_chars": sum(n.statement_chars + n.proof_chars for n in blocks) if blocks else
                       sum(n.statement_chars for n in nodes if n.kind == "section"),
        "source_chars": len(ctx.flat.text) - ctx.paper.body_start,
    }
    if pipeline is None:
        pipeline = PipelineInfo(version=__version__, generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                llm_enabled=False)
    return Report(
        paper=ctx.meta, pipeline=pipeline, macros=katex_macros(ctx.paper.info),
        text_macros={k: ctx.paper.info.macros[k].body for k in list(ctx.paper.info.ref_macros) + sorted(ctx.paper.info.cite_macros)
                     if k in ctx.paper.info.macros},
        theorem_envs={k: v.display for k, v in ctx.paper.info.theorems.items()},
        sections=ctx.gb.sections,
        nodes=nodes, edges=edges, labels=ctx.gb.labels, citations=ctx.gb.citations, symbols=ctx.symbols,
        risks=risks, sources=sources, stats=stats,
        figures=ctx.extra.get("figures") or build_figures(ctx), assets=ctx.extra.get("assets"),
        guide=ctx.extra.get("guide"))
