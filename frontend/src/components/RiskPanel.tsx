import { BookOpen, Crosshair, Gavel, Info, ListChecks, MessagesSquare, PenLine, ScanSearch, ShieldCheck } from "lucide-react";
import { useMemo, useState } from "react";
import { CATEGORY_LABEL, ERROR_TYPE_LABEL, SEVERITY_COLOR, SEVERITY_LABEL, STATUS_LABEL } from "../lib/labels";
import type { Risk, Severity } from "../types";
import { Empty, LocLink, NodeLink, SeverityBadge, Tag } from "./common";
import { usePaper } from "./context";
import { Latex } from "./Latex";

export function RiskCard({ risk, compact = false }: { risk: Risk; compact?: boolean }) {
  const { showRisk } = usePaper();
  const [open, setOpen] = useState(false);
  return (
    <div className={`risk-card sev-${risk.severity}`} style={{ ["--sev" as string]: SEVERITY_COLOR[risk.severity] }}>
      <div className="risk-head">
        <SeverityBadge s={risk.severity} />
        <Tag tone="grey">{CATEGORY_LABEL[risk.category] ?? risk.category}</Tag>
        {risk.error_type && ERROR_TYPE_LABEL[risk.error_type] ? (
          <Tag tone="grey" title="错误类型决定严重程度上限：笔误/记号≤低，伪代码/排版≤中，退化情形/约定≤提示">
            {ERROR_TYPE_LABEL[risk.error_type]}
          </Tag>
        ) : null}
        {risk.status ? (
          <Tag tone={risk.status === "conceded" ? "red" : risk.status === "unresolved" ? "orange" : risk.status === "minor" ? "blue" : "grey"}>
            {STATUS_LABEL[risk.status] ?? risk.status}
          </Tag>
        ) : null}
        <Tag tone={risk.source === "review" ? "purple" : "grey"}>
          {risk.source === "review" ? (risk.id.startsWith("Q") ? "论断审查" : risk.category === "numeric" ? "数字核对" : "审稿对抗") : risk.category === "numeric" ? "数字核对（程序）" : "结构检查"}
        </Tag>
        {compact ? null : (
          <button className="link-btn right" onClick={() => showRisk(risk.id)} title="在图中显示相关条目">
            <Crosshair size={13} /> 在图中显示
          </button>
        )}
      </div>
      <div className="risk-title">
        <Latex src={risk.title} inline />
      </div>
      <div className="risk-nodes">
        {risk.node_ids.map((id) => (
          <NodeLink key={id} id={id} />
        ))}
        {risk.loc ? <LocLink loc={risk.loc} /> : null}
      </div>
      {risk.quote ? (
        <blockquote className="risk-quote">
          <Latex src={risk.quote} inline />
        </blockquote>
      ) : null}
      {!compact || open ? (
        <div className="risk-desc">
          <Latex src={risk.description} />
        </div>
      ) : null}
      {risk.editor_note ? (
        <div className="risk-editor">
          <Gavel size={14} />
          <span>
          <strong>主审复核：</strong>
          {risk.original_severity && risk.original_severity !== risk.severity ? (
            <span className="muted">（审稿人定级“{SEVERITY_LABEL[risk.original_severity]}” → “{SEVERITY_LABEL[risk.severity]}”）</span>
          ) : null}
          <Latex src={risk.editor_note} inline />
          </span>
        </div>
      ) : null}
      {risk.reviewer_position && !open && !compact ? (
        <div className="risk-final">
          <ScanSearch size={14} />
          <span>
            <strong>审稿人最终意见：</strong>
            <Latex src={risk.reviewer_position} inline />
          </span>
        </div>
      ) : null}
      {risk.debate && risk.debate.length ? (
        <>
          <button className="link-btn" onClick={() => setOpen(!open)}>
            <MessagesSquare size={14} />
            {open ? "收起审稿对抗记录" : `查看审稿对抗记录（${risk.debate.length} 轮）`}
          </button>
          {open ? (
            <div className="debate">
              {risk.debate.map((t, i) => (
                <div key={i} className={`turn turn-${t.role}`}>
                  <div className="turn-avatar" title={t.role === "author" ? "作者智能体" : "审稿人智能体"}>
                    {t.role === "author" ? <PenLine size={13} /> : <ScanSearch size={13} />}
                  </div>
                  <div className="turn-bubble">
                    <div className="turn-stage">{t.stage}</div>
                    <Latex src={t.content} />
                  </div>
                </div>
              ))}
            </div>
          ) : null}
        </>
      ) : compact && !open ? (
        <button className="link-btn" onClick={() => setOpen(true)}>
          展开说明
        </button>
      ) : null}
    </div>
  );
}

