// Mirrors backend/src/paper_checkup/models.py

export interface Loc {
  file: string;
  line_start: number;
  line_end: number;
}

export interface ProofInfo {
  loc: Loc;
  text: string;
  source: "env" | "section" | "paragraph" | string;
  how: string;
  title?: string | null;
}

export interface Mention {
  loc: Loc;
  text: string;
  section_id?: string | null;
}

export interface ExternalInfo {
  source: "citation" | "llm";
  cite_key?: string | null;
  description?: string | null;
  evidence?: string | null;
}

export interface ReviewInfo {
  status: "passed" | "issues" | "error" | "skipped";
  steps?: number;
  challenges?: number;
  unresolved?: number;
  minor?: number;
  error?: string;
}

export interface PaperNode {
  id: string;
  kind: string;
  env?: string | null;
  display: string;
  number?: string | null;
  title?: string | null;
  labels: string[];
  statement: string;
  proofs: ProofInfo[];
  loc?: Loc | null;
  section_id?: string | null;
  order: number;
  parent?: string | null;
  lead_in?: string | null;
  flags: string[];
  cites: string[];
  symbols_used: string[];
  mentions: Mention[];
  external?: ExternalInfo | null;
  statement_chars: number;
  proof_chars: number;
  review?: ReviewInfo | null;
  notes: string[];
}

export interface Evidence {
  loc?: Loc | null;
  text: string;
  via: string;
  context: string;
}

export type EdgeKind = "explicit" | "inferred" | "symbol";

export interface PaperEdge {
  id: string;
  source: string; // prerequisite
  target: string; // dependent
  kind: EdgeKind;
  context: "statement" | "proof" | string;
  via: string;
  evidence: Evidence[];
  confidence?: number | null;
  reason?: string | null;
}

export interface SymbolDef {
  loc: Loc;
  node_id?: string | null;
  context: string;
  scope: string;
  pattern: string;
}

export interface SymbolEntry {
  id: string;
  latex: string;
  kind: "symbol" | "macro" | "term";
  meaning?: string | null;
  meaning_source?: string | null;
  macro_expansion?: string | null;
  definitions: SymbolDef[];
  defined_in_node?: string | null;
  first_use?: Loc | null;
  first_use_node?: string | null;
  first_use_context?: string | null;
  uses: number;
  flags: string[];
  note?: string | null;
}

export interface DebateTurn {
  role: "author" | "reviewer";
  stage: string;
  content: string;
}

export type Severity = "high" | "medium" | "low" | "info";

export interface Risk {
  id: string;
  category: string;
  severity: Severity;
  source: "structural" | "review" | "symbol";
  title: string;
  description: string;
  node_ids: string[];
  loc?: Loc | null;
  quote?: string | null;
  status?: string | null;
  debate?: DebateTurn[] | null;
  author_position?: string | null;
  reviewer_position?: string | null;
  symbol_id?: string | null;
  editor_note?: string | null;
  merged_ids?: string[];
  original_severity?: Severity | null;
  error_type?: string | null;
}

export interface SectionInfo {
  id: string;
  number?: string | null;
  title: string;
  level: number;
  parent?: string | null;
  loc?: Loc | null;
  appendix: boolean;
}

export interface Citation {
  key: string;
  label?: string | null;
  number?: number | null;
  text?: string | null;
  title?: string | null;
  authors?: string | null;
  year?: string | null;
  used_in: string[];
}

export interface LabelInfo {
  kind: string;
  node_id?: string | null;
  section_id?: string | null;
  display: string;
}

export interface StageInfo {
  name: string;
  status: "done" | "skipped" | "error" | "running" | "pending";
  seconds?: number | null;
  detail?: string | null;
}

export interface Report {
  schema_version: number;
  paper: {
    id: string;
    title: string;
    authors: string[];
    abstract?: string | null;
    arxiv_id?: string | null;
    main_file: string;
    documentclass?: string | null;
    sources_redacted?: boolean;
  };
  pipeline: {
    version: string;
    generated_at: string;
    llm_enabled: boolean;
    models: Record<string, string>;
    stages: StageInfo[];
    llm_calls: number;
    llm_cached: number;
    tokens_in: number;
    tokens_out: number;
    language: string;
  };
  macros: Record<string, string>;
  text_macros: Record<string, string>;
  theorem_envs: Record<string, string>;
  sections: SectionInfo[];
  nodes: PaperNode[];
  edges: PaperEdge[];
  labels: Record<string, LabelInfo>;
  citations: Record<string, Citation>;
  symbols: SymbolEntry[];
  risks: Risk[];
  sources: Record<string, string>;
  stats: Record<string, number>;
}

export interface PresetEntry {
  id: string;
  title: string;
  authors: string[];
  arxiv_id?: string | null;
  file: string;
  blurb?: string | null;
  stats: Record<string, number>;
  llm: boolean;
  models: Record<string, string>;
  generated_at: string;
}

export interface JobStatus {
  id: string;
  status: "queued" | "running" | "done" | "error";
  message: string;
  stages: StageInfo[];
  progress: Record<string, [number, number]>;
  error?: string | null;
  title?: string | null;
  has_partial: boolean;
  has_report: boolean;
}

export interface Health {
  ok: boolean;
  version: string;
  llm: { enabled: boolean; provider: string; models: Record<string, string>; reason?: string | null; requires_token?: boolean };
}
