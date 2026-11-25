"""FastAPI server: live analysis of uploaded sources, plus static hosting of the frontend and presets."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__
from .llm.pricing import SpendLedger, server_budget
from .llm.provider import LLMConfig
from .pipeline import run_pipeline
from .settings import FRONTEND_DIST, PRESET_DATA_DIR, UPLOAD_DIR, load_env
from .sources import SourceError, extract_bytes, fetch_arxiv, flatten_single_subdir, normalize_arxiv_id

load_env()
log = logging.getLogger("paper_checkup.server")

MAX_UPLOAD = 60 * 1024 * 1024
MAX_CONCURRENT_JOBS = 2


@dataclass
class Job:
    id: str
    status: str = "queued"          # queued | running | done | error
    message: str = "排队中"
    stages: list = field(default_factory=list)
    progress: dict = field(default_factory=dict)
    error: str | None = None
    created: float = field(default_factory=time.time)
    report_json: str | None = None
    partial_json: str | None = None
    title: str | None = None


JOBS: dict[str, Job] = {}
_job_sem = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
_tasks: set[asyncio.Task] = set()

# Deployment switches (see README「部署」):
#   ANALYZE_LLM_TOKEN  access code required to run the paid LLM stages (empty = no code needed)
#   SERVE_STATIC=0     API-only mode: do not serve the bundled frontend / preset reports
#   CORS_ORIGINS       comma-separated allowed origins (default *)
LLM_TOKEN = os.environ.get("ANALYZE_LLM_TOKEN", "").strip()
SERVE_STATIC = os.environ.get("SERVE_STATIC", "1").strip().lower() not in ("0", "false", "no", "off")
CORS_ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()]
# Figures of uploads are converted with the paper's own image files (PDF via pdftoppm / gs / sips, EPS via gs).
# Compiling uploaded LaTeX (TikZ, tables) is off unless FIGURES_RENDER_LATEX=1: it runs untrusted TeX code, so it
# only happens inside the macOS sandbox (no reads under /Users except the upload, no network, writes to a temp dir).
RENDER_LATEX = os.environ.get("FIGURES_RENDER_LATEX", "0").strip().lower() in ("1", "true", "yes", "on")
LATEX_BUDGET_S = float(os.environ.get("FIGURES_LATEX_BUDGET_S", "120") or 120)   # total compile time per upload
# Daily LLM spend of web-started analyses (limits: LLM_BUDGET_PER_JOB_USD / LLM_BUDGET_PER_DAY_USD).
LEDGER = SpendLedger(Path(os.environ.get("LLM_SPEND_FILE") or (UPLOAD_DIR.parent / "spend.json")))

app = FastAPI(title="Paper Checkup", version=__version__)
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["GET", "POST"], allow_headers=["*"])


def _token_ok(given: str | None) -> bool:
    if not LLM_TOKEN:
        return True
    return bool(given) and hmac.compare_digest(given.strip().encode(), LLM_TOKEN.encode())


@app.get("/api/health")
def health():
    cfg = LLMConfig.from_env()
    b = server_budget(LEDGER)
    return {"ok": True, "version": __version__,
            "llm": {"enabled": cfg.enabled, "provider": cfg.provider, "models": cfg.models if cfg.enabled else {},
                    "reason": cfg.reason_disabled, "requires_token": bool(LLM_TOKEN),
                    "budget": {"per_job": b.per_job, "per_day": b.per_day,
                               "spent_today": round(LEDGER.spent_today(), 4), "day_exhausted": b.day_exhausted()}}}


@app.get("/api/papers")
def papers():
    idx = PRESET_DATA_DIR / "index.json"
    if not idx.exists():
        return []
    return json.loads(idx.read_text())


async def _run_job(job: Job, root: Path, arxiv_id: str | None, use_llm: bool, review_max: int | None,
                   arxiv_src: str | None):
    async with _job_sem:
        job.status = "running"
        try:
            if arxiv_src:
                job.message = f"正在从 arXiv 下载 {arxiv_src} 的源码…"
                root = await asyncio.to_thread(fetch_arxiv, arxiv_src, root)

            def on_progress(ev: dict):
                job.message = ev.get("message", job.message)
                job.stages = ev.get("stages", job.stages)
                job.progress = ev.get("progress", job.progress)

            def on_partial(rep):
                job.partial_json = rep.model_dump_json()
                job.title = rep.paper.title

            rep = await run_pipeline(root, paper_id=f"upload-{job.id}", arxiv_id=arxiv_id, use_llm=use_llm,
                                     review_max=review_max, progress=on_progress, partial=on_partial,
                                     budget=server_budget(LEDGER) if use_llm else None,
                                     figures_dir=UPLOAD_DIR / job.id / "figures", assets=f"job:{job.id}",
                                     render_latex=RENDER_LATEX, latex_budget_s=LATEX_BUDGET_S, sandbox=True)
            job.report_json = rep.model_dump_json()
            job.title = rep.paper.title
            out = UPLOAD_DIR / job.id / "report.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(job.report_json)
            job.status = "done"
            job.message = "完成"
            job.stages = [s.model_dump() for s in rep.pipeline.stages]
        except SourceError as e:
            job.status, job.error = "error", str(e)
        except FileNotFoundError as e:
            job.status, job.error = "error", f"没有找到可分析的 LaTeX 主文件：{e}"
        except Exception as e:  # pragma: no cover - surfaced to the UI
            log.exception("job %s failed", job.id)
            job.status, job.error = "error", f"{type(e).__name__}: {e}"


@app.post("/api/analyze")
async def analyze(files: list[UploadFile] | None = File(default=None), arxiv_id: str | None = Form(default=None),
                  use_llm: bool = Form(default=True), review_max: int | None = Form(default=None),
                  access_token: str | None = Form(default=None)):
    if use_llm and not _token_ok(access_token):
        raise HTTPException(403, "运行大模型阶段需要访问口令（口令错误或未填写）；不填口令可以只做确定性分析。")
    job = Job(id=uuid.uuid4().hex[:12])
    job_dir = UPLOAD_DIR / job.id
    root = job_dir / "src"
    root.mkdir(parents=True, exist_ok=True)
    names = [f.filename or "?" for f in files or []]

    def reject(code: int, msg: str):
        # Log why (the access log only shows the status code) and drop the half-created upload directory.
        log.warning("rejected upload %s (%s): %s", job.id, ", ".join(names) or arxiv_id or "-", msg)
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(code, msg)

    aid = None
    arxiv_src = None
    if arxiv_id and arxiv_id.strip():
        try:
            aid = normalize_arxiv_id(arxiv_id)
        except SourceError as e:
            reject(400, str(e))
        arxiv_src = aid
    elif files:
        total = 0
        for f in files:
            data = await f.read()
            total += len(data)
            if total > MAX_UPLOAD:
                reject(413, "上传文件过大（上限 60MB）")
            try:
                extract_bytes(data, f.filename or "upload.tex", root)
            except SourceError as e:
                reject(400, str(e))
        root = flatten_single_subdir(root)
        if not any(root.rglob("*.tex")):
            reject(400, "没有找到 .tex 文件（请上传 .tex 源文件，或包含源文件的 .zip / .tar.gz；不支持 PDF）")
    else:
        reject(400, "请上传文件或填写 arXiv 编号")
    if review_max is not None:
        review_max = max(0, min(review_max, 80))
    JOBS[job.id] = job
    task = asyncio.create_task(_run_job(job, root, aid, use_llm, review_max, arxiv_src))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"job_id": job.id}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    return {"id": job.id, "status": job.status, "message": job.message, "stages": job.stages,
            "progress": job.progress, "error": job.error, "title": job.title,
            "has_partial": job.partial_json is not None, "has_report": job.report_json is not None}


_FIG_NAME = __import__("re").compile(r"^[A-Za-z0-9._-]+\.(?:png|jpe?g|gif|svg)$")
_FIG_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
              ".svg": "image/svg+xml"}


@app.get("/api/jobs/{job_id}/figures/{name}")
def job_figure(job_id: str, name: str):
    if not job_id.isalnum() or not _FIG_NAME.match(name):
        raise HTTPException(404, "图片不存在")
    p = UPLOAD_DIR / job_id / "figures" / name
    if not p.is_file():
        raise HTTPException(404, "图片不存在")
    ext = p.suffix.lower()
    headers = {"Cache-Control": "public, max-age=86400"}
    if ext == ".svg":   # uploaded SVG could carry script: never render it as a document
        headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"
    return FileResponse(p, media_type=_FIG_TYPES[ext], headers=headers)


@app.get("/api/jobs/{job_id}/report")
def job_report(job_id: str, partial: bool = False):
    job = JOBS.get(job_id)
    if job is None:
        p = UPLOAD_DIR / job_id / "report.json"
        if p.exists() and job_id.isalnum():
            return Response(p.read_text(), media_type="application/json")
        raise HTTPException(404, "任务不存在")
    body = job.report_json or (job.partial_json if partial else None)
    if body is None:
        raise HTTPException(409, "报告尚未生成")
    return Response(body, media_type="application/json")


# --- static hosting -----------------------------------------------------------
if SERVE_STATIC and PRESET_DATA_DIR.exists():
    app.mount("/data", StaticFiles(directory=PRESET_DATA_DIR), name="data")

if SERVE_STATIC and FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = (FRONTEND_DIST / path).resolve()
        if path and FRONTEND_DIST.resolve() in f.parents and f.is_file():
            return FileResponse(f)
        return FileResponse(FRONTEND_DIST / "index.html")
else:
    @app.get("/", include_in_schema=False)
    def root():
        msg = "Paper Checkup API（仅 API 模式）" if not SERVE_STATIC else \
            "Paper Checkup API。前端尚未构建：cd frontend && npm install && npm run build"
        return JSONResponse({"message": msg, "health": "/api/health"})
