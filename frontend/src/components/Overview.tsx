import { BookMarked, Check, CircleAlert, Clock, Cpu, ExternalLink, FileCheck2, GitBranch, Layers, ShieldCheck, Sigma, Sparkles, Waypoints, X } from "lucide-react";
import { useMemo, type ReactNode } from "react";
import { KIND_COLOR, KIND_LABEL } from "../lib/labels";
import { usePaper } from "./context";
import { Latex } from "./Latex";

const STAGE_MARK: Record<string, ReactNode> = {
  done: <Check size={11} />,
  skipped: "—",
  error: <X size={11} />,
  running: "…",
  pending: "·",
};
const ROLE: Record<string, string> = { extract: "提取", author: "作者", reviewer: "审稿人" };

export function Overview({ onSection }: { onSection: (ids: string[]) => void }) {
  const { report } = usePaper();
  const s = report.stats;
  const p = report.pipeline;
  const kinds = useMemo(() => {
    const m = new Map<string, number>();
    for (const n of report.nodes) if (n.kind !== "external" && n.kind !== "equation") m.set(n.kind, (m.get(n.kind) ?? 0) + 1);
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  }, [report]);
  const totalKinds = kinds.reduce((a, [, c]) => a + c, 0) || 1;
  const perSection = useMemo(() => {
    const m = new Map<string, string[]>();
    for (const n of report.nodes)
      if (n.section_id && n.kind !== "equation") {
        if (!m.has(n.section_id)) m.set(n.section_id, []);
        m.get(n.section_id)!.push(n.id);
      }
    return m;
  }, [report]);
  const sectionTree = report.sections.filter((x) => x.level <= 2);
  const desc = (sid: string): string[] => {
    const out = [...(perSection.get(sid) ?? [])];
    for (const c of report.sections) if (c.parent === sid) out.push(...desc(c.id));
    return out;
  };
  const stats: [string, number, typeof Layers, string][] = [
    ["定理类条目", s.nodes, Layers, "#2563eb"],
    ["有证明", s.proofs, FileCheck2, "#2f8f4e"],
    ["确定依赖", s.edges_explicit, GitBranch, "#475569"],
    ["推断依赖", s.edges_inferred, Waypoints, "#2f56c8"],
    ["记号 / 术语", s.symbols, Sigma, "#0f766e"],
    ["风险点", s.risks, CircleAlert, "#d23f3a"],
  ];

  return (
    <div className="overview">
      <h2 className="ov-title">
        <Latex src={report.paper.title} inline />
      </h2>
      <div className="ov-authors">{report.paper.authors.join(", ")}</div>
      <div className="ov-links">
        {report.paper.arxiv_id ? (
          <a className="chip-link" href={`https://arxiv.org/abs/${report.paper.arxiv_id}`} target="_blank" rel="noreferrer">
            arXiv:{report.paper.arxiv_id} <ExternalLink size={11} />
          </a>
        ) : null}
        <span className="chip-link">
          <BookMarked size={11} /> {report.paper.main_file}
        </span>
      </div>
      {report.paper.abstract ? (
        <details className="abstract">
          <summary>摘要</summary>
          <div>
            <Latex src={report.paper.abstract} />
          </div>
        </details>
      ) : null}

      <div className="stat-grid">
        {stats.map(([label, num, Icon, color]) => (
          <div key={label} className="stat">
            <div className="stat-icon" style={{ background: color + "14", color }}>
              <Icon size={16} />
            </div>
            <div>
              <div className="stat-num" style={label === "风险点" && num ? { color } : undefined}>
                {num}
              </div>
              <div className="stat-label">{label}</div>
            </div>
          </div>
        ))}
      </div>

      <section className="nd-section">
        <h3>
          <Layers size={13} /> 条目构成
        </h3>
        <div className="kind-stack">
          {kinds.map(([k, c]) => (
            <span key={k} title={`${KIND_LABEL[k] ?? k} ${c}`} style={{ width: `${(c / totalKinds) * 100}%`, background: KIND_COLOR[k] ?? "#999" }} />
          ))}
        </div>
        <div className="kind-legend">
          {kinds.map(([k, c]) => (
            <span key={k}>
              <i style={{ background: KIND_COLOR[k] ?? "#999" }} />
              {KIND_LABEL[k] ?? k} {c}
            </span>
          ))}
        </div>
      </section>

      <section className="nd-section">
        <h3>
          <BookMarked size={13} /> 章节结构 · 点击在图中高亮
        </h3>
        <ul className="sec-tree">
          {sectionTree.map((x) => {
            const ids = desc(x.id);
            return (
              <li key={x.id} style={{ paddingLeft: (x.level - 1) * 16 }}>
                <button className="link-btn" disabled={!ids.length} onClick={() => onSection(ids)}>
                  <span className="muted num">{x.number ? (x.appendix && x.level <= 1 ? `附录 ${x.number}` : x.number) : ""}</span>
                  <Latex src={x.title} inline />
                </button>
                {ids.length ? <span className="sec-count">{ids.length}</span> : null}
              </li>
            );
          })}
        </ul>
      </section>

      <section className="nd-section">
        <h3>
          <ShieldCheck size={13} /> 怎么读这份报告
        </h3>
        <ul className="explain">
          <li>
            <span className="legend-line" style={{ marginTop: 9 }} />
            <span>
              <strong>确定依赖</strong>：由 <code>\label</code> / <code>\ref</code> / <code>\eqref</code> / <code>\cite</code>、“Proof of …”章节和证明内嵌套结构程序化得到，可复现。
            </span>
          </li>
          <li>
            <span className="legend-line dashed" style={{ marginTop: 9 }} />
            <span>
              <strong>推断依赖</strong>：正文中没有用 <code>\ref</code> 标注的引用（“由上一引理”“by the Step Lemma”），由大模型补全，每条都附带能在原文中逐字定位的引文；正文直接写出编号的情况由程序按重建的编号匹配。
            </span>
          </li>
          <li>
            <span className="legend-line dotted" style={{ marginTop: 9 }} />
            <span>
              <strong>记号依赖</strong>：某条目用到了在某个定义环境中引入的记号或术语（程序检测）。
            </span>
          </li>
          <li>
            <CircleAlert size={15} color="#d23f3a" />
            <span>
              <strong>风险点</strong>：结构检查（程序）+ 审稿对抗（作者智能体给依据、审稿人智能体逐条质疑，只保留无法达成一致或作者承认的问题，再经主审复核定级）。本工具不判定论文对错，最终判断权在读者。
            </span>
          </li>
        </ul>
      </section>

      <section className="nd-section">
        <h3>
          <Clock size={13} /> 生成信息
        </h3>
        <div className="small muted">
          版本 {p.version} · 生成于 {p.generated_at.replace("T", " ").replace("+00:00", " UTC")}
        </div>
        {p.llm_enabled ? (
          <>
            <div className="model-chips">
              {Object.entries(p.models).map(([r, m]) => (
                <span key={r} className="tag tag-purple">
                  <Cpu size={11} /> {ROLE[r] ?? r} · {m.split(":").pop()}
                </span>
              ))}
            </div>
            <div className="small muted">
              <Sparkles size={11} style={{ verticalAlign: -1 }} /> 调用 {p.llm_calls + p.llm_cached} 次（其中 {p.llm_cached} 次复用缓存）· 输入 {p.tokens_in.toLocaleString()} / 输出{" "}
              {p.tokens_out.toLocaleString()} tokens
            </div>
          </>
        ) : (
          <div className="small muted">仅确定性分析（未运行大模型）</div>
        )}
        <ul className="stage-list">
          {p.stages.map((st) => (
            <li key={st.name} className={`stage stage-${st.status}`}>
              <span className="stage-mark">{STAGE_MARK[st.status]}</span> {st.detail ?? st.name}
              {st.seconds ? <span className="muted small"> · {st.seconds}s</span> : null}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
