import { createContext, useContext } from "react";
import type { Index } from "../lib/graph";
import type { RenderCtx } from "../lib/latex";
import type { Loc, Report } from "../types";

export type Tab = "overview" | "guide" | "detail" | "path" | "risks" | "symbols";

export interface PaperContextValue {
  report: Report;
  index: Index;
  renderCtx: RenderCtx;
  selectNode: (id: string | null, opts?: { tab?: Tab; focus?: boolean }) => void;
  openSource: (loc: Loc) => void;
  setPathTarget: (id: string) => void;
  showRisk: (id: string) => void;
  /** Jump to anything a guide / mind-map entry can point at: a node, a section, a figure or a risk. */
  goTo: (id: string) => void;
  openFigure: (id: string) => void;
}

export const PaperContext = createContext<PaperContextValue | null>(null);

export function usePaper(): PaperContextValue {
  const v = useContext(PaperContext);
  if (!v) throw new Error("PaperContext missing");
  return v;
}
