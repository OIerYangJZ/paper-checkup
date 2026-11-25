import { Fragment, useMemo, useState } from "react";
import type { SymbolEntry } from "../types";
import { Empty, LocLink, NodeLink, Tag } from "./common";
import { usePaper } from "./context";
import { Latex } from "./Latex";

const FLAG_LABEL: Record<string, [string, string]> = {
  used_before_definition: ["定义前使用", "orange"],
  used_before_definition_intro: ["引言中先行使用", "grey"],
  multiple_definitions: ["多处定义", "orange"],
  conflicting_meaning: ["含义冲突", "red"],
  reviewed_issue: ["审查有疑", "red"],
  reviewed_ok: ["审查无问题", "green"],
};
const SCOPE_LABEL: Record<string, string> = {
  definition: "定义环境",
  prose: "正文",
  statement: "结论陈述",
  proof: "证明内",
  macro: "宏定义",
};

function SymbolCell({ s }: { s: SymbolEntry }) {
  if (s.kind === "term") return <em className="term">{s.latex}</em>;
  return <Latex src={`$${s.latex}$`} inline />;
}

export function SymbolPanel() {
  const { report } = usePaper();
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<"all" | "flagged" | "symbol" | "term" | "macro">("all");
  const [open, setOpen] = useState<string | null>(null);
  const list = useMemo(() => {
    const t = q.trim().toLowerCase();
    return report.symbols.filter((s) => {
      if (filter === "flagged" && !s.flags.some((f) => f !== "reviewed_ok" && f !== "used_before_definition_intro")) return false;
      if (filter === "symbol" && s.kind !== "symbol") return false;
      if (filter === "term" && s.kind !== "term") return false;
      if (filter === "macro" && s.kind !== "macro") return false;
      if (!t) return true;
      return s.latex.toLowerCase().includes(t) || (s.meaning ?? "").toLowerCase().includes(t) || (s.macro_expansion ?? "").toLowerCase().includes(t);
    });
  }, [report, q, filter]);
  if (!report.symbols.length) return <Empty>没有检测到记号定义。</Empty>;
  return (
    <div className="symbol-panel">
      <div className="filter-row">
        <input className="search" placeholder="搜索记号或含义" value={q} onChange={(e) => setQ(e.target.value)} />
        <select value={filter} onChange={(e) => setFilter(e.target.value as typeof filter)}>
          <option value="all">全部（{report.symbols.length}）</option>
          <option value="flagged">有疑点</option>
          <option value="symbol">数学记号</option>
          <option value="macro">自定义宏</option>
          <option value="term">术语</option>
        </select>
      </div>
      <div className="muted small" style={{ margin: "4px 0 8px" }}>
        位置与疑点由程序确定性检测（定义句式、:=、定义环境中的强调术语、\newcommand 宏）；
        {report.pipeline.llm_enabled ? "“含义”一列由大模型依据定义语境概括。" : "未运行大模型，“含义”列为宏展开或留空。"}
      </div>
      <table className="sym-table">
        <thead>
          <tr>
            <th>记号</th>
            <th>含义</th>
            <th>位置</th>
            <th>次数</th>
          </tr>
        </thead>
        <tbody>
          {list.map((s) => {
            const d = s.definitions.find((x) => x.scope !== "macro") ?? s.definitions[0];
            const isOpen = open === s.id;
            return (
              <Fragment key={s.id}>
                <tr className={`sym-row ${isOpen ? "open" : ""}`} onClick={() => setOpen(isOpen ? null : s.id)}>
                  <td className="sym-cell">
                    <SymbolCell s={s} />
                    <div className="sym-flags">
                      {s.flags.map((f) =>
                        FLAG_LABEL[f] ? (
                          <Tag key={f} tone={FLAG_LABEL[f][1]}>
                            {FLAG_LABEL[f][0]}
                          </Tag>
                        ) : null,
                      )}
                    </div>
                  </td>
                  <td>
                    {s.meaning ? <Latex src={s.meaning} inline /> : s.macro_expansion ? <code className="small">{s.macro_expansion}</code> : <span className="muted">—</span>}
                  </td>
                  <td className="sym-loc" onClick={(e) => e.stopPropagation()}>
                    {d ? (
                      <div>
                        <span className="muted small">定义</span> <LocLink loc={d.loc} />{" "}
                        {s.defined_in_node ? <NodeLink id={s.defined_in_node} /> : <span className="muted small">{SCOPE_LABEL[d.scope] ?? d.scope}</span>}
                      </div>
                    ) : null}
                    {s.first_use ? (
                      <div>
                        <span className="muted small">首现</span> <LocLink loc={s.first_use} />
                      </div>
                    ) : null}
                  </td>
                  <td className="num">{s.uses}</td>
                </tr>
                {isOpen ? (
                  <tr className="sym-expand">
                    <td colSpan={4}>
                      {s.note ? <div className="note">{s.note}</div> : null}
                      {s.definitions.map((x, i) => (
                        <div key={i} className="sym-def">
                          <span className="muted small">
                            {SCOPE_LABEL[x.scope] ?? x.scope} · <LocLink loc={x.loc} />
                          </span>
                          {x.scope === "macro" ? <code className="small">{x.context}</code> : <Latex src={x.context} inline />}
                        </div>
                      ))}
                      {s.first_use_context ? (
                        <div className="sym-def">
                          <span className="muted small">首次出现</span> <Latex src={s.first_use_context} inline />
                        </div>
                      ) : null}
                    </td>
                  </tr>
                ) : null}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
