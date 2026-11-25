import { BookOpen, Check, Download, Library, Route, ScanSearch, Sigma } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { PathItem, PathMode } from "../lib/graph";
import { PROVABLE, locText, nodeName, shortTitle } from "../lib/labels";
import { Empty, KindChip, LocLink, NodeLink } from "./common";
import { usePaper } from "./context";
import { Latex } from "./Latex";

interface Props {
  target: string | null;
  setTarget: (id: string) => void;
  mode: PathMode;
  setMode: (m: PathMode) => void;
  inferred: boolean;
  setInferred: (b: boolean) => void;
  symbol: boolean;
  setSymbol: (b: boolean) => void;
  path: PathItem[] | null;
}

function useReadState(key: string) {
  const [done, setDone] = useState<Set<string>>(() => {
    try {
      return new Set(JSON.parse(localStorage.getItem(key) ?? "[]"));
    } catch {
      return new Set();
    }
  });
  useEffect(() => {
    try {
      setDone(new Set(JSON.parse(localStorage.getItem(key) ?? "[]")));
    } catch {
      setDone(new Set());
    }
  }, [key]);
  const toggle = (id: string) => {
    const next = new Set(done);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setDone(next);
    localStorage.setItem(key, JSON.stringify([...next]));
  };
  return [done, toggle] as const;
}

