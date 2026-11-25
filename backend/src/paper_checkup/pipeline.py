"""End-to-end pipeline: deterministic analysis, then the optional LLM stages."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import __version__
from .analysis.checks import Checker
from .deterministic import AnalysisContext, analyze_structure, assemble_report
from .llm.provider import LLMClient, LLMConfig, LLMError
from .llm import stages
from .models import PipelineInfo, Report, StageInfo

log = logging.getLogger("paper_checkup.pipeline")

STAGES = [
    ("parse", "解析 LaTeX 与依赖图"),
    ("proof_owner", "证明归属判断"),
    ("implicit", "隐式引用补全"),
    ("checks", "结构检查"),
    ("symbols", "符号含义"),
    ("symbol_debate", "符号争议审查"),
    ("unsupported", "无来源结论审查"),
    ("review", "审稿对抗"),
    ("external_merge", "外部知识合并"),
    ("meta_review", "主审复核"),
]

ProgressCb = Callable[[dict], None]


class Tracker:
    def __init__(self, cb: ProgressCb | None, llm: bool):
        self.cb = cb
        self.stages = {name: StageInfo(name=name, status="pending", detail=label) for name, label in STAGES}
        if not llm:
            for n in list(self.stages)[1:]:
                if n != "checks":
                    self.stages[n].status = "skipped"
        self._t: dict[str, float] = {}
        self.message = ""
        self.progress: dict[str, tuple[int, int]] = {}

    def start(self, name):
        self.stages[name].status = "running"
        self._t[name] = time.time()
        self.emit(f"{self.stages[name].detail}…")

    def done(self, name, detail: str | None = None, status="done"):
        st = self.stages[name]
        st.status = status
        st.seconds = round(time.time() - self._t.get(name, time.time()), 2)
        if detail:
            st.detail = f"{st.detail}：{detail}"
        self.emit(f"{st.detail}")

    def step(self, name, done, total, msg):
        self.progress[name] = (done, total)
        self.emit(msg)

    def emit(self, message: str):
        self.message = message
        if self.cb:
            self.cb({"message": message, "stages": [s.model_dump() for s in self.stages.values()],
                     "progress": {k: list(v) for k, v in self.progress.items()}})

    def list(self) -> list[StageInfo]:
        return list(self.stages.values())


def _pipeline_info(cfg: LLMConfig | None, client: LLMClient | None, tracker: Tracker,
                   llm_on: bool | None = None) -> PipelineInfo:
    # llm_enabled describes this report (did the LLM stages run on it), not whether the server has an LLM configured.
    on = bool(cfg and cfg.enabled) if llm_on is None else llm_on
    return PipelineInfo(
        version=__version__,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        llm_enabled=on,
        models=dict(cfg.models) if cfg and on else {},
        stages=tracker.list(),
        llm_calls=client.stats.calls if client else 0,
        llm_cached=client.stats.cached if client else 0,
        tokens_in=client.stats.tokens_in if client else 0,
        tokens_out=client.stats.tokens_out if client else 0,
        tokens_cached=client.stats.cache_read_tokens if client else 0,
        tokens_reasoning=client.stats.reasoning_tokens if client else 0,
        language=cfg.language if cfg else "zh",
    )


def rerun_checks(ctx: AnalysisContext):
    keep = [r for r in ctx.risks if r.source != "structural"]
    checker = Checker(ctx.paper, ctx.gb, ctx.nodes, ctx.edges, ctx.symbols)
    structural = checker.run()
    ctx.checker = checker
    ctx.risks[:] = structural + keep


async def run_pipeline(root: Path, *, paper_id: str | None = None, arxiv_id: str | None = None,
                       use_llm: bool = True, review_max: int | None = None, config: LLMConfig | None = None,
                       progress: ProgressCb | None = None, partial: Callable[[Report], None] | None = None,
                       use_cache: bool = True) -> Report:
    cfg = config or LLMConfig.from_env()
    llm_on = use_llm and cfg.enabled
    tracker = Tracker(progress, llm_on)
    if use_llm and not cfg.enabled:
        tracker.emit(f"大模型未启用（{cfg.reason_disabled}），只运行确定性分析")

    tracker.start("parse")
    ctx = analyze_structure(Path(root), paper_id=paper_id, arxiv_id=arxiv_id)
    tracker.done("parse", f"{sum(1 for n in ctx.nodes.values() if n.kind not in ('equation', 'external'))} 个条目，{len(ctx.edges)} 条依赖")
    tracker.stages["checks"].status = "done"
    if partial:
        partial(assemble_report(ctx, _pipeline_info(cfg, None, tracker, llm_on)))
    if not llm_on:
        return assemble_report(ctx, _pipeline_info(cfg, None, tracker, llm_on))

    client = LLMClient(cfg, use_cache=use_cache)
    review_max = review_max if review_max is not None else int(__import__("os").environ.get("REVIEW_MAX_TARGETS", "30"))

    async def guarded(name, coro_fn, fmt=lambda r: ""):
        tracker.start(name)
        try:
            res = await coro_fn()
            tracker.done(name, fmt(res))
            return res
        except LLMError as e:
            log.warning("阶段 %s 失败：%s", name, e)
            tracker.done(name, str(e)[:160], status="error")
        except Exception as e:  # keep the deterministic results even if a stage crashes
            log.exception("阶段 %s 异常", name)
            tracker.done(name, f"{type(e).__name__}: {e}"[:160], status="error")
        return None

    def step(name):
        return lambda stage, done, total, msg: tracker.step(name, done, total, msg)

    await guarded("proof_owner", lambda: stages.stage_proof_owners(ctx, client, step("proof_owner")),
                  lambda n: f"新确定 {n} 个证明的归属")
    await guarded("implicit", lambda: stages.stage_implicit(ctx, client, step("implicit")),
                  lambda s: f"新增 {s['added']} 条推断依赖、{s['external']} 项外部知识（丢弃 {s['dropped_unlocated']} 条无法在原文定位的结果）")
    tracker.start("checks")
    rerun_checks(ctx)
    tracker.done("checks", f"{len(ctx.risks)} 个结构提示")
    if partial:
        partial(assemble_report(ctx, _pipeline_info(cfg, client, tracker)))
    await guarded("symbols", lambda: stages.stage_symbol_meanings(ctx, client, step("symbols")),
                  lambda n: f"{n} 个符号")
    # Write the shared prefix (rule book + full paper) to the prompt cache once, before the fan-out.
    await stages.prewarm(ctx, client)
    await asyncio.gather(
        guarded("symbol_debate", lambda: stages.stage_symbol_debate(ctx, client, step("symbol_debate")),
                lambda s: f"审查 {s.get('debated', 0)} 个，未解决 {s.get('unresolved', 0)} 个"),
        guarded("unsupported", lambda: stages.stage_unsupported(ctx, client, step("unsupported")),
                lambda s: f"审查 {s.get('checked', 0)} 个，未解决 {s.get('unresolved', 0)} 个"),
        guarded("review", lambda: stages.stage_review(ctx, client, review_max, step("review")),
                lambda s: f"审查 {s['targets']} 个证明，{s['risks']} 个未解决的风险点、{s.get('minor', 0)} 个次要问题"),
    )
    # Post-processing runs last so that the inputs of all earlier (cached) stages stay unchanged.
    await guarded("external_merge", lambda: stages.stage_external_merge(ctx, client, step("external_merge")),
                  lambda s: f"合并 {s['merged']} 项重复、去掉 {s['dropped']} 项初等常识")
    await guarded("meta_review", lambda: stages.stage_meta_review(ctx, client, step("meta_review")),
                  lambda s: f"复核 {s.get('reviewed', 0)} 条，合并 {s.get('merged', 0)} 条，重新定级 {s.get('regraded', 0)} 条")
    await client.aclose()
    return assemble_report(ctx, _pipeline_info(cfg, client, tracker))


def run_pipeline_sync(root: Path, **kw) -> Report:
    return asyncio.run(run_pipeline(root, **kw))
