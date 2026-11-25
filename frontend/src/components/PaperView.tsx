import { FileText, GitBranch, GraduationCap, LayoutDashboard, Network, Route, ShieldAlert, Sigma } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { buildIndex, readingPath, type PathMode } from "../lib/graph";
import type { RenderCtx } from "../lib/latex";
import { nodeName } from "../lib/labels";
import type { Loc, Report } from "../types";
import { PaperContext, type PaperContextValue, type Tab } from "./context";
import { FigureModal } from "./FigureView";
import { GraphPanel, type GraphFilters } from "./GraphPanel";
import { GuidePanel } from "./GuidePanel";
import { MindMap, outlineItems } from "./MindMap";
import { NodeDetail } from "./NodeDetail";
import { Overview } from "./Overview";
import { ReadingPathPanel } from "./ReadingPathPanel";
import { RiskPanel } from "./RiskPanel";
import { SourceViewer } from "./SourceViewer";
import { SymbolPanel } from "./SymbolPanel";

function pickMainTarget(report: Report, idx: ReturnType<typeof buildIndex>): string | null {
  const score = (id: string) => {
    const seen = new Set<string>();
    const stack = [id];
    while (stack.length) {
      const v = stack.pop()!;
      for (const e of idx.preds.get(v) ?? []) {
        if (!seen.has(e.source)) {
          seen.add(e.source);
          stack.push(e.source);
        }
      }
    }
    return seen.size;
  };
  for (const kind of ["theorem", "proposition", "lemma", "assertion", "section"]) {
    const c = report.nodes.filter((n) => n.kind === kind);
    if (c.length) return c.map((n) => [n.id, score(n.id)] as const).sort((a, b) => b[1] - a[1])[0][0];
  }
  return null;
}

/** Theorem-mode reports keep float labels unnumbered ("Table"); the figure list knows their numbers. */
function withFloatNumbers(report: Report): Report["labels"] {
  const out = { ...report.labels };
  const names: Record<string, string> = { figure: "Figure", table: "Table", algorithm: "Algorithm", listing: "Listing" };
  for (const f of report.figures ?? []) {
    if (!f.number) continue;
    for (const lab of f.labels) {
      const li = out[lab];
      if (li && !/\d/.test(li.display)) out[lab] = { ...li, display: `${names[f.kind] ?? li.display} ${f.number}` };
    }
  }
  return out;
}

const TABS: Tab[] = ["overview", "guide", "detail", "path", "risks", "symbols"];

