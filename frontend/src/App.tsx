import { useCallback, useEffect, useMemo, useState } from "react";
import { Cpu, Download, ExternalLink } from "lucide-react";
import { Home } from "./components/Home";
import { Logo } from "./components/Logo";
import { PaperView } from "./components/PaperView";
import { health as fetchHealth, jobReport, loadPresetReport, loadPresets } from "./lib/api";
import { renderInline } from "./lib/latex";
import type { Health, PresetEntry, Report } from "./types";

type Route =
  | { page: "home" }
  | { page: "paper"; id: string; node?: string; tab?: string }
  | { page: "job"; id: string; node?: string; tab?: string }
  | { page: "local" };

function parseHash(): Route {
  const h = location.hash.replace(/^#\/?/, "");
  const [path, query] = h.split("?");
  const qs = new URLSearchParams(query ?? "");
  const node = qs.get("node") ?? undefined;
  const tab = qs.get("tab") ?? undefined;
  const parts = path.split("/").filter(Boolean);
  if (parts[0] === "paper" && parts[1]) return { page: "paper", id: decodeURIComponent(parts[1]), node, tab };
  if (parts[0] === "job" && parts[1]) return { page: "job", id: parts[1], node, tab };
  if (parts[0] === "local") return { page: "local" };
  return { page: "home" };
}

export default function App() {
  const [route, setRoute] = useState<Route>(parseHash);
  const [presets, setPresets] = useState<PresetEntry[]>([]);
  const [health, setHealth] = useState<Health | null | undefined>(undefined);
  const [report, setReport] = useState<Report | null>(null);
  const [reportKey, setReportKey] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const on = () => setRoute(parseHash());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  useEffect(() => {
    loadPresets().then(setPresets);
    fetchHealth().then(setHealth);
  }, []);

  // Load the report for the current route.
  useEffect(() => {
    setError(null);
    if (route.page === "paper") {
      const key = `paper:${route.id}`;
      if (key === reportKey && report) return;
      const entry = presets.find((p) => p.id === route.id);
      if (!entry) {
        if (presets.length) setError(`没有找到预置论文 ${route.id}`);
        return;
      }
      setLoading(true);
      loadPresetReport(entry)
        .then((r) => {
          setReport(r);
          setReportKey(key);
        })
        .catch((e) => setError(String(e)))
        .finally(() => setLoading(false));
    } else if (route.page === "job") {
      const key = `job:${route.id}`;
      if (key === reportKey && report) return;
      setLoading(true);
      jobReport(route.id)
        .then((r) => {
          setReport(r);
          setReportKey(key);
        })
        .catch((e) => setError(`无法加载任务报告：${e}`))
        .finally(() => setLoading(false));
    } else if (route.page === "local" && !report) {
      location.hash = "#/";
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route, presets]);

  const openReport = useCallback((r: Report, jobId?: string) => {
    setReport(r);
    const key = jobId ? `job:${jobId}` : `local:${Date.now()}`;
    setReportKey(key);
    location.hash = jobId ? `#/job/${jobId}` : "#/local";
  }, []);

  const inPaper = route.page !== "home" && report;
  const titleHtml = useMemo(
    () => (report ? renderInline(report.paper.title, { macros: report.macros, textMacros: {}, labels: {}, citations: {}, theoremEnvs: {} }) : ""),
    [report],
  );

  const download = () => {
    if (!report) return;
    const blob = new Blob([JSON.stringify(report)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${report.paper.id}.checkup.json`;
    a.click();
  };

  return (
    <div className={`app ${inPaper ? "app-paper" : ""}`}>
      <header className="topbar">
        <a className="brand" href="#/">
          <Logo />
          <span className="brand-text">
            <span className="brand-name">论文审稿体检</span>
            <span className="brand-en">Paper Checkup</span>
          </span>
        </a>
        {inPaper ? (
          <div className="topbar-paper">
            <span className="brand-divider" />
            <span className="topbar-title" dangerouslySetInnerHTML={{ __html: titleHtml }} />
            {report.paper.arxiv_id ? (
              <a className="chip-link" href={`https://arxiv.org/abs/${report.paper.arxiv_id}`} target="_blank" rel="noreferrer">
                arXiv:{report.paper.arxiv_id} <ExternalLink size={11} />
              </a>
            ) : null}
            {report.pipeline.llm_enabled ? (
              <span className="chip-link" title="生成这份报告所用的模型">
                <Cpu size={11} /> {[...new Set(Object.values(report.pipeline.models).map((m) => m.split(":").pop()))].join(" + ")}
              </span>
            ) : (
              <span className="pill pill-grey" title="这份报告只包含确定性分析">
                未运行大模型
              </span>
            )}
          </div>
        ) : (
          <div className="topbar-paper" />
        )}
        <div className="topbar-right">
          {inPaper ? (
            <>
              {presets.length > 1 && route.page === "paper" ? (
                <select className="paper-switch" value={route.id} onChange={(e) => (location.hash = `#/paper/${e.target.value}`)}>
                  {presets.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.id.includes("--") ? `　↳ 对照：${p.id.split("--")[1]}` : p.title.replace(/\$/g, "").slice(0, 46)}
                    </option>
                  ))}
                </select>
              ) : null}
              <button className="btn-small" onClick={download} title="下载报告 JSON">
                <Download size={14} /> 导出
              </button>
            </>
          ) : null}
          <span
            className={`pill ${health ? (health.llm.enabled ? "pill-green" : "pill-yellow") : health === null ? "pill-grey" : ""}`}
            title={health ? (health.llm.enabled ? `大模型：${Object.values(health.llm.models).join(", ")}` : `大模型未启用：${health.llm.reason}`) : "后端未连接，仅可查看预置报告"}
          >
            <span className="dot" />
            {health === undefined ? "连接中" : health ? (health.llm.enabled ? "实时分析可用" : "仅确定性分析") : "演示模式"}
          </span>
        </div>
      </header>
      <main className="main">
        {error ? <div className="note error center-note">{error}</div> : null}
        {loading ? <div className="loading">正在载入体检报告…</div> : null}
        {route.page === "home" ? (
          <Home presets={presets} health={health} onPreset={(id) => (location.hash = `#/paper/${id}`)} onOpen={openReport} />
        ) : report && !loading ? (
          <PaperView
            key={reportKey}
            report={report}
            initialNode={"node" in route ? route.node : undefined}
            initialTab={"tab" in route ? route.tab : undefined}
          />
        ) : null}
      </main>
    </div>
  );
}
