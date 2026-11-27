import type { Health, JobStatus, PresetEntry, Report } from "../types";

const API = (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, "") ?? "";
const DATA = `${import.meta.env.BASE_URL}data`;

/** The server answered with an error status (as opposed to the request never getting through). */
export class HttpError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

/** True for failures of the connection itself: "Failed to fetch", a timeout, a dropped TLS handshake. */
export function isNetworkError(e: unknown): boolean {
  return !(e instanceof HttpError) && e instanceof Error;
}

const sleep = (ms: number) => new Promise((res) => setTimeout(res, ms));

/**
 * fetch with a per-attempt timeout, retrying (1 s, 2 s, 4 s, …) when the request does not get through at all.
 * The public API sits behind a Tailscale Funnel relay: from some networks a TLS handshake takes 3–20 s and roughly
 * one request in five fails outright, so a single attempt is not enough. Only use it for idempotent requests
 * (GET, or the upload with its client_id).
 */
async function fetchRetry(url: string, init: RequestInit = {}, opts: { tries?: number; timeoutMs?: number } = {}): Promise<Response> {
  const tries = opts.tries ?? 4;
  const timeoutMs = opts.timeoutMs ?? 30000;
  let last: unknown;
  for (let attempt = 0; attempt < tries; attempt++) {
    if (attempt) await sleep(Math.min(1000 * 2 ** (attempt - 1), 8000));
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), timeoutMs);
    try {
      return await fetch(url, { ...init, signal: ctl.signal });
    } catch (e) {
      last = e;
    } finally {
      clearTimeout(t);
    }
  }
  throw last instanceof Error && last.name !== "AbortError" ? last : new Error("连接超时");
}

async function getJson<T>(url: string): Promise<T> {
  const r = await fetchRetry(url);
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try {
      const j = await r.json();
      if (j?.detail) msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* not json */
    }
    throw new HttpError(r.status, msg);
  }
  return r.json() as Promise<T>;
}

/** URL of a figure image of this report (null when the report carries no image directory). */
export function assetUrl(report: Report, file: string): string | null {
  const a = report.assets;
  if (!a) return null;
  if (a.startsWith("job:")) return `${API}/api/jobs/${a.slice(4)}/figures/${encodeURIComponent(file)}`;
  return `${DATA}/${a}/${encodeURIComponent(file)}`;
}

export function loadPresets(): Promise<PresetEntry[]> {
  return getJson<PresetEntry[]>(`${DATA}/index.json`).catch(() => []);
}

export function loadPresetReport(entry: PresetEntry): Promise<Report> {
  return getJson<Report>(`${DATA}/${entry.file}`);
}

// Be patient with the Funnel relay (the UI shows "连接中" meanwhile); App keeps retrying in the background after a
// failure, so a slow first contact only delays live analysis instead of disabling it for the visit.
export async function health(): Promise<Health | null> {
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const r = await fetchRetry(`${API}/api/health`, {}, { tries: 1, timeoutMs: 20000 });
      if (r.ok) return (await r.json()) as Health;
      if (r.status < 500) return null;
    } catch {
      /* network error or timeout: retry */
    }
    await sleep(1000);
  }
  return null;
}

export function newClientId(): string {
  const c = globalThis.crypto;
  if (c?.randomUUID) return c.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

// The job being analyzed is remembered in the browser, so that reloading the page (or coming back later) resumes
// showing its progress instead of losing it.
const RUNNING_KEY = "pc-running-job";

export function rememberRunningJob(id: string) {
  localStorage.setItem(RUNNING_KEY, JSON.stringify({ id, t: Date.now() }));
}

export function forgetRunningJob(id?: string) {
  if (!id || runningJob() === id) localStorage.removeItem(RUNNING_KEY);
}

export function runningJob(): string | null {
  try {
    const v = JSON.parse(localStorage.getItem(RUNNING_KEY) ?? "null");
    if (v && typeof v.id === "string" && /^[A-Za-z0-9]+$/.test(v.id) && Date.now() - v.t < 24 * 3600e3) return v.id;
  } catch {
    /* ignore */
  }
  return null;
}

export async function startAnalysis(opts: {
  files?: File[];
  arxivId?: string;
  useLlm: boolean;
  reviewMax?: number;
  accessToken?: string;
  /** Same id for repeated clicks on the same input, so the server can tell a retry from a new upload. */
  clientId?: string;
}): Promise<string> {
  const fd = new FormData();
  for (const f of opts.files ?? []) fd.append("files", f, f.name);
  if (opts.arxivId) fd.append("arxiv_id", opts.arxivId);
  fd.append("use_llm", String(opts.useLlm));
  if (opts.reviewMax !== undefined) fd.append("review_max", String(opts.reviewMax));
  if (opts.accessToken) fd.append("access_token", opts.accessToken);
  // If the response is lost the upload is sent again; the server matches the client_id and returns the job it
  // already started instead of analyzing (and paying for) the same paper twice.
  fd.append("client_id", opts.clientId ?? newClientId());
  const size = (opts.files ?? []).reduce((s, f) => s + f.size, 0);
  const timeoutMs = Math.max(60000, (size / (50 * 1024)) * 1000);   // allow ≥ 50 KB/s for the upload itself
  const r = await fetchRetry(`${API}/api/analyze`, { method: "POST", body: fd }, { tries: 4, timeoutMs });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new HttpError(r.status, j?.detail ?? `${r.status}`);
  return j.job_id as string;
}

export function jobStatus(id: string): Promise<JobStatus> {
  return getJson<JobStatus>(`${API}/api/jobs/${id}`);
}

export function jobReport(id: string, partial = false): Promise<Report> {
  return getJson<Report>(`${API}/api/jobs/${id}/report${partial ? "?partial=true" : ""}`);
}