export function ReadingPathPanel(p: Props) {
  const { report, index } = usePaper();
  const [done, toggle] = useReadState(`pc-read:${report.paper.id}`);
  const candidates = useMemo(
    () => report.nodes.filter((n) => n.kind !== "external" && n.kind !== "equation").sort((a, b) => a.order - b.order),
    [report],
  );
  const path = p.path ?? [];
  const items = path.filter((x) => !x.id.startsWith("ext-"));
  const externals = path.filter((x) => x.id.startsWith("ext-"));
  const chars = items.reduce((a, x) => a + x.chars, 0);
  const pct = index.totalChars ? Math.round((chars / index.totalChars) * 1000) / 10 : 0;
  const nResults = candidates.length;

  // Notation used along the path but defined in running prose (not in a definition environment).
  const proseNotation = useMemo(() => {
    const inPath = new Set(items.map((x) => x.id));
    const used = new Set<string>();
    for (const it of items) for (const s of index.nodes.get(it.id)?.symbols_used ?? []) used.add(s);
    return report.symbols.filter(
      (s) =>
        used.has(s.id) &&
        s.kind !== "macro" &&
        !(s.defined_in_node && inPath.has(s.defined_in_node)) &&
        s.definitions.some((d) => d.scope === "prose"),
    );
  }, [items, index, report]);

  const exportMd = () => {
    const t = p.target ? index.nodes.get(p.target) : undefined;
    const lines = [
      `# 阅读清单：${nodeName(t)}（${p.mode === "understand" ? "看懂证明" : "完全验证"}）`,
      `论文：${report.paper.title}`,
      "",
      ...items.map((x, i) => {
        const n = index.nodes.get(x.id)!;
        return `${i + 1}. [${done.has(x.id) ? "x" : " "}] ${nodeName(n)}${n.title ? `（${shortTitle(n.title, 60)}）` : ""} — 读${x.read === "statement" ? "陈述" : "陈述+证明"} — ${locText(n.loc)}`;
      }),
      "",
      externals.length ? "## 默认已掌握的外部知识" : "",
      ...externals.map((x) => `- ${nodeName(index.nodes.get(x.id))}`),
    ];
    const blob = new Blob([lines.join("\n")], { type: "text/markdown" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `reading-path-${p.target}.md`;
    a.click();
  };

  return (
    <div className="path-panel">
      <div className="path-controls">
        <label className="field">
          <span>阅读目标</span>
          <select value={p.target ?? ""} onChange={(e) => p.setTarget(e.target.value)}>
            <option value="" disabled>
              选择一个结论…
            </option>
            {candidates.map((n) => (
              <option key={n.id} value={n.id}>
                {nodeName(n)}
                {n.title ? ` · ${shortTitle(n.title, 30)}` : ""}
                {PROVABLE.has(n.kind) && !n.proofs.length ? "（无证明）" : ""}
              </option>
            ))}
          </select>
        </label>
        <div className="controls-row">
        <div className="seg">
          <button className={p.mode === "understand" ? "on" : ""} onClick={() => p.setMode("understand")} title="读目标的证明，以及它用到的结论的陈述">
            <BookOpen size={14} /> 看懂证明
          </button>
          <button className={p.mode === "verify" ? "on" : ""} onClick={() => p.setMode("verify")} title="递归地阅读所有前置结论的证明">
            <ScanSearch size={14} /> 完全验证
          </button>
        </div>
        <div className="checks">
          <label>
            <input type="checkbox" checked={p.inferred} onChange={(e) => p.setInferred(e.target.checked)} /> 包含推断依赖
          </label>
          <label>
            <input type="checkbox" checked={p.symbol} onChange={(e) => p.setSymbol(e.target.checked)} /> 包含记号依赖
          </label>
        </div>
        </div>
      </div>
      {!p.target ? (
        <Empty>
          <Route size={30} />
          选择一个你关心的结论（或在条目详情中点“设为阅读目标”），
          <br />
          系统会计算看懂它所需的最小前置子图，并按拓扑顺序给出阅读清单。
        </Empty>
      ) : (
        <>
          <div className="path-summary">
            <div className="metric">
              <b>
                {items.length} <span className="muted" style={{ fontSize: 13, fontWeight: 500 }}>/ {nResults}</span>
              </b>
              <span>需要阅读的条目</span>
            </div>
            <div className="metric">
              <b>
                {pct}
                <span style={{ fontSize: 13 }}>%</span>
              </b>
              <span>约 {(chars / 1000).toFixed(1)}k 字符（占定理类内容）</span>
            </div>
            <button className="btn-small" style={{ height: "auto" }} onClick={exportMd} title="导出 Markdown 阅读清单">
              <Download size={14} /> 导出
            </button>
          </div>
          <div className="muted small path-explain">
            {p.mode === "understand"
              ? "“看懂证明”：完整阅读目标的陈述和证明；对它直接或间接用到的结论，只需读陈述（以及理解这些陈述所需的定义）。"
              : "“完全验证”：递归地阅读每一个前置结论的陈述和证明，直到定义和外部知识为止。"}
          </div>
          <ol className="path-list">
            {items.map((x, i) => {
              const n = index.nodes.get(x.id)!;
              const isTarget = x.id === p.target;
              return (
                <li key={x.id} className={`path-item ${done.has(x.id) ? "done" : ""} ${isTarget ? "target" : ""}`}>
                  <button className="path-step" onClick={() => toggle(x.id)} title={done.has(x.id) ? "标记为未读" : "标记为已读"}>
                    {done.has(x.id) ? <Check size={14} /> : i + 1}
                  </button>
                  <div className="path-body">
                    <div className="path-line">
                      <KindChip kind={n.kind} />
                      <NodeLink id={x.id} showTitle />
                      <span className={`read-tag ${x.read === "statement" ? "" : "full"}`}>
                        {x.read === "statement" ? "读陈述" : "读陈述 + 证明"}
                      </span>
                    </div>
                    <div className="path-meta muted small">
                      {isTarget ? (
                        "阅读目标"
                      ) : (
                        <>
                          被{" "}
                          {x.neededBy.slice(0, 4).map((nb, k) => (
                            <span key={nb}>
                              {k ? "、" : ""}
                              {nodeName(index.nodes.get(nb))}
                            </span>
                          ))}
                          {x.neededBy.length > 4 ? ` 等 ${x.neededBy.length} 项` : ""} 用到
                        </>
                      )}
                      {" · "}
                      <LocLink loc={n.loc} />
                      {n.flags.includes("no_proof") && PROVABLE.has(n.kind) && x.read !== "statement" ? <span className="warn-text"> · 无证明</span> : null}
                      {(index.risksByNode.get(n.id) ?? []).length ? (
                        <span className="warn-text"> · {(index.risksByNode.get(n.id) ?? []).length} 个风险点</span>
                      ) : null}
                    </div>
                  </div>
                </li>
              );
            })}
          </ol>
          {proseNotation.length ? (
            <section className="nd-section">
              <h3>
                <Sigma size={13} /> 需要的记号（定义在正文段落中，而非定义环境）
              </h3>
              <ul className="sym-mini">
                {proseNotation.map((s) => {
                  const d = s.definitions.find((d) => d.scope === "prose")!;
                  return (
                    <li key={s.id}>
                      {s.kind === "term" ? <em>{s.latex}</em> : <Latex src={`$${s.latex}$`} inline />}
                      <span className="muted"> — {s.meaning ?? "见原文"}</span> <LocLink loc={d.loc} />
                    </li>
                  );
                })}
              </ul>
            </section>
          ) : null}
          <section className="nd-section">
            <h3>
              <Library size={13} /> 论文默认你已掌握的外部知识（{externals.length}）
            </h3>
            {externals.length ? (
              <ul className="ext-list">
                {externals.map((x) => {
                  const n = index.nodes.get(x.id)!;
                  const ev = (index.succs.get(x.id) ?? []).find((e) => path.some((q) => q.id === e.target))?.evidence[0];
                  return (
                    <li key={x.id}>
                      <NodeLink id={x.id} />
                      <span className="muted small">
                        {" "}
                        {n.external?.source === "llm" ? "（未引用文献的标准结果，大模型识别）" : `（文献）${shortTitle(n.statement, 80)}`}
                      </span>
                      {ev?.text ? (
                        <div className="evidence">
                          <Latex src={ev.text} inline />
                        </div>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            ) : (
              <div className="muted">
                {p.mode === "understand" ? "“看懂证明”模式下，目标证明没有直接引用外部结果。" : "没有发现外部依赖。"}
                {report.pipeline.llm_enabled ? "" : "（未运行大模型阶段，未引用文献的标准结果没有被识别。）"}
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}
