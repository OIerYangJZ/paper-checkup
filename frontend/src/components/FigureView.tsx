import { ImageOff, Maximize2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { assetUrl } from "../lib/api";
import type { FigureInfo } from "../types";
import { LocLink } from "./common";
import { usePaper } from "./context";
import { Latex } from "./Latex";

const KIND_NAME: Record<string, string> = { figure: "图", table: "表", algorithm: "算法", listing: "代码" };

export function figureName(f: FigureInfo): string {
  return `${KIND_NAME[f.kind] ?? "图"} ${f.number ?? ""}`.trim();
}

function Lightbox({ src, alt, onClose }: { src: string; alt: string; onClose: () => void }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  return (
    <div className="modal-backdrop lightbox" onClick={onClose}>
      <img src={src} alt={alt} onClick={(e) => e.stopPropagation()} />
      <button className="lightbox-close" onClick={onClose} title="关闭（Esc）">
        <X size={18} />
      </button>
    </div>
  );
}

/** A float with its images (the paper's own figure files, or the float compiled with the paper's preamble). */
export function FigureView({ fig, compact = false }: { fig: FigureInfo; compact?: boolean }) {
  const { report } = usePaper();
  const [zoom, setZoom] = useState<string | null>(null);
  const urls = fig.images.map((f) => assetUrl(report, f)).filter((u): u is string => !!u);
  return (
    <figure className={`figure-view ${compact ? "compact" : ""}`}>
      <div className="fv-head">
        <strong>{figureName(fig)}</strong>
        {fig.source === "latex" ? <span className="muted small">由论文源码编译</span> : null}
        {fig.loc ? <LocLink loc={fig.loc} /> : null}
      </div>
      {urls.length ? (
        <div className="fv-images">
          {urls.map((u) => (
            <button key={u} className="fv-img" onClick={() => setZoom(u)} title="点击放大">
              <img src={u} alt={figureName(fig)} loading="lazy" />
              <Maximize2 size={13} className="fv-zoom" />
            </button>
          ))}
        </div>
      ) : (
        <div className="fv-missing">
          <ImageOff size={16} />
          <span>{fig.note || (report.assets ? "没有找到这张图的图片文件。" : "这份报告没有附带图片。")}</span>
        </div>
      )}
      {fig.caption ? (
        <figcaption>
          <Latex src={fig.caption} inline />
        </figcaption>
      ) : null}
      {urls.length ? (
        <div className="fv-credit">
          图片取自论文源码{report.paper.arxiv_id ? `（arXiv:${report.paper.arxiv_id}）` : ""}，版权归原作者所有
        </div>
      ) : null}
      {zoom ? <Lightbox src={zoom} alt={figureName(fig)} onClose={() => setZoom(null)} /> : null}
    </figure>
  );
}

export function FigureModal({ fig, onClose }: { fig: FigureInfo; onClose: () => void }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal figure-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <strong>{figureName(fig)}</strong>
          <button className="btn-small right" onClick={onClose}>
            关闭（Esc）
          </button>
        </div>
        <div className="figure-modal-body">
          <FigureView fig={fig} />
        </div>
      </div>
    </div>
  );
}
