import type { ReactNode } from "react";
import { KIND_COLOR, KIND_LABEL, SEVERITY_COLOR, SEVERITY_LABEL, locText, nodeName } from "../lib/labels";
import type { Loc, Severity } from "../types";
import { usePaper } from "./context";
import { Latex } from "./Latex";

export function KindChip({ kind }: { kind: string }) {
  const c = KIND_COLOR[kind] ?? "#868e96";
  return (
    <span className="kind-chip" style={{ color: c, borderColor: c + "66", background: c + "14" }}>
      {KIND_LABEL[kind] ?? kind}
    </span>
  );
}

export function SeverityBadge({ s }: { s: Severity }) {
  return (
    <span className="sev-badge" style={{ background: SEVERITY_COLOR[s] }}>
      {SEVERITY_LABEL[s]}
    </span>
  );
}

export function Tag({ children, tone = "grey", title }: { children: ReactNode; tone?: string; title?: string }) {
  return (
    <span className={`tag tag-${tone}`} title={title}>
      {children}
    </span>
  );
}

export function LocLink({ loc, children }: { loc: Loc | null | undefined; children?: ReactNode }) {
  const { openSource } = usePaper();
  if (!loc) return null;
  return (
    <button className="loc-link" onClick={() => openSource(loc)} title="查看原文">
      {children ?? locText(loc)}
    </button>
  );
}

export function NodeLink({ id, showTitle = false }: { id: string; showTitle?: boolean }) {
  const { index, selectNode } = usePaper();
  const n = index.nodes.get(id);
  if (!n) return <span className="muted">{id}</span>;
  return (
    <button className="node-link" onClick={() => selectNode(id, { tab: "detail", focus: true })}>
      <span className="node-link-dot" style={{ background: KIND_COLOR[n.kind] ?? "#868e96" }} />
      {nodeName(n)}
      {showTitle && n.title && n.kind !== "external" ? (
        <span className="node-link-title">
          （<Latex src={n.title} inline />）
        </span>
      ) : null}
    </button>
  );
}

export function Collapsible({
  title,
  children,
  defaultOpen = false,
  right,
}: {
  title: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
  right?: ReactNode;
}) {
  return (
    <details className="collapsible" open={defaultOpen}>
      <summary>
        <span className="collapsible-title">{title}</span>
        {right ? <span className="collapsible-right">{right}</span> : null}
      </summary>
      <div className="collapsible-body">{children}</div>
    </details>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}
