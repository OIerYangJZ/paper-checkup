import { BookOpen, CircleHelp, GraduationCap, Lightbulb, ListTree, Network, Sparkles } from "lucide-react";
import { useState } from "react";
import { nodeName } from "../lib/labels";
import { Empty } from "./common";
import { usePaper } from "./context";
import { figureName } from "./FigureView";
import { Latex } from "./Latex";

const LEVEL: Record<string, [string, string]> = {
  basic: ["基础", "green"],
  intermediate: ["进阶", "blue"],
  advanced: ["较深", "purple"],
};

function useIdLabel() {
  const { report, index } = usePaper();
  return (id: string): string | null => {
    const n = index.nodes.get(id);
    if (n) return nodeName(n);
    const s = report.sections.find((x) => x.id === id);
    if (s) return s.number ? `§${s.number}` : s.title;
    const f = (report.figures ?? []).find((x) => x.id === id);
    if (f) return figureName(f);
    const r = report.risks.find((x) => x.id === id);
    if (r) return `风险 ${r.id}`;
    return null;
  };
}

const ID_TOKEN = /\b(?:[a-z]{1,5}-[A-Za-z0-9.]+|[RQUYS]\d+)\b/g;

/** Prose from the model sometimes cites ids inline: drop "(as-1、sec-3)" groups, name the remaining ones. */
function useReadable() {
  const label = useIdLabel();
  return (text: string): string =>
    text
      .replace(/[（(]\s*((?:[A-Za-z]{1,5}-[A-Za-z0-9.]+|[RQUYS]\d+)(?:\s*[、,，/;；和与及]\s*(?:[A-Za-z]{1,5}-[A-Za-z0-9.]+|[RQUYS]\d+))*)\s*[）)]/g,
        (m, inner: string) => (inner.split(/\s*[、,，/;；和与及]\s*/).every((t) => label(t)) ? "" : m))
      .replace(ID_TOKEN, (t) => (/^[RQUYS]\d+$/.test(t) ? t : label(t) ?? t));
}

/** Clickable chips for the ids a guide entry points at (nodes, sections, figures, risks). */
function IdChips({ ids }: { ids: string[] }) {
  const { goTo } = usePaper();
  const label = useIdLabel();
  if (!ids.length) return null;
  return (
    <span className="id-chips">
      {ids.map((id) => (
        <button key={id} className="id-chip" onClick={() => goTo(id)}>
          {label(id) ?? id}
        </button>
      ))}
    </span>
  );
}

export function GuidePanel({ onShowMindmap }: { onShowMindmap: () => void }) {
  const { report, goTo } = usePaper();
  const g = report.guide;
  const [openHints, setOpenHints] = useState<Set<number>>(new Set());
  const rd = useReadable();
  if (!g)
    return (
      <Empty>
        <GraduationCap size={30} />
        这份报告没有导读（没有运行大模型阶段）。
        <br />
        左侧可以切换到按章节结构生成的
        <button className="link-btn" onClick={onShowMindmap}>
          思维导图
        </button>
        。
      </Empty>
    );
  const secTitle = (id: string) => {
    const s = report.sections.find((x) => x.id === id);
    if (!s) return id;
    const num = s.number ? (s.appendix && s.level <= 1 ? `附录 ${s.number}` : s.number) : "";
    return `${num} ${s.title}`.trim();
  };
  return (
    <div className="guide-panel">
      <div className="note">
        <Sparkles size={15} />
        <span>
          导读由大模型{g.model ? `（${g.model.split(":").pop()}）` : ""}根据全文生成，帮助第一次读这篇论文的同学抓住主线；数字与结论以原文为准，有争议的地方见“风险点”。点击带框的标签可以跳到原文中的对应位置。
        </span>
      </div>

      <section className="nd-section">
        <h3>
          <BookOpen size={13} /> 一段话看懂这篇论文
        </h3>
        <div className="guide-summary">
          <Latex src={rd(g.summary)} />
        </div>
        {g.audience ? (
          <div className="small muted guide-audience">
            <strong>需要的基础：</strong>
            <Latex src={rd(g.audience)} inline />
          </div>
        ) : null}
      </section>

      <section className="nd-section">
        <h3>
          <Network size={13} /> 思维导图
        </h3>
        <div className="small muted">
          {g.organization ? <Latex src={rd(g.organization)} inline /> : null}
          <button className="btn-small" style={{ marginLeft: 8 }} onClick={onShowMindmap}>
            <Network size={13} /> 在左侧打开
          </button>
        </div>
      </section>

      {g.prerequisites.length ? (
        <section className="nd-section">
          <h3>
            <GraduationCap size={13} /> 先要了解的背景知识
          </h3>
          <ul className="guide-list">
            {g.prerequisites.map((x, i) => (
              <li key={i}>
                <span className={`tag tag-${LEVEL[x.level]?.[1] ?? "grey"}`}>{LEVEL[x.level]?.[0] ?? x.level}</span>{" "}
                <strong>
                  <Latex src={x.name} inline />
                </strong>
                {x.why ? (
                  <span className="muted">
                    {" "}
                    — <Latex src={rd(x.why)} inline />
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {g.sections.length ? (
        <section className="nd-section">
          <h3>
            <ListTree size={13} /> 逐节导读
          </h3>
          {g.sections.map((x) => (
            <div key={x.id} className="guide-section">
              <button className="link-btn guide-sec-title" onClick={() => goTo(x.id)}>
                <Latex src={secTitle(x.id)} inline />
              </button>
              <div>
                <Latex src={rd(x.summary)} />
              </div>
              {x.key_points.length ? (
                <ul className="guide-points">
                  {x.key_points.map((k, i) => (
                    <li key={i}>
                      <Latex src={rd(k)} inline />
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          ))}
        </section>
      ) : null}

      {g.concepts.length ? (
        <section className="nd-section">
          <h3>
            <Lightbulb size={13} /> 关键概念
          </h3>
          <dl className="guide-concepts">
            {g.concepts.map((c, i) => (
              <div key={i}>
                <dt>
                  <Latex src={c.term} inline /> <IdChips ids={c.ids} />
                </dt>
                <dd>
                  <Latex src={rd(c.explanation)} inline />
                </dd>
              </div>
            ))}
          </dl>
        </section>
      ) : null}

      {g.questions.length ? (
        <section className="nd-section">
          <h3>
            <CircleHelp size={13} /> 自测：读懂了吗？
          </h3>
          <ol className="guide-questions">
            {g.questions.map((q, i) => (
              <li key={i}>
                <Latex src={rd(q.question)} inline />
                <div>
                  <button
                    className="link-btn small"
                    onClick={() =>
                      setOpenHints((s) => {
                        const t = new Set(s);
                        if (t.has(i)) t.delete(i);
                        else t.add(i);
                        return t;
                      })
                    }
                  >
                    {openHints.has(i) ? "收起提示" : "看提示"}
                  </button>{" "}
                  <IdChips ids={q.ids} />
                </div>
                {openHints.has(i) ? (
                  <div className="guide-hint">
                    <Latex src={rd(q.hint)} inline />
                  </div>
                ) : null}
              </li>
            ))}
          </ol>
        </section>
      ) : null}
    </div>
  );
}
