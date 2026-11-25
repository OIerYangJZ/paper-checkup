import { Cpu, FileText, FolderOpen, Info, KeyRound, Link2, Stethoscope, Upload } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { jobReport, jobStatus, startAnalysis } from "../lib/api";
import type { Health, JobStatus, Report } from "../types";

const STAGE_ICON: Record<string, string> = { done: "✓", skipped: "—", error: "✗", running: "⟳", pending: "·" };

export function UploadPanel({ health, onOpen }: { health: Health | null | undefined; onOpen: (r: Report, jobId?: string) => void }) {
  const [files, setFiles] = useState<File[]>([]);
  const [arxiv, setArxiv] = useState("");
  const [useLlm, setUseLlm] = useState(true);
  const [reviewMax, setReviewMax] = useState(12);
  const [accessToken, setAccessToken] = useState(() => localStorage.getItem("pc-access-code") ?? "");
  const [job, setJob] = useState<JobStatus | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [drag, setDrag] = useState(false);
  const timer = useRef<number | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const jsonInput = useRef<HTMLInputElement>(null);
  const llmAvailable = !!health?.llm.enabled;

  useEffect(() => () => {
    if (timer.current) window.clearInterval(timer.current);
  }, []);

  const poll = (id: string) => {
    if (timer.current) window.clearInterval(timer.current);
    timer.current = window.setInterval(async () => {
      try {
        const st = await jobStatus(id);
        setJob(st);
        if (st.status === "done") {
          window.clearInterval(timer.current!);
          const rep = await jobReport(id);
          onOpen(rep, id);
        } else if (st.status === "error") {
          window.clearInterval(timer.current!);
          setError(st.error ?? "分析失败");
        }
      } catch (e) {
        window.clearInterval(timer.current!);
        setError(String(e));
      }
    }, 1500);
  };

  const submit = async () => {
    setError(null);
    setJob(null);
    try {
      const id = await startAnalysis({
        files: arxiv.trim() ? [] : files,
        arxivId: arxiv.trim() || undefined,
        useLlm: useLlm && llmAvailable,
        reviewMax,
        accessToken: accessToken.trim() || undefined,
      });
      if (accessToken.trim()) localStorage.setItem("pc-access-code", accessToken.trim());
      setJobId(id);
      poll(id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const openPartial = async () => {
    if (!jobId) return;
    try {
      onOpen(await jobReport(jobId, true), jobId);
    } catch (e) {
      setError(String(e));
    }
  };

  const openJson = async (f: File) => {
    try {
      const rep = JSON.parse(await f.text()) as Report;
      if (!rep.nodes || !rep.paper) throw new Error("不是 Paper Checkup 报告");
      onOpen(rep);
    } catch (e) {
      setError(`无法打开报告：${e instanceof Error ? e.message : e}`);
    }
  };

  const running = job && (job.status === "running" || job.status === "queued");
  const backendDown = health === null;

  return (
    <div className="upload card">
      {backendDown ? (
        <div className="note warn" style={{ gridColumn: "1 / -1", margin: 0 }}>
          <Info size={15} />
          <span>
            没有连接到后端服务，实时分析不可用（预置报告不受影响）。在项目目录运行 <code>cd backend && uv run paper-checkup serve</code> 后刷新页面。
          </span>
        </div>
      ) : null}
      <div className="upload-col">
        <div
          className={`dropzone ${drag ? "drag" : ""} ${backendDown ? "disabled" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDrag(true);
          }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDrag(false);
            setFiles([...e.dataTransfer.files]);
            setArxiv("");
          }}
          onClick={() => !backendDown && fileInput.current?.click()}
        >
          <div className="dz-icon">
            {files.length ? <FileText size={22} /> : <Upload size={22} />}
          </div>
          {files.length ? (
            <div>
              {files.map((f) => (
                <div key={f.name}>
                  <strong>{f.name}</strong> <span className="muted small">{(f.size / 1024).toFixed(0)} KB</span>
                </div>
              ))}
              <div className="muted small">点击可重新选择</div>
            </div>
          ) : (
            <div>
              <strong>拖入 LaTeX 源码，或点击选择文件</strong>
              <div className="muted small">.tex · .zip · .tar.gz · arXiv 源码包；多文件项目请打包上传</div>
            </div>
          )}
          <input
            ref={fileInput}
            type="file"
            multiple
            hidden
            accept=".tex,.zip,.gz,.tgz,.tar,.bbl,.bib,.sty,.cls"
            onChange={(e) => {
              setFiles([...(e.target.files ?? [])]);
              setArxiv("");
            }}
          />
        </div>
      </div>
      <div className="upload-col">
        <div className="input-wrap">
          <Link2 size={15} />
          <input
            className="arxiv-input"
            placeholder="或填写 arXiv 编号，如 1212.0506"
            value={arxiv}
            disabled={backendDown}
            onChange={(e) => {
              setArxiv(e.target.value);
              if (e.target.value) setFiles([]);
            }}
          />
        </div>
        <div className="upload-options">
          <label title={llmAvailable ? "" : health?.llm.reason ?? "后端未配置大模型"}>
            <input type="checkbox" checked={useLlm && llmAvailable} disabled={!llmAvailable} onChange={(e) => setUseLlm(e.target.checked)} />
            运行大模型阶段（隐式引用、符号含义、审稿对抗）
          </label>
          {!llmAvailable && health ? <span className="muted small">大模型不可用：{health.llm.reason}</span> : null}
          {useLlm && llmAvailable && health?.llm.requires_token ? (
            <label>
              <KeyRound size={14} />
              访问口令
              <input
                type="password"
                className="num-input"
                style={{ width: 160 }}
                value={accessToken}
                placeholder="运行大模型需要"
                onChange={(e) => setAccessToken(e.target.value)}
              />
            </label>
          ) : null}
          {useLlm && llmAvailable ? (
            <label>
              审稿对抗最多审查
              <input type="number" min={0} max={80} value={reviewMax} onChange={(e) => setReviewMax(Number(e.target.value))} className="num-input" />
              个证明
            </label>
          ) : null}
          {llmAvailable ? (
            <span className="muted small" style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
              <Cpu size={12} /> {Object.values(health!.llm.models).filter((v, i, a) => a.indexOf(v) === i).join(" / ")}
            </span>
          ) : null}
        </div>
        <div className="upload-actions">
          <button className="btn-primary" disabled={backendDown || !!running || (!files.length && !arxiv.trim())} onClick={submit}>
            <Stethoscope size={15} /> {running ? "分析中…" : "开始体检"}
          </button>
          <button className="btn" onClick={() => jsonInput.current?.click()}>
            <FolderOpen size={15} /> 打开报告 JSON
          </button>
          <input ref={jsonInput} type="file" hidden accept=".json" onChange={(e) => e.target.files?.[0] && openJson(e.target.files[0])} />
        </div>
      </div>
      {error ? (
        <div className="note error" style={{ gridColumn: "1 / -1", margin: 0 }}>
          <Info size={15} /> {error}
        </div>
      ) : null}
      {job ? (
        <div className="job">
          <div className="job-msg">{job.message}</div>
          <ul className="stage-list">
            {job.stages.map((s) => {
              const pr = job.progress[s.name];
              return (
                <li key={s.name} className={`stage stage-${s.status}`}>
                  <span className="stage-mark">{STAGE_ICON[s.status]}</span> {s.detail ?? s.name}
                  {pr && s.status === "running" ? <span className="muted small"> {pr[0]}/{pr[1]}</span> : null}
                  {s.seconds ? <span className="muted small"> · {s.seconds}s</span> : null}
                </li>
              );
            })}
          </ul>
          {job.has_partial && job.status !== "done" ? (
            <button className="btn-small" onClick={openPartial}>
              先查看确定性结果（大模型阶段仍在运行）
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
