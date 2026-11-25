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

export async function health(): Promise<Health | null> {
  try {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 2500);
    const r = await fetch(`${API}/api/health`, { signal: ctl.signal });
    clearTimeout(t);
    if (!r.ok) return null;
    return (await r.json()) as Health;
  } catch {
    return null;
  }
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
