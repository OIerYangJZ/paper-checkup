import type { Health, JobStatus, PresetEntry, Report } from "../types";

const API = (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, "") ?? "";
const DATA = `${import.meta.env.BASE_URL}data`;

async function getJson<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try {
      const j = await r.json();
      if (j?.detail) msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* not json */
    }
    throw new Error(msg);
  }
  return r.json() as Promise<T>;
}

export function loadPresets(): Promise<PresetEntry[]> {
  return getJson<PresetEntry[]>(`${DATA}/index.json`).catch(() => []);
}

export function loadPresetReport(entry: PresetEntry): Promise<Report> {
  return getJson<Report>(`${DATA}/${entry.file}`);
}

// The public API sits behind a Tailscale Funnel relay; the first TLS handshake alone can take ~2-3 s from far away,
// so be patient (the UI shows "连接中" meanwhile) and retry once before falling back to demo mode.
export async function health(timeoutMs = 12000, retries = 1): Promise<Health | null> {
  for (let attempt = 0; attempt <= retries; attempt++) {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), timeoutMs);
    try {
      const r = await fetch(`${API}/api/health`, { signal: ctl.signal });
      if (r.ok) return (await r.json()) as Health;
      if (r.status < 500) return null;
    } catch {
      /* network error or timeout: retry */
    } finally {
      clearTimeout(t);
    }
  }
  return null;
}

export async function startAnalysis(opts: {
  files?: File[];
  arxivId?: string;
  useLlm: boolean;
  reviewMax?: number;
  accessToken?: string;
}): Promise<string> {
  const fd = new FormData();
  for (const f of opts.files ?? []) fd.append("files", f, f.name);
  if (opts.arxivId) fd.append("arxiv_id", opts.arxivId);
  fd.append("use_llm", String(opts.useLlm));
  if (opts.reviewMax !== undefined) fd.append("review_max", String(opts.reviewMax));
  if (opts.accessToken) fd.append("access_token", opts.accessToken);
  const r = await fetch(`${API}/api/analyze`, { method: "POST", body: fd });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j?.detail ?? `${r.status}`);
  return j.job_id as string;
}

export function jobStatus(id: string): Promise<JobStatus> {
  return getJson<JobStatus>(`${API}/api/jobs/${id}`);
}

export function jobReport(id: string, partial = false): Promise<Report> {
  return getJson<Report>(`${API}/api/jobs/${id}/report${partial ? "?partial=true" : ""}`);
}
