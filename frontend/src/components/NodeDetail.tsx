import { CornerDownRight, Crosshair, FileCode2, GitBranch, Info, MousePointerClick, Quote, Route, ShieldAlert } from "lucide-react";
import { useMemo } from "react";
import { EDGE_KIND_LABEL, KIND_COLOR, KIND_LABEL, PROOF_HOW_LABEL, PROOF_SOURCE_LABEL, PROVABLE, STRUCT_KINDS, VIA_LABEL, nodeName } from "../lib/labels";
import type { PaperEdge } from "../types";
import { Collapsible, Empty, KindChip, LocLink, NodeLink, Tag } from "./common";
import { usePaper } from "./context";
import { Latex } from "./Latex";
import { RiskCard } from "./RiskPanel";

function EdgeItem({ e, dir }: { e: PaperEdge; dir: "pred" | "succ" }) {
  const other = dir === "pred" ? e.source : e.target;
  const ev = e.evidence[0];
  const tone = e.kind === "explicit" ? "dark" : e.kind === "inferred" ? "blue" : "grey";
  return (
    <li className="edge-item">
      <div className="edge-head">
        <NodeLink id={other} showTitle />
        <Tag tone={tone} title={VIA_LABEL[e.via] ?? e.via}>
          {EDGE_KIND_LABEL[e.kind]}
        </Tag>
        <span className="muted small">
          {VIA_LABEL[e.via] ?? e.via} · {e.context === "statement" ? "陈述中" : "证明中"}
          {e.confidence != null && e.kind === "inferred" ? ` · 置信度 ${Math.round(e.confidence * 100)}%` : ""}
        </span>
      </div>
      {e.reason && e.kind !== "explicit" ? (
        <div className="edge-reason">
          <Latex src={e.reason} inline />
        </div>
      ) : null}
      {ev && ev.text && e.via !== "contained" && e.via !== "nested" ? (
        <div className="evidence">
          <Latex src={ev.text} inline /> {ev.loc ? <LocLink loc={ev.loc} /> : null}
        </div>
      ) : null}
    </li>
  );
}

