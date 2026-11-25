"""FastAPI server: live analysis of uploaded sources, plus static hosting of the frontend and presets."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__
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

app = FastAPI(title="Paper Checkup", version=__version__)
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["GET", "POST"], allow_headers=["*"])


def _token_ok(given: str | None) -> bool:
    if not LLM_TOKEN:
        return True
    return bool(given) and hmac.compare_digest(given.strip().encode(), LLM_TOKEN.encode())


@app.get("/api/health")
def health():
    cfg = LLMConfig.from_env()
    return {"ok": True, "version": __version__,
            "llm": {"enabled": cfg.enabled, "provider": cfg.provider, "models": cfg.models if cfg.enabled else {},
                    "reason": cfg.reason_disabled, "requires_token": bool(LLM_TOKEN)}}


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
                                     review_max=review_max, progress=on_progress, partial=on_partial)
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
    root = UPLOAD_DIR / job.id / "src"
    root.mkdir(parents=True, exist_ok=True)
    aid = None
    arxiv_src = None
    if arxiv_id and arxiv_id.strip():
        try:
            aid = normalize_arxiv_id(arxiv_id)
        except SourceError as e:
            raise HTTPException(400, str(e))
        arxiv_src = aid
    elif files:
        total = 0
        for f in files:
            data = await f.read()
            total += len(data)
            if total > MAX_UPLOAD:
                raise HTTPException(413, "上传文件过大（上限 60MB）")
            try:
                extract_bytes(data, f.filename or "upload.tex", root)
            except SourceError as e:
                raise HTTPException(400, str(e))
        root = flatten_single_subdir(root)
        if not any(root.rglob("*.tex")):
            raise HTTPException(400, "没有找到 .tex 文件")
    else:
        raise HTTPException(400, "请上传文件或填写 arXiv 编号")
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
