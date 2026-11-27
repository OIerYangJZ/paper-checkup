import { Cpu, FileText, FolderOpen, Info, KeyRound, Link2, Stethoscope, Upload } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import {
  HttpError,
  forgetRunningJob,
  isNetworkError,
  jobReport,
  jobStatus,
  newClientId,
  rememberRunningJob,
  runningJob,
  startAnalysis,
} from "../lib/api";
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
  const [submitting, setSubmitting] = useState(false);
  const [netTrouble, setNetTrouble] = useState(false);
  const timer = useRef<number | null>(null);
  const alive = useRef(true);
  // One id per chosen input: clicking "开始体检" again after a lost response is recognised by the server as the
  // same upload, not a second paid analysis of the same paper.
  const clientId = useRef(newClientId());
  const fileInput = useRef<HTMLInputElement>(null);
  const jsonInput = useRef<HTMLInputElement>(null);
  const llmAvailable = !!health?.llm.enabled;

  useEffect(() => {
    alive.current = true;
    const saved = runningJob();   // resume a job started before the page was reloaded
    if (saved) {
      setJobId(saved);
      poll(saved);
    }
    return () => {
      alive.current = false;
      if (timer.current) window.clearTimeout(timer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const pickInput = (f: File[], a: string) => {
    setFiles(f);
    setArxiv(a);
    clientId.current = newClientId();
  };

  // Poll until the job ends. The link to the server drops requests now and then, so a failed poll is retried
  // (the job keeps running on the server) and only a long outage or a real error from the server ends it.
  const poll = (id: string) => {
    if (timer.current) window.clearTimeout(timer.current);
    let failingSince: number | null = null;
    const finish = (msg?: string) => {
      forgetRunningJob(id);
      setNetTrouble(false);
      if (msg) setError(msg);
    };
    const tick = async () => {
      if (!alive.current) return;
      try {
        const st = await jobStatus(id);
        failingSince = null;
        setNetTrouble(false);
        setJob(st);
        if (st.status === "done") {
          const rep = await jobReport(id);
          finish();
          onOpen(rep, id);
          return;
        }
        if (st.status === "error") return finish(st.error ?? "分析失败");
      } catch (e) {
        if (e instanceof HttpError && e.status === 404) {
          // The server was restarted: the job list is gone, but a finished report is still on disk.
          try {
            const rep = await jobReport(id);
            finish();
            onOpen(rep, id);
          } catch {
            setJobId(null);
            finish("服务器上找不到这个任务（分析服务可能重启过），请重新上传。");
          }
          return;
        }
        if (!isNetworkError(e)) return finish(e instanceof Error ? e.message : String(e));
        failingSince ??= Date.now();
        setNetTrouble(true);
        if (Date.now() - failingSince > 10 * 60e3) {
          setNetTrouble(false);
          setError("和分析服务的连接已中断超过 10 分钟。任务仍在服务器上运行，稍后刷新本页或打开下面的任务链接查看结果。");
          return;
        }
      }
      if (alive.current) timer.current = window.setTimeout(tick, 2000);
    };
    tick();
  };

  const submit = async () => {
    setError(null);
    setJob(null);
    setSubmitting(true);
    try {
      const id = await startAnalysis({
        files: arxiv.trim() ? [] : files,
        arxivId: arxiv.trim() || undefined,
        useLlm: useLlm && llmAvailable,
        reviewMax,
        accessToken: accessToken.trim() || undefined,
        clientId: clientId.current,
      });
      if (accessToken.trim()) localStorage.setItem("pc-access-code", accessToken.trim());
      rememberRunningJob(id);
      setJobId(id);
      poll(id);
    } catch (e) {
      // After a network failure the id is kept, so the next click is matched to the upload the server may already
      // have; after a rejection by the server it is renewed.
      if (!(e instanceof HttpError)) {
        setError("上传请求没有得到分析服务的响应（网络不稳定，已自动重试 4 次）。请稍后再点一次「开始体检」；如果服务器其实已经收到，不会重复分析。");
      } else {
        clientId.current = newClientId();
        setError(e.message);
      }
    } finally {
      setSubmitting(false);
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
            暂时连不上分析服务，实时分析不可用（预置报告不受影响）；页面每 20 秒自动重试，连上后这里会自动恢复。本地运行时请先在项目目录运行{" "}
            <code>cd backend && uv run paper-checkup serve</code>。
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
            pickInput([...e.dataTransfer.files], "");
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
            onChange={(e) => pickInput([...(e.target.files ?? [])], "")}
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
            onChange={(e) => pickInput(e.target.value ? [] : files, e.target.value)}
          />
        </div>
        <div className="upload-options">
          <label title={llmAvailable ? "" : health?.llm.reason ?? "后端未配置大模型"}>
            <input type="checkbox" checked={useLlm && llmAvailable} disabled={!llmAvailable} onChange={(e) => setUseLlm(e.target.checked)} />
            运行大模型阶段（隐式引用、符号含义、审稿对抗；没有定理的论文审查核心论断）
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
              <Cpu size={12} /> {Object.values(health!.llm.models).filter((v, i, a) => a.indexOf(v) === i).map((m) => m.split(":").pop()).join(" / ")}
            </span>
          ) : null}
          {llmAvailable && health?.llm.budget && (health.llm.budget.per_job != null || health.llm.budget.per_day != null) ? (
            <span className={`small ${health.llm.budget.day_exhausted ? "warn-text" : "muted"}`}>
              {health.llm.budget.day_exhausted
                ? `今天的大模型额度（$${health.llm.budget.per_day}）已用完，现在只能做确定性分析，明天自动恢复`
                : `花费上限：每篇 $${health.llm.budget.per_job ?? "不限"}` +
                  (health.llm.budget.per_day != null
                    ? `，今天已用 $${health.llm.budget.spent_today.toFixed(2)} / $${health.llm.budget.per_day}`
                    : "")}
            </span>
          ) : null}
        </div>
        <div className="upload-actions">
          <button
            className="btn-primary"
            disabled={backendDown || submitting || !!running || (!files.length && !arxiv.trim())}
            onClick={submit}
          >
            <Stethoscope size={15} /> {submitting ? "上传中…" : running ? "分析中…" : "开始体检"}
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
      {jobId && !job ? (
        <div className="job">
          {!error ? <div className="job-msg">{netTrouble ? "网络不稳定，正在重新连接分析服务…" : "正在获取任务进度…"}</div> : null}
          <div className="muted small">
            任务链接：<a href={`#/job/${jobId}`}>{`#/job/${jobId}`}</a>
          </div>
        </div>
      ) : null}
      {job ? (
        <div className="job">
          <div className="job-msg">{job.message}</div>
          {netTrouble && (job.status === "running" || job.status === "queued") ? (
            <div className="muted small warn-text">网络不稳定，正在重新连接…（分析在服务器上继续进行，不受影响）</div>
          ) : null}
          {jobId && job.status !== "done" ? (
            <div className="muted small">
              任务链接：<a href={`#/job/${jobId}`}>{`#/job/${jobId}`}</a>（关掉页面也没关系，分析完成后打开这个链接即可查看）
            </div>
          ) : null}
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