export function NodeDetail({ id }: { id: string | null }) {
  const { index, report, setPathTarget, openSource, selectNode } = usePaper();
  const n = id ? index.nodes.get(id) : undefined;
  const preds = useMemo(() => (id ? index.preds.get(id) ?? [] : []), [id, index]);
  const succs = useMemo(() => (id ? index.succs.get(id) ?? [] : []), [id, index]);
  if (!n)
    return (
      <Empty>
        <MousePointerClick size={30} />
        在左侧依赖图中点击一个条目，
        <br />
        查看它的陈述、证明、依赖与风险点。
      </Empty>
    );
  const risks = index.risksByNode.get(n.id) ?? [];
  const section = n.section_id ? index.sectionTitle.get(n.section_id) : undefined;
  const groups: [string, PaperEdge[]][] = [
    ["确定依赖", preds.filter((e) => e.kind === "explicit" && !e.source.startsWith("ext-"))],
    ["推断依赖", preds.filter((e) => e.kind === "inferred" && !e.source.startsWith("ext-"))],
    ["记号依赖", preds.filter((e) => e.kind === "symbol")],
    ["外部知识 / 文献", preds.filter((e) => e.source.startsWith("ext-"))],
  ];
  const symbols = report.symbols.filter((s) => n.symbols_used.includes(s.id));
  const parent = n.parent ? index.nodes.get(n.parent) : undefined;
  const structural = STRUCT_KINDS.has(n.kind);
  const reviewName = n.kind === "assertion" ? "论断审查" : "审稿对抗";
  const stmtTitle =
    n.kind === "section" ? "正文（本节自身的文字，不含小节）" : n.kind === "assertion" ? "论断原文" : ["figure", "table", "pseudocode", "listing"].includes(n.kind) ? "题注" : "陈述";

  return (
    <div className="node-detail" style={{ ["--stmt-color" as string]: KIND_COLOR[n.kind] ?? "#94a3b8" }}>
      <div className="nd-hero">
      <div className="nd-header">
        <KindChip kind={n.kind} />
        <h2>
          {nodeName(n)}
          {n.title && n.kind !== "external" ? (
            <span className="nd-title">
              {" "}
              （<Latex src={n.title} inline />）
            </span>
          ) : null}
        </h2>
      </div>
      <div className="nd-meta">
        {n.loc ? <LocLink loc={n.loc} /> : null}
        {section ? (
          <span className="muted">
            <Latex src={section} inline />
          </span>
        ) : null}
        {n.labels.length ? <code className="muted small">{n.labels.join(", ")}</code> : null}
        {n.review ? (
          <Tag tone={n.review.status === "passed" ? "green" : n.review.status === "issues" ? "red" : "grey"}>
            {n.review.status === "passed"
              ? `${reviewName}：通过（${n.review.challenges ?? 0} 条质疑均已解决${n.review.minor ? `，含 ${n.review.minor} 个次要问题` : ""}）`
              : n.review.status === "issues"
                ? `${reviewName}：${n.review.unresolved} 处未解决`
                : n.review.status === "skipped"
                  ? n.review.reason === "budget"
                    ? `因花费上限未${reviewName === "论断审查" ? "审查" : "纳入审稿对抗"}`
                    : `未纳入${reviewName}`
                  : "审查失败"}
          </Tag>
        ) : null}
      </div>
      <div className="nd-actions">
        {n.kind !== "external" ? (
          <button className="btn-primary" onClick={() => setPathTarget(n.id)}>
            <Route size={15} /> 设为阅读目标
          </button>
        ) : null}
        {n.loc ? (
          <button className="btn" onClick={() => openSource(n.loc!)}>
            <FileCode2 size={15} /> 查看原文
          </button>
        ) : null}
        <button className="btn" onClick={() => selectNode(n.id, { focus: true })}>
          <Crosshair size={15} /> 在图中定位
        </button>
      </div>
      </div>

      {parent ? (
        <div className="note">
          <Info size={15} />
          <span>
            {n.kind === "assertion" ? (
              <>
                这条论断出自 <NodeLink id={parent.id} />（由大模型从全文中提取，原文已逐字核对）。
              </>
            ) : structural ? (
              <>
                属于 <NodeLink id={parent.id} />。
              </>
            ) : (
              <>
                该条目位于 <NodeLink id={parent.id} /> 的证明之中。
              </>
            )}
          </span>
        </div>
      ) : null}
      {n.flags.includes("no_proof") && PROVABLE.has(n.kind) ? (
        <div className="note warn">
          <Info size={15} />
          <span>没有找到这个{KIND_LABEL[n.kind]}的证明（无 proof 环境、无“Proof of …”章节、无文献引用）。</span>
        </div>
      ) : null}
      {n.notes.map((t, i) => (
        <div key={i} className="note">
          <Info size={15} />
          <Latex src={t} inline />
        </div>
      ))}

      {n.kind === "external" ? (
        <section className="nd-section">
          <h3>{n.external?.source === "llm" ? "外部知识（大模型识别，论文默认读者已掌握）" : "被引用的文献"}</h3>
          <div className="statement">{n.statement}</div>
          {n.external?.evidence ? (
            <div className="evidence">
              原文：<Latex src={n.external.evidence} inline />
            </div>
          ) : null}
        </section>
      ) : (
        <section className="nd-section">
          <h3>
            <Quote size={13} /> {stmtTitle}
          </h3>
          {n.kind === "section" ? (
            <div className="unit-text">
              <Latex src={n.statement || "（本节没有自身的文字）"} className="statement" />
            </div>
          ) : (
            <Latex src={n.statement || "（无题注）"} className="statement" />
          )}
        </section>
      )}

      {n.proofs.map((p, i) => (
        <Collapsible
          key={i}
          defaultOpen={p.text.length < 1800 && n.proofs.length === 1}
          title={
            <>
              证明{n.proofs.length > 1 ? ` ${i + 1}` : ""}
              <span className="muted small">
                {" "}
                · {PROOF_SOURCE_LABEL[p.source] ?? p.source}
                {p.source === "section" && p.title ? `「${p.title.split("||")[0].replace(/\\[a-z]+~?\{[^}]*\}/gi, "…").slice(0, 40)}」` : ""} · 归属依据：
                {PROOF_HOW_LABEL[p.how] ?? p.how} · 约 {Math.round(p.text.length / 100) / 10}k 字符
              </span>
            </>
          }
          right={<LocLink loc={p.loc} />}
        >
          <Latex src={p.text} className="proof" />
        </Collapsible>
      ))}

      {risks.length ? (
        <section className="nd-section">
          <h3>
            <ShieldAlert size={13} /> 风险点（{risks.length}）
          </h3>
          {risks.map((r) => (
            <RiskCard key={r.id} risk={r} compact />
          ))}
        </section>
      ) : null}

      <section className="nd-section">
        <h3>
          <GitBranch size={13} /> {n.kind === "assertion" ? "证据与依赖" : "依赖（前置条目）"}
        </h3>
        {groups.every(([, es]) => !es.length) ? <div className="muted">没有发现依赖。</div> : null}
        {groups.map(([title, es]) =>
          es.length ? (
            <div key={title} className="edge-group">
              <div className="edge-group-title">
                {title} <span className="muted">({es.length})</span>
              </div>
              <ul className="edge-list">
                {es
                  .slice()
                  .sort((a, b) => (index.nodes.get(a.source)?.order ?? 0) - (index.nodes.get(b.source)?.order ?? 0))
                  .map((e) => (
                    <EdgeItem key={e.id} e={e} dir="pred" />
                  ))}
              </ul>
            </div>
          ) : null,
        )}
      </section>

      <section className="nd-section">
        <h3>
          <CornerDownRight size={13} /> 被以下条目使用（{succs.length}）
        </h3>
        {succs.length ? (
          <ul className="chip-list">
            {succs
              .slice()
              .sort((a, b) => (index.nodes.get(a.target)?.order ?? 0) - (index.nodes.get(b.target)?.order ?? 0))
              .map((e) => (
                <li key={e.id}>
                  <NodeLink id={e.target} />
                  <span className={`mini-kind mk-${e.kind}`}>{EDGE_KIND_LABEL[e.kind]}</span>
                </li>
              ))}
          </ul>
        ) : (
          <div className="muted">没有其他条目使用它{n.kind === "lemma" ? "（孤立引理，或主要结论）" : ""}。</div>
        )}
      </section>

      {symbols.length ? (
        <Collapsible title={`用到的记号（${symbols.length}）`}>
          <ul className="sym-mini">
            {symbols.map((s) => (
              <li key={s.id}>
                {s.kind === "term" ? <em>{s.latex}</em> : <Latex src={`$${s.latex}$`} inline />}
                <span className="muted"> — {s.meaning ?? s.macro_expansion ?? "（含义待补充）"}</span>
                {s.defined_in_node && s.defined_in_node !== n.id ? (
                  <>
                    {" "}
                    · 定义于 <NodeLink id={s.defined_in_node} />
                  </>
                ) : null}
              </li>
            ))}
          </ul>
        </Collapsible>
      ) : null}

      {n.mentions.length ? (
        <Collapsible title={`正文中提到它的地方（${n.mentions.length}）`}>
          <ul className="mention-list">
            {n.mentions.map((m, i) => (
              <li key={i}>
                <Latex src={m.text} inline /> <LocLink loc={m.loc} />
              </li>
            ))}
          </ul>
        </Collapsible>
      ) : null}

      {n.lead_in ? (
        <Collapsible title="引出段落（陈述之前的一段正文）">
          <Latex src={n.lead_in} />
        </Collapsible>
      ) : null}
    </div>
  );
}
