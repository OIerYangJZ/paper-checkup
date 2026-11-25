import { ExternalLink, FileLock2 } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";
import type { Loc } from "../types";
import { usePaper } from "./context";

export function SourceViewer({ loc, onClose }: { loc: Loc; onClose: () => void }) {
  const { report } = usePaper();
  const text = report.sources[loc.file];
  const lines = useMemo(() => (text ?? "").split("\n"), [text]);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current?.querySelector(`[data-line="${loc.line_start}"]`) as HTMLElement | null;
    el?.scrollIntoView({ block: "center" });
  }, [loc]);
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  const end = Math.min(loc.line_end || loc.line_start, loc.line_start + 400);
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal source-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <strong>原文</strong> <code>{loc.file}</code>
          <span className="muted">
            {" "}
            第 {loc.line_start}
            {end !== loc.line_start ? `–${end}` : ""} 行
          </span>
          <button className="btn-small right" onClick={onClose}>
            关闭（Esc）
          </button>
        </div>
        {text === undefined ? (
          <div className="empty">
            <FileLock2 size={30} />
            {report.paper.sources_redacted ? (
              <>
                公开版本不包含论文全文（arXiv 默认许可证只授权 arXiv 分发全文）。
                <br />
                该位置在源文件 <code>{loc.file}</code> 的第 {loc.line_start}
                {end !== loc.line_start ? `–${end}` : ""} 行。
                {report.paper.arxiv_id ? (
                  <a className="btn" href={`https://arxiv.org/abs/${report.paper.arxiv_id}`} target="_blank" rel="noreferrer">
                    <ExternalLink size={14} /> 在 arXiv 查看论文
                  </a>
                ) : null}
              </>
            ) : (
              "报告中没有附带这个文件的源码。"
            )}
          </div>
        ) : (
          <div className="source" ref={ref}>
            {lines.map((l, i) => {
              const n = i + 1;
              const hl = n >= loc.line_start && n <= end;
              return (
                <div key={n} data-line={n} className={`src-line ${hl ? "hl" : ""}`}>
                  <span className="ln">{n}</span>
                  <span className="lt">{l || " "}</span>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