export function PaperView({ report, initialNode, initialTab }: { report: Report; initialNode?: string | null; initialTab?: string }) {
  const index = useMemo(() => buildIndex(report), [report]);
  const renderCtx = useMemo<RenderCtx>(
    () => ({
      macros: report.macros,
      textMacros: report.text_macros ?? {},
      labels: withFloatNumbers(report),
      citations: report.citations,
      theoremEnvs: report.theorem_envs ?? {},
      nodeNames: Object.fromEntries(report.nodes.map((n) => [n.id, nodeName(n)])),
    }),
    [report],
  );
  const [selected, setSelected] = useState<string | null>(initialNode ?? null);
  const [tab, setTab] = useState<Tab>(
    initialTab && (TABS as string[]).includes(initialTab) ? (initialTab as Tab) : initialNode ? "detail" : "overview",
  );
  const [focusToken, setFocusToken] = useState(0);
  const [focusSet, setFocusSet] = useState<string[] | null>(null);
  const [source, setSource] = useState<Loc | null>(null);
  const [leftView, setLeftView] = useState<"graph" | "mindmap">(() =>
    new URLSearchParams(location.hash.split("?")[1] ?? "").get("view") === "mindmap" ? "mindmap" : "graph",
  );
  const [figureId, setFigureId] = useState<string | null>(null);
  const mindItems = useMemo(
    () => (report.guide?.mindmap?.length ? report.guide.mindmap : outlineItems(report)),
    [report],
  );
  const [pathTarget, setPathTargetState] = useState<string | null>(() => pickMainTarget(report, index));
  const [pathMode, setPathMode] = useState<PathMode>("understand");
  const [pathInferred, setPathInferred] = useState(true);
  const [pathSymbol, setPathSymbol] = useState(true);
  const [filters, setFilters] = useState<GraphFilters>(() => ({
    showRemarks: false,
    showEquations: true,
    showExternal: true,
    explicit: true,
    inferred: true,
    symbol: (report.stats.edges_symbol ?? 0) <= 80,
    rankDir: "TB",
    pathOnly: true,
  }));

  const path = useMemo(
    () => (pathTarget ? readingPath(index, report.edges, pathTarget, pathMode, pathInferred, pathSymbol) : null),
    [index, report, pathTarget, pathMode, pathInferred, pathSymbol],
  );

  const selectNode = useCallback((id: string | null, opts?: { tab?: Tab; focus?: boolean }) => {
    setSelected(id);
    setFocusSet(null);
    if (opts?.tab) setTab(opts.tab);
    if (opts?.focus) setFocusToken((t) => t + 1);
    if (id === null) setTab((t) => (t === "path" ? "detail" : t));
  }, []);

  useEffect(() => {
    const base = location.hash.split("?")[0];
    const qs = new URLSearchParams();
    if (selected) qs.set("node", selected);
    if (tab !== "overview") qs.set("tab", tab);
    if (leftView === "mindmap") qs.set("view", "mindmap");
    const q = qs.toString();
    history.replaceState(null, "", base + (q ? `?${q}` : ""));
  }, [selected, tab, leftView]);

  const ctx: PaperContextValue = useMemo(
    () => ({
      report,
      index,
      renderCtx,
      selectNode,
      openSource: (loc: Loc) => setSource(loc),
      setPathTarget: (id: string) => {
        setPathTargetState(id);
        setTab("path");
        setFocusSet(null);
      },
      showRisk: (rid: string) => {
        const r = report.risks.find((x) => x.id === rid);
        if (r) setFocusSet(r.node_ids);
      },
      goTo: (id: string) => {
        if (index.nodes.has(id)) {
          selectNode(id, { tab: "detail", focus: true });
          return;
        }
        const fig = (report.figures ?? []).find((f) => f.id === id);
        if (fig) {
          setFigureId(id);
          return;
        }
        const risk = report.risks.find((r) => r.id === id);
        if (risk) {
          const first = risk.node_ids.find((x) => index.nodes.has(x));
          if (first) selectNode(first, { tab: "detail", focus: true });
          else setTab("risks");
          setFocusSet(risk.node_ids);
          return;
        }
        const sec = report.sections.find((x) => x.id === id);
        if (sec?.loc) setSource(sec.loc);
      },
      openFigure: (id: string) => setFigureId(id),
    }),
    [report, index, renderCtx, selectNode],
  );

  const riskCount = report.risks.filter((r) => r.severity === "high" || r.severity === "medium").length;
  const graphPath = tab === "path" ? path : null;

  return (
    <PaperContext.Provider value={ctx}>
      <div className="paper-view">
        <div className="left-pane">
          <div className="view-switch" role="tablist">
            <button className={leftView === "graph" ? "on" : ""} onClick={() => setLeftView("graph")}>
              <GitBranch size={14} /> 依赖图
            </button>
            <button className={leftView === "mindmap" ? "on" : ""} onClick={() => setLeftView("mindmap")}>
              <Network size={14} /> 思维导图
            </button>
          </div>
          {leftView === "graph" ? (
            <GraphPanel filters={filters} setFilters={setFilters} selected={selected} path={graphPath} focusToken={focusToken} focusSet={focusSet} />
          ) : (
            <MindMap items={mindItems} generated={!!report.guide?.mindmap?.length} />
          )}
        </div>
        <aside className="side-panel">
          <nav className="tabs">
            {(
              [
                ["overview", "概览", LayoutDashboard, null],
                ["guide", "导读", GraduationCap, null],
                ["detail", "详情", FileText, null],
                ["path", "阅读路径", Route, null],
                ["risks", "风险点", ShieldAlert, riskCount],
                ["symbols", "符号表", Sigma, report.symbols.length],
              ] as [Tab, string, typeof Sigma, number | null][]
            ).map(([t, label, Icon, count]) => (
              <button key={t} className={`tab ${tab === t ? "on" : ""}`} onClick={() => setTab(t)}>
                <Icon size={15} />
                {label}
                {count ? <span className={`badge ${t === "risks" ? "alert" : ""}`}>{count}</span> : null}
              </button>
            ))}
          </nav>
          <div className="tab-body">
            {tab === "overview" ? <Overview onSection={(ids) => setFocusSet(ids)} /> : null}
            {tab === "guide" ? <GuidePanel onShowMindmap={() => setLeftView("mindmap")} /> : null}
            {tab === "detail" ? <NodeDetail id={selected} /> : null}
            {tab === "path" ? (
              <ReadingPathPanel
                target={pathTarget}
                setTarget={(id) => setPathTargetState(id)}
                mode={pathMode}
                setMode={setPathMode}
                inferred={pathInferred}
                setInferred={setPathInferred}
                symbol={pathSymbol}
                setSymbol={setPathSymbol}
                path={path}
              />
            ) : null}
            {tab === "risks" ? <RiskPanel /> : null}
            {tab === "symbols" ? <SymbolPanel /> : null}
          </div>
        </aside>
        {source ? <SourceViewer loc={source} onClose={() => setSource(null)} /> : null}
        {figureId && (report.figures ?? []).some((f) => f.id === figureId) ? (
          <FigureModal fig={(report.figures ?? []).find((f) => f.id === figureId)!} onClose={() => setFigureId(null)} />
        ) : null}
      </div>
    </PaperContext.Provider>
  );
}
