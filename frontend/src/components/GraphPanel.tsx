import cytoscape, { type Core, type ElementDefinition } from "cytoscape";
import { ArrowDownUp, Eye, EyeOff, Maximize2, Route, Search, X } from "lucide-react";
import dagre from "cytoscape-dagre";
import { useEffect, useMemo, useRef, useState } from "react";
import type { PathItem } from "../lib/graph";
import { KIND_COLOR, KIND_LABEL, nodeName, shortTitle } from "../lib/labels";
import type { PaperNode } from "../types";
import { usePaper } from "./context";

cytoscape.use(dagre);

export interface GraphFilters {
  showRemarks: boolean;
  showEquations: boolean;
  showExternal: boolean;
  explicit: boolean;
  inferred: boolean;
  symbol: boolean;
  rankDir: "TB" | "LR";
  pathOnly: boolean;
}

const MINOR_KINDS = new Set(["remark", "example", "problem", "other"]);
const SEV_RANK: Record<string, number> = { high: 3, medium: 2, low: 1, info: 0 };
const SEV_CLASS = ["", "risk-low", "risk-medium", "risk-high"];

/** Mix a #rrggbb colour with white (Cytoscape does not accept #rrggbbaa). */
function tint(hex: string, amount: number): string {
  const n = parseInt(hex.slice(1), 16);
  const ch = (v: number) => Math.round(v + (255 - v) * amount).toString(16).padStart(2, "0");
  return `#${ch((n >> 16) & 255)}${ch((n >> 8) & 255)}${ch(n & 255)}`;
}

function textWidth(s: string): number {
  let w = 0;
  for (const ch of s) w += /[\u3000-\u9fff\uff00-\uffef]/.test(ch) ? 12.5 : 7.2;
  return w;
}

function nodeLabel(n: PaperNode): string {
  if (n.kind === "external") return n.external?.source === "llm" ? `◇ ${shortTitle(n.title, 22)}` : `[${shortTitle(n.title, 18)}]`;
  if (n.kind === "equation") return `(${n.number ?? "?"})`;
  const name = nodeName(n);
  const t = n.title ? shortTitle(n.title, 22) : "";
  return t ? `${name}\n${t}` : name;
}

interface Props {
  filters: GraphFilters;
  setFilters: (f: GraphFilters) => void;
  selected: string | null;
  path: PathItem[] | null;
  focusToken: number;
  focusSet: string[] | null;
}