export function RiskPanel() {
  const { report } = usePaper();
  const [cat, setCat] = useState<string>("all");
  const [src, setSrc] = useState<string>("all");
  const [mode, setMode] = useState<"reader" | "selfcheck">("reader");
  const minSev: Severity = mode === "reader" ? "medium" : "info";
  const rank: Record<Severity, number> = { high: 3, medium: 2, low: 1, info: 0 };
  const cats = useMemo(() => {
    const m = new Map<string, number>();
    for (const r of report.risks) m.set(r.category, (m.get(r.category) ?? 0) + 1);
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  }, [report]);
  const bySev = useMemo(() => {
    const m: Record<Severity, number> = { high: 0, medium: 0, low: 0, info: 0 };
    for (const r of report.risks) m[r.severity]++;
    return m;
  }, [report]);
  const list = report.risks.filter(
    (r) => (cat === "all" || r.category === cat) && (src === "all" || r.source === src) && rank[r.severity] >= rank[minSev],
  );
  const reviewed = report.nodes.filter((n) => n.review && n.review.status !== "skipped");
  const passed = reviewed.filter((n) => n.review?.status === "passed").length;
  const structure = report.paper.mode === "structure";
  const claims = reviewed.filter((n) => n.kind === "assertion");
  const proofs = reviewed.filter((n) => n.kind !== "assertion");
  const budgetSkipped = report.nodes.filter((n) => n.review?.reason === "budget").length;

  return (
    <div className="risk-panel">
      <div className="mode-row">
        <div className="seg">
          <button className={mode === "reader" ? "on" : ""} onClick={() => setMode("reader")} title="只看影响结论或需要真正补证的问题">
            <BookOpen size={14} /> 读者模式
          </button>
          <button className={mode === "selfcheck" ? "on" : ""} onClick={() => setMode("selfcheck")} title="投稿前自查：包括笔误、记号和退化情形">
            <ListChecks size={14} /> 投稿自查
          </button>
        </div>
        <span className="muted small">{mode === "reader" ? "显示高 / 中" : "显示全部级别"}</span>
      </div>
      <div className="risk-summary">
        {(["high", "medium", "low", "info"] as Severity[]).map((s) => (
          <div key={s} className={`sev-count ${rank[s] >= rank[minSev] ? "active" : "dim"}`} style={{ ["--sev" as string]: SEVERITY_COLOR[s] }}>
            <span style={{ color: SEVERITY_COLOR[s] }}>{bySev[s]}</span> {SEVERITY_LABEL[s]}
          </div>
        ))}
      </div>
      {report.pipeline.llm_enabled ? (
        <div className="note">
          <Info size={15} />
          {structure ? (
            <span>
              这篇论文没有定理类环境，体检对象是核心论断：共审查 {claims.length} 条论断
              {proofs.length ? `和 ${proofs.length} 个证明` : ""}，其中 {passed} 项的所有质疑都被作者智能体以论文中的证据化解；
              下面只列出双方没能达成一致、作者承认、或双方认为次要的问题，并经主审复核合并重复、统一定级。
              “高”表示核心论断与论文自己的证据矛盾，“中”表示证据弱于论断（比较不对等、缺少对照等）。“数字核对”先由程序找出同一指标前后取值不同的地方，再由审稿人智能体逐条确认。
              {budgetSkipped ? `（另有 ${budgetSkipped} 项因花费上限没有审查。）` : ""}
            </span>
          ) : (
            <span>
              审稿对抗共审查 {reviewed.length} 个证明，其中 {passed} 个的所有质疑都被作者智能体以原文依据化解；
              下面只列出双方没能达成一致、作者承认、或双方认为次要的问题，并经主审复核合并重复、统一定级。
              “高”表示结论本身可能有误，“中”表示证明需要实质补证；笔误、记号和退化情形归为“低 / 提示”，在“投稿自查”模式中显示。结构检查项由程序确定性给出。
              {budgetSkipped ? `（另有 ${budgetSkipped} 个证明因花费上限没有审查。）` : ""}
            </span>
          )}
        </div>
      ) : (
        <div className="note warn">
          <Info size={15} />
          {structure
            ? "这份报告没有运行大模型阶段，只包含程序确定性检测到的问题（未定义引用、记号疑点、数字前后不一致的候选）。数字候选是按上下文启发式配对的，需要读者核实。"
            : "这份报告没有运行大模型阶段，只包含程序确定性检测到的结构问题（未定义引用、循环依赖、无证明结论、记号疑点）。"}
        </div>
      )}
      <div className="filter-row">
        <select value={cat} onChange={(e) => setCat(e.target.value)}>
          <option value="all">全部类别（{report.risks.length}）</option>
          {cats.map(([c, k]) => (
            <option key={c} value={c}>
              {CATEGORY_LABEL[c] ?? c}（{k}）
            </option>
          ))}
        </select>
        <select value={src} onChange={(e) => setSrc(e.target.value)}>
          <option value="all">全部来源</option>
          <option value="review">审稿对抗</option>
          <option value="structural">结构检查</option>
        </select>
      </div>
      {list.length ? (
        list.map((r) => <RiskCard key={r.id} risk={r} />)
      ) : (
        <Empty>
          <ShieldCheck size={30} />
          当前筛选条件下没有风险点。
        </Empty>
      )}
    </div>
  );
}
