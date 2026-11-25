import type { PaperNode, Severity } from "../types";

export const KIND_LABEL: Record<string, string> = {
  theorem: "定理",
  lemma: "引理",
  proposition: "命题",
  corollary: "推论",
  definition: "定义",
  conjecture: "猜想",
  claim: "断言",
  fact: "事实",
  assumption: "假设",
  notation: "记号",
  remark: "注记",
  example: "例子",
  problem: "问题",
  algorithm: "算法",
  other: "其他",
  equation: "公式",
  external: "外部",
  // structure mode (papers without theorem-like environments)
  section: "章节",
  figure: "图",
  table: "表",
  listing: "代码",
  pseudocode: "算法（浮动体）",
  assertion: "论断",
};

export const KIND_COLOR: Record<string, string> = {
  theorem: "#c2410c",
  proposition: "#7c3aed",
  lemma: "#2563eb",
  corollary: "#b45309",
  claim: "#4f46e5",
  fact: "#4f46e5",
  definition: "#0f766e",
  notation: "#0f766e",
  conjecture: "#be185d",
  assumption: "#9f1239",
  remark: "#64748b",
  example: "#64748b",
  problem: "#64748b",
  algorithm: "#0e7490",
  other: "#64748b",
  equation: "#475569",
  external: "#94a3b8",
  section: "#334155",
  figure: "#0d9488",
  table: "#0369a1",
  listing: "#64748b",
  pseudocode: "#0e7490",
  assertion: "#c2410c",
};

/** Kinds that structure the paper rather than state a result (structure mode). */
export const STRUCT_KINDS = new Set(["section", "figure", "table", "pseudocode", "listing", "assertion"]);

export const PROVABLE = new Set(["theorem", "lemma", "proposition", "corollary", "claim", "fact"]);

export const CATEGORY_LABEL: Record<string, string> = {
  precondition: "前提条件",
  symbol: "符号",
  unsupported: "无来源支撑",
  gap: "论证跳跃",
  circular: "循环论证",
  cycle: "循环依赖",
  reference: "引用",
  structure: "结构",
  numeric: "数字不一致",
  overclaim: "论断过度",
  comparison: "比较不对等",
  attribution: "归因缺乏对照",
  reproducibility: "可复现性",
  other: "其他",
};

export const SEVERITY_LABEL: Record<Severity, string> = { high: "高", medium: "中", low: "低", info: "提示" };
export const SEVERITY_COLOR: Record<Severity, string> = {
  high: "#d23f3a",
  medium: "#d9820f",
  low: "#3a6fc4",
  info: "#8b93a5",
};

export const STATUS_LABEL: Record<string, string> = {
  unresolved: "争议未决",
  conceded: "作者承认",
  minor: "次要",
  flagged: "程序检测",
};

export const ERROR_TYPE_LABEL: Record<string, string> = {
  pseudocode_or_typesetting: "伪代码 / 排版",
  typo_or_notation: "笔误 / 记号",
  degenerate_or_convention: "退化情形 / 约定",
};

export const EDGE_KIND_LABEL: Record<string, string> = { explicit: "确定", inferred: "推断", symbol: "记号" };
export const VIA_LABEL: Record<string, string> = {
  ref: "\\ref 引用",
  eqref: "公式引用",
  cite: "文献引用",
  contained: "位于其证明章节内",
  nested: "嵌套于其证明内",
  "text-number": "正文直接写出编号",
  llm: "大模型推断",
  symbol: "使用其定义的记号",
  term: "使用其定义的术语",
};

export const PROOF_HOW_LABEL: Record<string, string> = {
  "ref-in-title": "证明标题中的 \\ref",
  "text-in-title": "证明标题中的编号",
  "lead-in-sentence": "证明前一句“to prove …”",
  "first-sentence": "证明首句",
  adjacent: "紧随陈述之后",
  llm: "大模型判断",
};

export const PROOF_SOURCE_LABEL: Record<string, string> = {
  env: "proof 环境",
  section: "整节证明",
  paragraph: "证明段落",
};

export function nodeName(n: PaperNode | undefined): string {
  if (!n) return "?";
  if (n.kind === "external") return n.title || n.display;
  if (n.kind === "equation") return `公式 (${n.number ?? "?"})`;
  if (n.kind === "assertion") return `论断 ${n.number ?? ""}`.trim();
  if (n.kind === "section" && !n.number) return n.title || n.display;
  return n.number ? `${n.display} ${n.number}` : n.display;
}

export function shortTitle(s: string | null | undefined, max = 28): string {
  if (!s) return "";
  const t = s
    .replace(/\$([^$]*)\$/g, "$1")
    .replace(/\\[a-zA-Z]+\s*/g, "")
    .replace(/[{}]/g, "")
    .trim();
  return t.length > max ? t.slice(0, max - 1) + "…" : t;
}

export function locText(l: { file: string; line_start: number; line_end?: number } | null | undefined): string {
  if (!l) return "";
  return l.line_end && l.line_end !== l.line_start ? `${l.file}:${l.line_start}–${l.line_end}` : `${l.file}:${l.line_start}`;
}