export function GraphPanel({ filters, setFilters, selected, path, focusToken, focusSet }: Props) {
  const { report, index, selectNode } = usePaper();
  const container = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const [hover, setHover] = useState<{ x: number; y: number; id: string } | null>(null);
  const [search, setSearch] = useState("");

  // Nodes on the reading path are always shown, even if their kind is filtered out.
  const forcedKey = path ? path.map((p) => p.id).sort().join("|") : "";
  const forced = useMemo(() => new Set(forcedKey ? forcedKey.split("|") : []), [forcedKey]);

  const elements = useMemo<ElementDefinition[]>(() => {
    const visible = new Set<string>();
    const onlyForced = filters.pathOnly && forced.size > 0;
    for (const n of report.nodes) {
      if (onlyForced && !forced.has(n.id)) continue;
      if (forced.has(n.id)) {
        visible.add(n.id);
        continue;
      }
      if (MINOR_KINDS.has(n.kind) && !filters.showRemarks) continue;
      if (n.kind === "equation" && !filters.showEquations) continue;
      if (n.kind === "external" && !filters.showExternal) continue;
      visible.add(n.id);
    }
    const kinds = new Set<string>();
    if (filters.explicit) kinds.add("explicit");
    if (filters.inferred) kinds.add("inferred");
    if (filters.symbol) kinds.add("symbol");
    const edges = report.edges.filter(
      (e) => (kinds.has(e.kind) || (forced.has(e.source) && forced.has(e.target))) && visible.has(e.source) && visible.has(e.target),
    );
    // Externals only make sense when connected.
    const connected = new Set<string>();
    for (const e of edges) {
      connected.add(e.source);
      connected.add(e.target);
    }
    const els: ElementDefinition[] = [];
    for (const n of report.nodes) {
      if (!visible.has(n.id)) continue;
      if ((n.kind === "external" || n.kind === "equation") && !connected.has(n.id)) continue;
      const label = nodeLabel(n);
      const lines = label.split("\n");
      const w = Math.max(n.kind === "equation" ? 46 : 70, Math.max(...lines.map(textWidth)) + 26);
      const h = lines.length * 16 + 16;
      // Only the item a risk is *about* is outlined (every member for cycles), not the related items.
      const risks = (index.risksByNode.get(n.id) ?? []).filter((r) => r.category === "cycle" || r.node_ids[0] === n.id);
      const sev = risks.reduce((m, r) => Math.max(m, SEV_RANK[r.severity] ?? 0), 0);
      const classes = [n.kind];
      if (sev > 0) classes.push(SEV_CLASS[sev]);
      if (n.review?.status === "passed") classes.push("reviewed");
      if (n.flags.includes("no_proof") && !risks.length) classes.push("noproof");
      els.push({
        data: {
          id: n.id, label, dlabel: label, color: KIND_COLOR[n.kind] ?? "#868e96", w, h,
          tint: tint(KIND_COLOR[n.kind] ?? "#868e96", 0.92),
        },
        classes: classes.join(" "),
      });
    }
    for (const e of edges) {
      const cls: string[] = [e.kind];
      if (e.via === "cite") cls.push("cite");
      if (e.via === "contained" || e.via === "nested") cls.push("contained");
      els.push({ data: { id: e.id, source: e.source, target: e.target }, classes: cls.join(" ") });
    }
    return els;
  }, [report, index, forcedKey, filters.pathOnly, filters.showRemarks, filters.showEquations, filters.showExternal, filters.explicit, filters.inferred, filters.symbol]);

  // (Re)create the graph when elements or direction change.
  useEffect(() => {
    if (!container.current) return;
    const cy = cytoscape({
      container: container.current,
      elements,
      minZoom: 0.08,
      maxZoom: 3,
      style: [
        {
          selector: "node",
          style: {
            shape: "round-rectangle",
            width: "data(w)",
            height: "data(h)",
            "background-color": "data(tint)",
            "border-width": 1.6,
            "border-color": "data(color)",
            "border-opacity": 0.85,
            label: "data(dlabel)",
            "text-wrap": "wrap",
            "text-valign": "center",
            "text-halign": "center",
            "font-size": 12,
            "font-weight": 600,
            "font-family": "Inter, PingFang SC, Microsoft YaHei, Helvetica, Arial, sans-serif",
            color: "#1c2333",
            "line-height": 1.3,
          },
        },
        { selector: "node.theorem", style: { "border-width": 2.6, "font-weight": 800, "font-size": 12.5 } },
        { selector: "node.definition, node.notation", style: { "corner-radius": "3" } },
        { selector: "node.equation", style: { shape: "ellipse", "font-size": 10.5, color: "#475569" } },
        { selector: "node.external", style: { shape: "round-tag", "border-style": "dashed", "background-color": "#f8fafc", color: "#64748b", "font-weight": 500 } },
        { selector: "node.conjecture, node.assumption", style: { "border-style": "dashed" } },
        { selector: "node.remark, node.example, node.problem, node.other", style: { "background-color": "#fafafa", color: "#64748b", "font-weight": 500 } },
        { selector: "node.noproof", style: { "border-style": "dotted", "border-width": 2.4 } },
        { selector: "node.reviewed", style: { "border-color": "#2f8f4e", "border-width": 2.4, "border-opacity": 1 } },
        { selector: "node.risk-low", style: { "border-color": "#3a6fc4", "border-width": 3, "border-opacity": 1 } },
        { selector: "node.risk-medium", style: { "border-color": "#d9820f", "border-width": 3.2, "border-opacity": 1 } },
        { selector: "node.risk-high", style: { "border-color": "#d23f3a", "border-width": 3.6, "border-opacity": 1 } },
        {
          selector: "edge",
          style: {
            width: 1.3,
            "curve-style": "bezier",
            "target-arrow-shape": "triangle",
            "arrow-scale": 0.8,
            "line-color": "#94a3b8",
            "target-arrow-color": "#94a3b8",
            opacity: 0.9,
          },
        },
        { selector: "edge.explicit", style: { "line-color": "#64748b", "target-arrow-color": "#64748b" } },
        { selector: "edge.inferred", style: { "line-style": "dashed", "line-dash-pattern": [6, 4], "line-color": "#2f56c8", "target-arrow-color": "#2f56c8", width: 1.6 } },
        { selector: "edge.symbol", style: { "line-style": "dotted", "line-color": "#c3cad6", "target-arrow-color": "#c3cad6", width: 1.2 } },
        { selector: "edge.cite", style: { "line-style": "dashed", "line-color": "#c3cad6", "target-arrow-color": "#c3cad6" } },
        { selector: "edge.contained", style: { "line-color": "#d5dae2", "target-arrow-color": "#d5dae2", width: 1 } },
        { selector: ".faded", style: { opacity: 0.13 } },
        { selector: "node.sel", style: { "underlay-color": "#f59f00", "underlay-padding": 7, "underlay-opacity": 0.35, "underlay-shape": "round-rectangle" } },
        { selector: "node.up", style: { "underlay-color": "#2f56c8", "underlay-padding": 5, "underlay-opacity": 0.14 } },
        { selector: "node.down", style: { "underlay-color": "#6b4fd8", "underlay-padding": 5, "underlay-opacity": 0.14 } },
        { selector: "node.pathnode", style: { "underlay-color": "#2f8f4e", "underlay-padding": 6, "underlay-opacity": 0.2 } },
        { selector: "node.pathtarget", style: { "underlay-color": "#2f56c8", "underlay-padding": 7, "underlay-opacity": 0.3 } },
        { selector: "node.focus", style: { "underlay-color": "#d23f3a", "underlay-padding": 7, "underlay-opacity": 0.25 } },
        { selector: "edge.hl", style: { opacity: 1, width: 2.2 } },
        { selector: "node.pathnode, node.pathtarget, node.sel", style: { "z-index": 10 } },
      ],
      layout: {
        name: "dagre",
        rankDir: filters.rankDir,
        nodeSep: 22,
        rankSep: 62,
        edgeSep: 8,
        animate: false,
        fit: true,
        padding: 24,
      } as cytoscape.LayoutOptions,
    });
    cy.on("tap", "node", (ev) => selectNode(ev.target.id(), { tab: "detail" }));
    cy.on("mouseover", "node", (ev) => {
      const p = ev.renderedPosition ?? ev.target.renderedPosition();
      setHover({ x: p.x, y: p.y, id: ev.target.id() });
      container.current!.style.cursor = "pointer";
    });
    cy.on("mouseout", "node", () => {
      setHover(null);
      container.current!.style.cursor = "default";
    });
    cy.on("pan zoom", () => setHover(null));
    cyRef.current = cy;
    return () => {
      cy.destroy();
      cyRef.current = null;
    };
  }, [elements, filters.rankDir, selectNode]);

  // Highlighting (selection, reading path, risk focus) without re-layout.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    let fitTo: cytoscape.NodeCollection | null = null;
    cy.batch(() => {
      cy.elements().removeClass("faded sel up down pathnode pathtarget focus hl");
      cy.nodes().forEach((n) => {
        n.data("dlabel", n.data("label"));
      });
      if (path && path.length) {
        const ids = new Map(path.map((p, i) => [p.id, i + 1]));
        const target = path[path.length - 1].id;
        cy.nodes().forEach((n) => {
          const step = ids.get(n.id());
          if (step === undefined) n.addClass("faded");
          else {
            n.addClass(n.id() === target ? "pathtarget" : "pathnode");
            n.data("dlabel", `${step}. ${n.data("label")}`);
          }
        });
        cy.edges().forEach((e) => {
          if (ids.has(e.source().id()) && ids.has(e.target().id())) e.addClass("hl");
          else e.addClass("faded");
        });
        fitTo = cy.nodes().filter((n) => ids.has(n.id()));
      } else if (focusSet && focusSet.length) {
        const fs = new Set(focusSet);
        cy.nodes().forEach((n) => {
          if (fs.has(n.id())) n.addClass("focus");
          else n.addClass("faded");
        });
        cy.edges().forEach((e) => {
          if (!(fs.has(e.source().id()) && fs.has(e.target().id()))) e.addClass("faded");
        });
        fitTo = cy.nodes().filter((n) => fs.has(n.id()));
      } else if (selected && cy.getElementById(selected).nonempty()) {
        const node = cy.getElementById(selected);
        const ups = node.predecessors();
        const downs = node.outgoers();
        const keep = node.union(ups).union(downs);
        cy.elements().not(keep).addClass("faded");
        ups.nodes().addClass("up");
        downs.nodes().addClass("down");
        node.addClass("sel");
        node.connectedEdges().addClass("hl");
      }
    });
    if (fitTo && (fitTo as cytoscape.NodeCollection).nonempty()) {
      cy.animate({ fit: { eles: fitTo as cytoscape.NodeCollection, padding: 40 } }, { duration: 300 });
    }
  }, [selected, path, focusSet, elements]);

  // Center on the selected node when asked to.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || !selected || !focusToken) return;
    const n = cy.getElementById(selected);
    if (n.nonempty()) cy.animate({ center: { eles: n }, zoom: Math.max(cy.zoom(), 0.9) }, { duration: 350 });
  }, [focusToken, selected]);

  const hoverNode = hover ? index.nodes.get(hover.id) : undefined;
  const searchable = useMemo(
    () => report.nodes.filter((n) => n.kind !== "external" && n.kind !== "equation"),
    [report],
  );

  const doSearch = (q: string) => {
    const t = q.trim().toLowerCase();
    if (!t) return;
    const hit =
      searchable.find((n) => nodeName(n).toLowerCase() === t) ??
      searchable.find((n) => nodeName(n).toLowerCase().includes(t)) ??
      searchable.find((n) => (n.title ?? "").toLowerCase().includes(t)) ??
      searchable.find((n) => n.labels.some((l) => l.toLowerCase().includes(t)));
    if (hit) selectNode(hit.id, { tab: "detail", focus: true });
  };

  const tog = (k: keyof GraphFilters) => setFilters({ ...filters, [k]: !filters[k] });
  const edgeCounts = useMemo(() => {
    const c = { explicit: 0, inferred: 0, symbol: 0 } as Record<string, number>;
    for (const e of report.edges) c[e.kind] = (c[e.kind] ?? 0) + 1;
    return c;
  }, [report]);

  return (
    <div className="graph-panel">
      <div className="graph-toolbar">
        <div className="search-box">
          <Search size={14} />
          <input
            className="graph-search"
            placeholder="搜索条目，如 Lemma 3.2"
            value={search}
            list="node-options"
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && doSearch(search)}
          />
        </div>
        <datalist id="node-options">
          {searchable.map((n) => (
            <option key={n.id} value={nodeName(n)}>
              {shortTitle(n.title, 40)}
            </option>
          ))}
        </datalist>
        <div className="toolbar-group" title="依赖类型">
          <label className={`tg tg-explicit ${filters.explicit ? "on" : ""}`}>
            <input type="checkbox" checked={filters.explicit} onChange={() => tog("explicit")} />
            <span className="swatch" />确定 <span className="count">{edgeCounts.explicit}</span>
          </label>
          <label className={`tg tg-inferred ${filters.inferred ? "on" : ""}`}>
            <input type="checkbox" checked={filters.inferred} onChange={() => tog("inferred")} />
            <span className="swatch dashed" />推断 <span className="count">{edgeCounts.inferred}</span>
          </label>
          <label className={`tg tg-symbol ${filters.symbol ? "on" : ""}`}>
            <input type="checkbox" checked={filters.symbol} onChange={() => tog("symbol")} />
            <span className="swatch dotted" />记号 <span className="count">{edgeCounts.symbol}</span>
          </label>
        </div>
        <div className="toolbar-group" title="显示的条目">
          <label className={`tg ${filters.showRemarks ? "on" : ""}`}>
            <input type="checkbox" checked={filters.showRemarks} onChange={() => tog("showRemarks")} />
            {filters.showRemarks ? <Eye size={13} /> : <EyeOff size={13} />}注记
          </label>
          <label className={`tg ${filters.showEquations ? "on" : ""}`}>
            <input type="checkbox" checked={filters.showEquations} onChange={() => tog("showEquations")} />
            {filters.showEquations ? <Eye size={13} /> : <EyeOff size={13} />}公式
          </label>
          <label className={`tg ${filters.showExternal ? "on" : ""}`}>
            <input type="checkbox" checked={filters.showExternal} onChange={() => tog("showExternal")} />
            {filters.showExternal ? <Eye size={13} /> : <EyeOff size={13} />}外部
          </label>
          {path ? (
            <label className={`tg ${filters.pathOnly ? "on" : ""}`} title="只对阅读目标的最小前置子图做布局">
              <input type="checkbox" checked={filters.pathOnly} onChange={() => tog("pathOnly")} />
              <Route size={13} />子图
            </label>
          ) : null}
        </div>
        <div className="toolbar-group">
          <button className="tg on" onClick={() => setFilters({ ...filters, rankDir: filters.rankDir === "TB" ? "LR" : "TB" })} title={filters.rankDir === "TB" ? "当前纵向布局，点击切换为横向" : "当前横向布局，点击切换为纵向"}>
            <ArrowDownUp size={14} style={{ transform: filters.rankDir === "LR" ? "rotate(90deg)" : undefined }} />
          </button>
          <button className="tg on" onClick={() => cyRef.current?.fit(undefined, 28)} title="适应窗口">
            <Maximize2 size={14} />
          </button>
          {selected || path ? (
            <button className="tg on" onClick={() => selectNode(null)} title="清除高亮">
              <X size={14} />
            </button>
          ) : null}
        </div>
      </div>
      <div className="graph-canvas" ref={container} />
      {hoverNode && hover ? (
        <div className="graph-tooltip" style={{ left: hover.x + 14, top: hover.y + 50 }}>
          <div>
            <strong>{nodeName(hoverNode)}</strong> <span className="muted">{KIND_LABEL[hoverNode.kind]}</span>
          </div>
          {hoverNode.title ? <div className="muted">{shortTitle(hoverNode.title, 60)}</div> : null}
          <div className="muted small">
            依赖 {(index.preds.get(hoverNode.id) ?? []).length} · 被用 {(index.succs.get(hoverNode.id) ?? []).length}
            {(index.risksByNode.get(hoverNode.id) ?? []).length ? ` · 风险 ${(index.risksByNode.get(hoverNode.id) ?? []).length}` : ""}
          </div>
        </div>
      ) : null}
      <div className="graph-legend">
        <div className="legend-row">
          <span className="legend-line solid" /> 确定依赖
        </div>
        <div className="legend-row">
          <span className="legend-box" style={{ borderColor: "#d23f3a" }} />
          <span className="legend-box" style={{ borderColor: "#d9820f" }} />
          <span className="legend-box" style={{ borderColor: "#3a6fc4" }} /> 风险（高 / 中 / 低）
        </div>
        <div className="legend-row">
          <span className="legend-line dashed" /> 推断依赖
        </div>
        <div className="legend-row">
          <span className="legend-box" style={{ borderColor: "#2f8f4e" }} /> 审稿对抗通过
        </div>
        <div className="legend-row">
          <span className="legend-line dotted" /> 记号依赖
        </div>
        <div className="legend-row">
          <span className="legend-box" style={{ borderColor: "#94a3b8", borderStyle: "dotted" }} /> 无证明
        </div>
        <div className="legend-foot">箭头方向：前置条目 → 使用它的条目</div>
      </div>
    </div>
  );
}
