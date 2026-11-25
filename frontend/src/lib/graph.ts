import type { PaperEdge, PaperNode, Report, Risk } from "../types";

export interface Index {
  nodes: Map<string, PaperNode>;
  preds: Map<string, PaperEdge[]>; // edges whose target is the key (prerequisites)
  succs: Map<string, PaperEdge[]>; // edges whose source is the key (dependents)
  risksByNode: Map<string, Risk[]>;
  sectionTitle: Map<string, string>;
  totalChars: number;
}

export function buildIndex(r: Report): Index {
  const nodes = new Map(r.nodes.map((n) => [n.id, n]));
  const preds = new Map<string, PaperEdge[]>();
  const succs = new Map<string, PaperEdge[]>();
  for (const e of r.edges) {
    if (!preds.has(e.target)) preds.set(e.target, []);
    preds.get(e.target)!.push(e);
    if (!succs.has(e.source)) succs.set(e.source, []);
    succs.get(e.source)!.push(e);
  }
  const risksByNode = new Map<string, Risk[]>();
  for (const k of r.risks) {
    for (const nid of k.node_ids) {
      if (!risksByNode.has(nid)) risksByNode.set(nid, []);
      risksByNode.get(nid)!.push(k);
    }
  }
  const sectionTitle = new Map(
    r.sections.map((s) => [s.id, `${s.number ? (s.appendix && s.level <= 1 ? "附录 " : "§") + s.number + " " : ""}${s.title}`]),
  );
  const totalChars = r.nodes
    .filter((n) => n.kind !== "external" && n.kind !== "equation")
    .reduce((a, n) => a + n.statement_chars + n.proof_chars, 0);
  return { nodes, preds, succs, risksByNode, sectionTitle, totalChars };
}

export type PathMode = "understand" | "verify";

export interface PathItem {
  id: string;
  read: "statement" | "statement+proof";
  neededBy: string[];
  chars: number;
}

/**
 * Minimal prerequisite set for `target` in topological order (ties broken by document order).
 * understand: target statement+proof, and the *statements* of what they use (recursively through
 *             statement-level dependencies only).
 * verify:     recursively read statements and proofs of all prerequisites.
 * Mirrors backend analysis/graph.py::reading_path.
 */
export function readingPath(
  idx: Index,
  edges: PaperEdge[],
  target: string,
  mode: PathMode,
  includeInferred: boolean,
  includeSymbol: boolean,
): PathItem[] {
  const allowed = new Set<string>(["explicit"]);
  if (includeInferred) allowed.add("inferred");
  if (includeSymbol) allowed.add("symbol");
  const need = new Map<string, "statement" | "statement+proof">([[target, "statement+proof"]]);
  const reasons = new Map<string, Set<string>>();
  const queue = [target];
  while (queue.length) {
    const v = queue.pop()!;
    const readProof = need.get(v) === "statement+proof";
    for (const e of idx.preds.get(v) ?? []) {
      if (!allowed.has(e.kind)) continue;
      if (!readProof && e.context === "proof") continue;
      const u = e.source;
      const un = idx.nodes.get(u);
      const want = mode === "verify" && un && un.proofs.length ? "statement+proof" : "statement";
      if (!reasons.has(u)) reasons.set(u, new Set());
      reasons.get(u)!.add(v);
      const cur = need.get(u);
      if (!cur || (want === "statement+proof" && cur === "statement")) {
        need.set(u, want);
        queue.push(u);
      }
    }
  }
  const sub = new Set(need.keys());
  const indeg = new Map<string, number>([...sub].map((v) => [v, 0]));
  const succ = new Map<string, string[]>();
  for (const e of edges) {
    if (!allowed.has(e.kind) || !sub.has(e.source) || !sub.has(e.target)) continue;
    indeg.set(e.target, (indeg.get(e.target) ?? 0) + 1);
    if (!succ.has(e.source)) succ.set(e.source, []);
    succ.get(e.source)!.push(e.target);
  }
  const ord = (v: string) => {
    const n = idx.nodes.get(v);
    return n && n.order >= 0 ? n.order : 1e9;
  };
  const ready = [...sub].filter((v) => indeg.get(v) === 0).sort((a, b) => ord(a) - ord(b));
  const out: string[] = [];
  while (ready.length) {
    const v = ready.shift()!;
    out.push(v);
    for (const w of succ.get(v) ?? []) {
      indeg.set(w, indeg.get(w)! - 1);
      if (indeg.get(w) === 0) {
        ready.push(w);
        ready.sort((a, b) => ord(a) - ord(b));
      }
    }
  }
  // Nodes on cycles never reach indegree 0: append them in document order.
  for (const v of [...sub].filter((v) => !out.includes(v)).sort((a, b) => ord(a) - ord(b))) out.push(v);
  return out.map((id) => {
    const n = idx.nodes.get(id);
    const read = need.get(id)!;
    const chars = n ? n.statement_chars + (read === "statement+proof" ? n.proof_chars : 0) : 0;
    return { id, read, neededBy: [...(reasons.get(id) ?? [])], chars };
  });
}

/** Ancestors (prerequisites, transitively) and descendants of a node, for highlighting. */
export function neighborhood(idx: Index, id: string, allowed: Set<string>) {
  const up = new Set<string>();
  const down = new Set<string>();
  const walk = (start: string, dir: "up" | "down", acc: Set<string>) => {
    const stack = [start];
    while (stack.length) {
      const v = stack.pop()!;
      const es = dir === "up" ? idx.preds.get(v) ?? [] : idx.succs.get(v) ?? [];
      for (const e of es) {
        if (!allowed.has(e.kind)) continue;
        const w = dir === "up" ? e.source : e.target;
        if (!acc.has(w)) {
          acc.add(w);
          stack.push(w);
        }
      }
    }
  };
  walk(id, "up", up);
  walk(id, "down", down);
  return { up, down };
}
