import { Maximize2, Minus, Network, Plus } from "lucide-react";
import type { IPureNode } from "markmap-common";
import { Markmap } from "markmap-view";
import { useEffect, useMemo, useRef } from "react";
import { escapeHtml, renderInline } from "../lib/latex";
import { nodeName, shortTitle } from "../lib/labels";
import type { Report } from "../types";
import { usePaper } from "./context";

export interface MindItem {
  key: string;
  parent: string | null;
  label: string;
  note?: string;
  ids: string[];
}

/** Without a guide: sections, then the main results / claims / figures inside them (all deterministic). */
export function outlineItems(report: Report): MindItem[] {
  const items: MindItem[] = [];
  const secs = report.sections.filter((s) => s.level >= 1 && s.level <= 2);
  const keep = new Set(secs.map((s) => s.id));
  const nodeIds = new Set(report.nodes.map((n) => n.id));
  for (const s of secs) {
    const num = s.number ? (s.appendix && s.level <= 1 ? `附录 ${s.number}` : s.number) : "";
    items.push({ key: s.id, parent: s.parent && keep.has(s.parent) ? s.parent : null, label: `${num} ${s.title}`.trim(), ids: nodeIds.has(s.id) ? [s.id] : [s.id] });
  }
  const important = new Set(["theorem", "proposition", "corollary", "definition", "assertion", "figure", "table", "conjecture"]);
  const perSection = new Map<string, number>();
  for (const n of [...report.nodes].sort((a, b) => a.order - b.order)) {
    if (!important.has(n.kind)) continue;
    let sec = n.section_id ?? null;
    while (sec && !keep.has(sec)) sec = report.sections.find((x) => x.id === sec)?.parent ?? null;
    if (n.kind === "assertion" && n.parent && keep.has(n.parent)) sec = n.parent;
    const c = perSection.get(sec ?? "") ?? 0;
    if (c >= 8) continue;
    perSection.set(sec ?? "", c + 1);
    const t = n.title ? `：${shortTitle(n.title, 18)}` : "";
    items.push({ key: `n:${n.id}`, parent: sec, label: `${nodeName(n)}${t}`, ids: [n.id] });
  }
  return items;
}

function buildTree(title: string, items: MindItem[], render: (s: string) => string): IPureNode {
  const root: IPureNode = { content: `<span class="mm-root">${render(title)}</span>`, children: [] };
  const byKey = new Map<string, IPureNode>();
  for (const it of items) {
    const ids = it.ids.join(",");
    const note = it.note ? ` title="${escapeHtml(it.note)}"` : "";
    byKey.set(it.key, {
      content: `<span class="mm-item${it.ids.length ? " mm-link" : ""}" data-ids="${escapeHtml(ids)}"${note}>${render(it.label)}</span>`,
      children: [],
    });
  }
  for (const it of items) {
    const node = byKey.get(it.key)!;
    const parent = it.parent ? byKey.get(it.parent) : undefined;
    (parent ?? root).children!.push(node);
  }
  return root;
}

export function MindMap({ items, generated }: { items: MindItem[]; generated: boolean }) {
  const { report, renderCtx, goTo } = usePaper();
  const svgRef = useRef<SVGSVGElement>(null);
  const mmRef = useRef<Markmap | null>(null);
  const tree = useMemo(
    () => buildTree(report.paper.title, items, (s) => (/[$\\]/.test(s) ? renderInline(s, renderCtx) : escapeHtml(s))),
    [report, items, renderCtx],
  );

  useEffect(() => {
    const mm = Markmap.create(svgRef.current!, {
      autoFit: false,
      duration: 350,
      maxWidth: 280,
      initialExpandLevel: 3,
      spacingVertical: 9,
      spacingHorizontal: 70,
      paddingX: 10,
      scrollForPan: true, // two-finger scroll pans, pinch / ctrl+wheel zooms (same as the dependency graph)
    });
    mmRef.current = mm;
    return () => {
      mm.destroy();
      mmRef.current = null;
    };
  }, []);

  useEffect(() => {
    const mm = mmRef.current;
    if (!mm) return;
    void mm.setData(tree).then(() => mm.fit());
  }, [tree]);

  const onClick = (e: React.MouseEvent) => {
    const el = (e.target as Element).closest("[data-ids]");
    const ids = (el?.getAttribute("data-ids") ?? "").split(",").filter(Boolean);
    if (ids.length) goTo(ids[0]);
  };

  return (
    <div className="mindmap-panel">
      <div className="mm-toolbar">
        <span className="mm-title">
          <Network size={14} /> 思维导图
          <span className="muted small">{generated ? " · 大模型按论文类型组织，点击条目查看详情" : " · 按章节结构生成（运行大模型阶段后会生成导读版）"}</span>
        </span>
        <span className="mm-actions">
          <button className="tg" title="放大" onClick={() => mmRef.current?.rescale(1.25)}>
            <Plus size={14} />
          </button>
          <button className="tg" title="缩小" onClick={() => mmRef.current?.rescale(0.8)}>
            <Minus size={14} />
          </button>
          <button className="tg" title="适应窗口" onClick={() => mmRef.current?.fit()}>
            <Maximize2 size={14} />
          </button>
        </span>
      </div>
      <svg ref={svgRef} className="mindmap-svg" onClick={onClick} />
      <div className="mm-hint">拖动或双指滑动平移 · 捏合或 Ctrl+滚轮缩放 · 点击圆点折叠 / 展开</div>
    </div>
  );
}
