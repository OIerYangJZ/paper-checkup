import { useMemo } from "react";
import { renderInline, renderLatex } from "../lib/latex";
import { usePaper } from "./context";

interface Props {
  src: string | null | undefined;
  inline?: boolean;
  className?: string;
}

/** Renders a LaTeX fragment; \ref links navigate to the referenced item. */
export function Latex({ src, inline = false, className }: Props) {
  const { renderCtx, selectNode } = usePaper();
  const html = useMemo(
    () => (src ? (inline ? renderInline(src, renderCtx) : renderLatex(src, renderCtx)) : ""),
    [src, inline, renderCtx],
  );
  const Tag = inline ? "span" : "div";
  return (
    <Tag
      className={`latex ${inline ? "latex-inline" : ""} ${className ?? ""}`}
      dangerouslySetInnerHTML={{ __html: html }}
      onClick={(e) => {
        const el = (e.target as HTMLElement).closest("[data-node]") as HTMLElement | null;
        if (el?.dataset.node) {
          e.preventDefault();
          selectNode(el.dataset.node, { tab: "detail", focus: true });
        }
      }}
    />
  );
}
