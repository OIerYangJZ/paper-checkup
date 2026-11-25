"""Report schema shared by the backend pipeline and the frontend (see frontend/src/types.ts)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1

EdgeKind = Literal["explicit", "inferred", "symbol"]
Severity = Literal["high", "medium", "low", "info"]


class Loc(BaseModel):
    file: str
    line_start: int
    line_end: int


class ProofInfo(BaseModel):
    loc: Loc
    text: str
    source: str            # env | section | paragraph
    how: str               # how the owner was determined (ref-in-title, adjacent, lead-in-sentence, llm, ...)
    title: str | None = None


class Mention(BaseModel):
    loc: Loc
    text: str              # the sentence that mentions the node
    section_id: str | None = None


class ExternalInfo(BaseModel):
    source: Literal["citation", "llm"]
    cite_key: str | None = None
    description: str | None = None
    evidence: str | None = None


class Node(BaseModel):
    id: str
    kind: str              # theorem lemma proposition corollary definition ... equation external
    env: str | None = None
    display: str           # "Lemma"
    number: str | None = None
    title: str | None = None
    labels: list[str] = Field(default_factory=list)
    statement: str = ""
    proofs: list[ProofInfo] = Field(default_factory=list)
    loc: Loc | None = None
    section_id: str | None = None
    order: int = 0
    parent: str | None = None              # nested inside another node's proof
    lead_in: str | None = None
    flags: list[str] = Field(default_factory=list)
    cites: list[str] = Field(default_factory=list)
    symbols_used: list[str] = Field(default_factory=list)
    mentions: list[Mention] = Field(default_factory=list)
    external: ExternalInfo | None = None
    statement_chars: int = 0
    proof_chars: int = 0
    review: dict | None = None             # {"status": passed|issues|error|skipped, "challenges": n, ...}
    notes: list[str] = Field(default_factory=list)


class Evidence(BaseModel):
    loc: Loc | None = None
    text: str = ""
    via: str = ""          # ref eqref cite contained nested text-number symbol term llm
    context: str = ""      # statement | proof | lead-in


class Edge(BaseModel):
    id: str
    source: str            # prerequisite
    target: str            # dependent
    kind: EdgeKind
    context: str           # statement | proof  (strongest context among evidence)
    via: str
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float | None = None
    reason: str | None = None


class SymbolDef(BaseModel):
    loc: Loc
    node_id: str | None = None
    context: str           # sentence around the definition
    scope: str             # definition | prose | statement | proof | macro
    pattern: str


class Symbol(BaseModel):
    id: str
    latex: str
    kind: Literal["symbol", "macro", "term"]
    meaning: str | None = None
    meaning_source: str | None = None
    macro_expansion: str | None = None
    definitions: list[SymbolDef] = Field(default_factory=list)
    defined_in_node: str | None = None
    first_use: Loc | None = None
    first_use_node: str | None = None
    first_use_context: str | None = None
    uses: int = 0
    flags: list[str] = Field(default_factory=list)
    note: str | None = None


class DebateTurn(BaseModel):
    role: Literal["author", "reviewer"]
    stage: str
    content: str


class Risk(BaseModel):
    id: str
    category: str          # precondition symbol unsupported cycle gap reference structure
    severity: Severity
    source: Literal["structural", "review", "symbol"]
    title: str
    description: str
    node_ids: list[str] = Field(default_factory=list)
    loc: Loc | None = None
    quote: str | None = None
    status: str | None = None          # unresolved | conceded | flagged
    debate: list[DebateTurn] | None = None
    author_position: str | None = None
    reviewer_position: str | None = None
    symbol_id: str | None = None
    editor_note: str | None = None       # meta-review rationale (severity calibration / merge)
    merged_ids: list[str] = Field(default_factory=list)
    original_severity: str | None = None
    error_type: str | None = None        # logic | pseudocode_or_typesetting | typo_or_notation | degenerate_or_convention


class SectionInfo(BaseModel):
    id: str
    number: str | None
    title: str
    level: int
    parent: str | None = None
    loc: Loc | None = None
    appendix: bool = False


class Citation(BaseModel):
    key: str
    label: str | None = None
    number: int | None = None
    text: str | None = None
    title: str | None = None
    authors: str | None = None
    year: str | None = None
    used_in: list[str] = Field(default_factory=list)


class LabelInfo(BaseModel):
    kind: str
    node_id: str | None = None
    section_id: str | None = None
    display: str


class StageInfo(BaseModel):
    name: str
    status: Literal["done", "skipped", "error", "running", "pending"]
    seconds: float | None = None
    detail: str | None = None


class PaperMeta(BaseModel):
    id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    abstract: str | None = None
    arxiv_id: str | None = None
    main_file: str
    documentclass: str | None = None
    sources_redacted: bool = False       # public copies omit the full LaTeX sources
    mode: Literal["theorem", "structure"] = "theorem"   # structure: no theorem-like environments (read by section)


class PipelineInfo(BaseModel):
    version: str
    generated_at: str
    llm_enabled: bool
    models: dict[str, str] = Field(default_factory=dict)
    stages: list[StageInfo] = Field(default_factory=list)
    llm_calls: int = 0
    llm_cached: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cached: int = 0
    tokens_reasoning: int = 0
    cost_usd: float = 0.0                 # estimated spend of this analysis (token counts x list prices)
    budget_usd: float | None = None       # per-analysis spend limit, if any
    budget_note: str | None = None        # set when a spend limit stopped some LLM calls
    language: str = "zh"


class Report(BaseModel):
    schema_version: int = SCHEMA_VERSION
    paper: PaperMeta
    pipeline: PipelineInfo
    macros: dict[str, str] = Field(default_factory=dict)          # KaTeX-safe math macros
    text_macros: dict[str, str] = Field(default_factory=dict)     # ref/cite wrapper macros, e.g. \lemref
    theorem_envs: dict[str, str] = Field(default_factory=dict)    # env name -> display name
    sections: list[SectionInfo] = Field(default_factory=list)
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    labels: dict[str, LabelInfo] = Field(default_factory=dict)
    citations: dict[str, Citation] = Field(default_factory=dict)
    symbols: list[Symbol] = Field(default_factory=list)
    risks: list[Risk] = Field(default_factory=list)
    sources: dict[str, str] = Field(default_factory=dict)
    stats: dict[str, int | float] = Field(default_factory=dict)
