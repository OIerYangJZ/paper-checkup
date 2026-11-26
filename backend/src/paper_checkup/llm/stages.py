"""The LLM-backed stages. Each stage reads/writes the shared AnalysisContext."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from typing import Callable

from ..deterministic import AnalysisContext
from ..latex.preamble import PROVABLE_KINDS
from ..models import DebateTurn, Edge, Evidence, ExternalInfo, Node, ProofInfo, Risk
from . import prompts as P
from .context import digest, full_paper, locate_quote, loc_at, node_header, node_regions, numbered, render
from .provider import BudgetExceeded, LLMClient, LLMError

log = logging.getLogger("paper_checkup.stages")

Progress = Callable[[str, int, int, str], None]

IMPLICIT_TARGET_KINDS = PROVABLE_KINDS | {"definition", "conjecture", "assumption", "notation", "algorithm",
                                          "problem", "other"}


def _noop(*_a):
    pass


def _clean(text):
    """Strip prompt artifacts from model text before showing it: 'L123| ' prefixes and 〔id〕 markers."""
    if not text:
        return text
    text = re.sub(r"(?m)(^|\s)L\d+\|\s?", r"\1", text)
    text = re.sub(r"〔[^〕]*〕", "", text)
    return text.strip()


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + "\n…（截断）"


def _system(ctx: AnalysisContext, client: LLMClient) -> str:
    return P.shared_system(client.config.language, ctx.gb.mode)


def paper_prefix(ctx: AnalysisContext) -> str:
    """Full paper text, computed once per analysis: the shared cached prefix of every debate call."""
    if "paper_prefix" not in ctx.extra:
        ctx.extra["paper_prefix"] = full_paper(ctx)
    return ctx.extra["paper_prefix"]


async def prewarm(ctx: AnalysisContext, client: LLMClient) -> None:
    """Write the shared prefix (rule book + full paper) to each debate model's prompt cache before fanning out
    (author and reviewer may be different providers, e.g. DeepSeek and OpenAI)."""
    roles: dict[str, str] = {}
    for role in ("reviewer", "author"):
        roles.setdefault(client.config.models.get(role) or client.config.models.get("extract", ""), role)

    async def one(role: str):
        try:
            await client.json(role, _system(ctx, client),
                              [paper_prefix(ctx), "这是缓存预热请求。请只返回 {\"ok\": true}。"],
                              {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"],
                               "additionalProperties": False}, "prewarm", max_tokens=2000, use_disk_cache=False)
        except LLMError as e:  # best effort
            log.info("预热失败（不影响结果）：%s", e)

    await asyncio.gather(*(one(r) for r in roles.values()))


# Severity ceilings per error type (deterministic policy applied on top of the model's grading).
ERROR_TYPE_CAP = {"typo_or_notation": "low", "pseudocode_or_typesetting": "medium", "degenerate_or_convention": "info"}


def cap_severity(sev: str, error_type: str | None) -> str:
    cap = ERROR_TYPE_CAP.get(error_type or "")
    return _min_sev(sev, cap) if cap else sev


def _min_sev(a: str, b: str) -> str:
    order = ["high", "medium", "low", "info"]
    return a if order.index(a if a in order else "medium") >= order.index(b) else b


def _block(ctx: AnalysisContext, nid: str):
    return next((b for b in ctx.paper.blocks if b.id == nid), None)


def _explicit_preds(ctx: AnalysisContext, nid: str) -> list[Edge]:
    return [e for e in ctx.edges if e.target == nid and e.kind == "explicit"]


# ---------------------------------------------------------------------------
# Stage: proof ownership for unowned proofs
# ---------------------------------------------------------------------------

async def stage_proof_owners(ctx: AnalysisContext, client: LLMClient, progress: Progress = _noop) -> int:
    unowned = [(i, pp) for i, pp in enumerate(ctx.paper.proofs) if pp.owner is None]
    if not unowned:
        return 0
    lang = client.config.language
    text = ctx.flat.text
    parts = []
    for i, pp in unowned[:20]:
        before = text[max(ctx.paper.body_start, pp.outer_start - 1200):pp.outer_start]
        parts.append(f"### 证明 #{i}\n【前文】\n{render(ctx, before)}\n【证明开头】\n{render(ctx, text[pp.start:pp.start + 1500])}")
    data = await client.json("extract", P.owner_system(lang), [digest(ctx), "\n\n".join(parts)], P.OWNER_SCHEMA,
                             "proof_owner", max_tokens=4000)
    assigned = 0
    for item in data.get("proofs", []):
        idx, owner = item.get("proof_index"), item.get("owner_id")
        if not isinstance(idx, int) or idx >= len(ctx.paper.proofs) or not owner or owner not in ctx.nodes:
            continue
        if (item.get("confidence") or 0) < 0.6:
            continue
        pp = ctx.paper.proofs[idx]
        if pp.owner is not None:
            continue
        b = _block(ctx, owner)
        if b is None:
            continue
        pp.owner = b.index
        pp.how = "llm"
        b.proofs.append(idx)
        node = ctx.nodes[owner]
        node.proofs.append(ProofInfo(loc=loc_at(ctx, pp.outer_start, pp.outer_end - pp.outer_start),
                                     text=text[pp.start:pp.end].strip("\n"), source=pp.source, how="llm",
                                     title=pp.title))
        node.proof_chars = sum(len(x.text) for x in node.proofs)
        if "no_proof" in node.flags:
            node.flags.remove("no_proof")
        node.notes.append(f"证明归属由大模型判断：{item.get('reason', '')}")
        assigned += 1
    # Proof-context refs of newly owned proofs become explicit edges.
    if assigned:
        _refs_in_new_proofs(ctx)
    return assigned


def _refs_in_new_proofs(ctx: AnalysisContext):
    from ..analysis.graph import sentence_around

    gb = ctx.gb
    for r in ctx.paper.refs:
        if r.context != "proof" or r.block is not None:
            continue
        for pp in ctx.paper.proofs:
            if pp.how == "llm" and pp.start <= r.offset < pp.end and pp.owner is not None:
                dep = gb.block_ids[pp.owner]
                for k in r.keys:
                    target, kind = gb.resolve_label(k)
                    if target and target != dep:
                        ev = Evidence(loc=loc_at(ctx, r.offset, r.end - r.offset),
                                      text=sentence_around(ctx.flat.text, r.offset, r.end), via="ref", context="proof")
                        gb.add_edge(target, dep, "explicit", "proof", "ref", ev)
    ctx.edges[:] = list(gb.edges.values())


# ---------------------------------------------------------------------------
# Stage: implicit dependencies + external knowledge
# ---------------------------------------------------------------------------

def _implicit_item(ctx: AnalysisContext, nid: str) -> str:
    n = ctx.nodes[nid]
    preds = _explicit_preds(ctx, nid)
    known = "；".join(f"[{e.source}] {ctx.node_name(e.source)}" for e in preds if e.source in ctx.nodes) or "无"
    out = [f"### 条目 {node_header(ctx, nid)}", f"程序已确定的依赖：{known}"]
    if n.lead_in:
        out.append("【引出段落】\n" + _clip(render(ctx, n.lead_in), 1500))
    out.append("【陈述】\n" + _clip(render(ctx, n.statement), 4000))
    if n.proofs:
        ptxt = "\n\n".join(render(ctx, p.text) for p in n.proofs)
        out.append("【证明】\n" + _clip(ptxt, 9000))
    return "\n".join(out)


async def stage_implicit(ctx: AnalysisContext, client: LLMClient, progress: Progress = _noop) -> dict:
    lang = client.config.language
    targets = [n.id for n in sorted(ctx.nodes.values(), key=lambda n: n.order)
               if n.kind in IMPLICIT_TARGET_KINDS and n.order >= 0]
    batches: list[list[tuple[str, str]]] = []
    cur: list[tuple[str, str]] = []
    size = 0
    for nid in targets:
        item = _implicit_item(ctx, nid)
        if cur and (size + len(item) > 16000 or len(cur) >= 6):
            batches.append(cur)
            cur, size = [], 0
        cur.append((nid, item))
        size += len(item)
    if cur:
        batches.append(cur)
    dig = digest(ctx)
    system = P.implicit_system(lang)
    stats = {"batches": len(batches), "added": 0, "dropped_unlocated": 0, "external": 0}
    done = 0

    async def run_batch(batch):
        nonlocal done
        task = "请分析以下条目：\n\n" + "\n\n".join(item for _, item in batch)
        try:
            data = await client.json("extract", system, [dig, task], P.IMPLICIT_SCHEMA, "implicit_dependencies",
                                     max_tokens=8000)
        except LLMError as e:
            log.warning("隐式引用批次失败：%s", e)
            data = {"items": []}
        done += 1
        progress("implicit", done, len(batches), f"隐式引用补全 {done}/{len(batches)}")
        return batch, data

    results = await asyncio.gather(*(run_batch(b) for b in batches))
    gb = ctx.gb
    for batch, data in results:
        allowed = {nid for nid, _ in batch}
        for item in data.get("items", []):
            nid = item.get("node_id")
            if nid not in allowed:
                continue
            regions = node_regions(ctx, nid)
            for d in item.get("dependencies", []):
                tid = d.get("target_id")
                conf = float(d.get("confidence") or 0)
                if tid not in ctx.nodes or tid == nid or conf < 0.7:
                    continue
                if ctx.nodes[tid].kind == "external":
                    continue
                off = locate_quote(ctx, d.get("quote"), regions)
                if off is None:
                    stats["dropped_unlocated"] += 1
                    continue
                key = (tid, nid)
                existing = gb.edges.get(key)
                if existing is not None and existing.kind == "explicit":
                    continue
                where = d.get("where") or "proof"
                ctx_kind = "statement" if where in ("statement", "lead-in") else "proof"
                ev = Evidence(loc=loc_at(ctx, off, len(d.get("quote") or "")), text=_clean(d.get("quote")) or "", via="llm",
                              context=where)
                was_symbol = existing is not None and existing.kind == "symbol"
                e = gb.add_edge(tid, nid, "inferred", ctx_kind, "llm", ev, confidence=round(conf, 2),
                                reason=_clean(d.get("reason")))
                if e is not None and (existing is None or was_symbol):
                    stats["added"] += 1
            for x in item.get("external", []):
                name = (x.get("name") or "").strip()
                if not name:
                    continue
                off = locate_quote(ctx, x.get("quote"), regions)
                if off is None:
                    stats["dropped_unlocated"] += 1
                    continue
                norm = re.sub(r"[\s\-–—'’`\"“”()（）]+", "", name.lower())
                slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]
                if not slug or not slug.isascii() or len(slug) < 3:
                    slug = hashlib.sha1(norm.encode()).hexdigest()[:10]
                ext_id = f"ext-k-{slug}"
                # Merge near-duplicate names (e.g. the same theorem named twice).
                for other in ctx.nodes.values():
                    if other.id.startswith("ext-k-") and re.sub(r"[\s\-–—'’`\"“”()（）]+", "", (other.title or "").lower()) == norm:
                        ext_id = other.id
                        break
                if ext_id not in ctx.nodes:
                    ctx.nodes[ext_id] = Node(id=ext_id, kind="external", display="外部知识", title=name,
                                             statement=x.get("description") or name, order=-1,
                                             external=ExternalInfo(source="llm", description=x.get("description"),
                                                                   evidence=x.get("quote")))
                    stats["external"] += 1
                where = x.get("where") or "proof"
                ev = Evidence(loc=loc_at(ctx, off, len(x.get("quote") or "")), text=x.get("quote") or "", via="llm",
                              context=where)
                gb.add_edge(ext_id, nid, "inferred", "statement" if where != "proof" else "proof", "llm", ev,
                            confidence=0.8, reason=f"用到了外部知识：{name}")
    ctx.edges[:] = list(gb.edges.values())
    return stats


# ---------------------------------------------------------------------------
# Stage: symbol meanings + symbol debate
# ---------------------------------------------------------------------------

async def stage_symbol_meanings(ctx: AnalysisContext, client: LLMClient, progress: Progress = _noop) -> int:
    lang = client.config.language
    syms = ctx.symbols
    if not syms:
        return 0
    batches = [syms[i:i + 30] for i in range(0, len(syms), 30)]
    dig = digest(ctx, max_statement=200)
    system = P.symbol_meaning_system(lang)
    filled = 0
    done = 0

    async def run(batch):
        nonlocal done
        lines = []
        for s in batch:
            defs = [d for d in s.definitions if d.scope != "macro"][:2]
            ctxs = " ‖ ".join(render(ctx, d.context)[:320] for d in defs)
            if not ctxs and s.first_use_context:
                ctxs = "（首次出现）" + render(ctx, s.first_use_context)[:320]
            exp = f" 宏展开：{s.macro_expansion}" if s.macro_expansion else ""
            lines.append(f"- id={s.id} 类型={s.kind} 记号：{s.latex}{exp}\n  语境：{ctxs}")
        try:
            data = await client.json("extract", system, [dig, "符号列表：\n" + "\n".join(lines)],
                                     P.SYMBOL_MEANING_SCHEMA, "symbol_meanings", max_tokens=6000)
        except LLMError as e:
            log.warning("符号含义批次失败：%s", e)
            data = {"symbols": []}
        done += 1
        progress("symbols", done, len(batches), f"符号含义 {done}/{len(batches)}")
        return data

    by_id = {s.id: s for s in syms}
    for data in await asyncio.gather(*(run(b) for b in batches)):
        for item in data.get("symbols", []):
            s = by_id.get(item.get("id"))
            if s is not None and item.get("meaning"):
                s.meaning = item["meaning"].strip()
                s.meaning_source = "llm"
                filled += 1
    return filled


async def stage_symbol_debate(ctx: AnalysisContext, client: LLMClient, progress: Progress = _noop) -> dict:
    lang = client.config.language
    flagged = [s for s in ctx.symbols if {"used_before_definition", "multiple_definitions"} & set(s.flags)][:40]
    if not flagged:
        return {"debated": 0}
    paper = paper_prefix(ctx)
    system = _system(ctx, client)
    lines = []
    for s in flagged:
        issues = []
        if "used_before_definition" in s.flags:
            issues.append("在定义之前被使用")
        if "multiple_definitions" in s.flags:
            issues.append("在多处被定义")
        defs = "\n".join(f"    · 定义 @{d.loc.file}:L{d.loc.line_start}：{render(ctx, d.context)[:360]}"
                         for d in s.definitions if d.scope != "macro")
        fu = (f"\n    · 首次出现 @{s.first_use.file}:L{s.first_use.line_start}：{render(ctx, s.first_use_context or '')[:360]}"
              if s.first_use else "")
        lines.append(f"- id={s.id} 记号：{s.latex}（{s.kind}）含义：{s.meaning or '未知'}\n  疑点：{'、'.join(issues)}\n{defs}{fu}")
    task = "## 需要核查的符号\n" + "\n".join(lines)
    progress("symbol_debate", 0, 2, "符号争议：作者辩护")
    a = await client.json("author", system, [paper, P.AUTHOR_ROLE + "\n\n" + task, P.SYMBOL_AUTHOR_TASK],
                          P.SYMBOL_AUTHOR_SCHEMA, "symbol_author", max_tokens=8000)
    progress("symbol_debate", 1, 2, "符号争议：审稿人裁决")
    r = await client.json("reviewer", system,
                          [paper, P.REVIEWER_ROLE + "\n\n" + task,
                           "## 作者的辩护\n" + json.dumps(a, ensure_ascii=False, indent=1), P.SYMBOL_REVIEWER_TASK],
                          P.SYMBOL_REVIEWER_SCHEMA, "symbol_reviewer", max_tokens=8000)
    progress("symbol_debate", 2, 2, "符号争议完成")
    author_by = {x.get("id"): x for x in a.get("responses", [])}
    verdict_by = {x.get("id"): x for x in r.get("verdicts", [])}
    debated_ids = {s.id for s in flagged}
    # Replace structural symbol risks for debated symbols.
    ctx.risks[:] = [x for x in ctx.risks if not (x.source == "structural" and x.symbol_id in debated_ids)]
    unresolved = 0
    for s in flagged:
        v = verdict_by.get(s.id)
        au = author_by.get(s.id, {})
        if v is None:
            continue
        verdict = v.get("verdict")
        if verdict == "resolved":
            s.note = f"审查结论：无问题。{v.get('comment', '')}"
            s.flags = [f for f in s.flags if f not in ("used_before_definition", "multiple_definitions")] + ["reviewed_ok"]
            continue
        unresolved += verdict == "unresolved"
        s.note = v.get("comment")
        s.flags.append("conflicting_meaning" if "multiple_definitions" in s.flags and verdict == "unresolved" else "reviewed_issue")
        d0 = next((d for d in s.definitions if d.scope != "macro"), None)
        sev = v.get("severity", "low")
        if verdict == "minor":
            sev = _min_sev(sev, "low")
        ctx.risks.append(Risk(
            id=f"Y{len(ctx.risks) + 1}", category="symbol", severity=sev, source="review",
            title=_clean(v.get("title")) or f"符号 {s.latex} 存在问题", description=_clean(v.get("comment", "")),
            node_ids=[x for x in {s.first_use_node, s.defined_in_node, *(d.node_id for d in s.definitions)} if x][:5],
            loc=s.first_use if "used_before_definition" in s.flags else (d0.loc if d0 else s.first_use),
            quote=s.first_use_context if "used_before_definition" in s.flags else (d0.context if d0 else None),
            status="minor" if verdict == "minor" else ("conceded" if au.get("position") == "issue" else "unresolved"),
            debate=[
                DebateTurn(role="reviewer", stage="程序检测", content=f"符号 {s.latex}：" + "、".join(
                    x for x in ("在定义之前被使用" if "used_before_definition" in s.flags else "",
                                "在多处被定义" if "multiple_definitions" in s.flags else "") if x)),
                DebateTurn(role="author", stage="作者辩护", content=_clean((au.get("argument") or "（无回应）") +
                           (f"\n原文：{au['quote']}" if au.get("quote") else ""))),
                DebateTurn(role="reviewer", stage="审稿人裁决", content=_clean(v.get("comment", ""))),
            ],
            author_position=_clean(au.get("argument")), reviewer_position=_clean(v.get("comment")), symbol_id=s.id))
    return {"debated": len(flagged), "unresolved": unresolved}


# ---------------------------------------------------------------------------
# Stage: unsupported results (no proof, no citation)
# ---------------------------------------------------------------------------

async def stage_unsupported(ctx: AnalysisContext, client: LLMClient, progress: Progress = _noop) -> dict:
    lang = client.config.language
    risks = [r for r in ctx.risks if r.source == "structural" and r.category == "unsupported" and r.node_ids]
    if not risks:
        return {"checked": 0}
    paper = paper_prefix(ctx)
    system = _system(ctx, client)
    done = 0

    async def run(risk: Risk):
        nonlocal done
        nid = risk.node_ids[0]
        b = _block(ctx, nid)
        if b is None:
            return risk, None, None
        mentions = "\n".join(f"- {m.loc.file}:L{m.loc.line_start}：{render(ctx, m.text)}" for m in ctx.nodes[nid].mentions[:8])
        task = (f"# 待核查结论 {node_header(ctx, nid)}（见全文中【{nid} …】标记）\n## 陈述\n{render(ctx, ctx.nodes[nid].statement)}\n\n"
                f"## 正文中提到该结论的地方\n{mentions or '无'}")
        try:
            a = await client.json("author", system, [paper, P.AUTHOR_ROLE + "\n\n" + task, P.SUPPORT_AUTHOR_TASK],
                                  P.SUPPORT_AUTHOR_SCHEMA, "support_author", max_tokens=4000)
            v = await client.json("reviewer", system,
                                  [paper, P.REVIEWER_ROLE + "\n\n" + task,
                                   "## 作者给出的依据\n" + json.dumps(a, ensure_ascii=False, indent=1),
                                   P.SUPPORT_REVIEWER_TASK], P.SUPPORT_REVIEWER_SCHEMA, "support_reviewer",
                                  max_tokens=3000)
        except LLMError as e:
            log.warning("无来源结论审查失败 %s：%s", nid, e)
            return risk, None, None
        done += 1
        progress("unsupported", done, len(risks), f"无来源结论审查 {done}/{len(risks)}")
        return risk, a, v

    results = await asyncio.gather(*(run(r) for r in risks))
    unresolved = 0
    for risk, a, v in results:
        if a is None or v is None:
            continue
        nid = risk.node_ids[0]
        node = ctx.nodes[nid]
        ctx.risks.remove(risk)
        verdict = v.get("verdict")
        if verdict == "resolved":
            node.notes.append(_clean(f"未找到形式化证明，但审查认为有依据（{a.get('kind')}）：{v.get('comment', '')}"))
            node.flags.append(f"support:{a.get('kind')}")
            continue
        unresolved += verdict == "unresolved"
        off = locate_quote(ctx, a.get("quote"), [(ctx.paper.body_start, ctx.paper.body_end)], line_hint=a.get("line"))
        sev = v.get("severity", "medium")
        if verdict == "minor":
            sev = _min_sev(sev, "low")
        ctx.risks.append(Risk(
            id=f"U{len(ctx.risks) + 1}", category="unsupported", severity=sev, source="review",
            title=_clean(v.get("title")) or risk.title, description=_clean(v.get("comment", "")), node_ids=[nid],
            loc=loc_at(ctx, off, len(a.get("quote") or "")) if off is not None else node.loc,
            quote=_clean(a.get("quote")) if off is not None else None,
            status="minor" if verdict == "minor" else ("conceded" if not a.get("support_found") else "unresolved"),
            debate=[
                DebateTurn(role="reviewer", stage="程序检测", content=risk.description),
                DebateTurn(role="author", stage="作者寻找依据",
                           content=_clean(f"[{a.get('kind')}] {a.get('argument', '')}" + (f"\n原文：{a['quote']}" if a.get("quote") else ""))),
                DebateTurn(role="reviewer", stage="审稿人裁决", content=_clean(v.get("comment", ""))),
            ],
            author_position=_clean(a.get("argument")), reviewer_position=_clean(v.get("comment"))))
    return {"checked": len(risks), "unresolved": unresolved}


# ---------------------------------------------------------------------------
# Stage: proof review (author / reviewer debate)
# ---------------------------------------------------------------------------

KIND_WEIGHT = {"theorem": 6, "proposition": 4, "lemma": 3, "corollary": 2, "claim": 2, "fact": 1}


def select_review_targets(ctx: AnalysisContext, max_targets: int) -> list[str]:
    cands = []
    for n in ctx.nodes.values():
        if n.kind not in PROVABLE_KINDS or not n.proofs:
            continue
        preds = [e for e in ctx.edges if e.target == n.id and e.kind in ("explicit", "inferred")]
        gaps = len(ctx.checker.gap_hints.get(n.id, []))
        score = KIND_WEIGHT.get(n.kind, 1) + min(4, len(preds) * 0.5) + min(3, gaps) + min(3, n.proof_chars / 3000)
        cands.append((score, n.order, n.id))
    cands.sort(key=lambda t: (-t[0], t[1]))
    # Highest priority first: debates start in this order, so a spend limit cuts the least important ones.
    return [nid for _, _, nid in cands[:max_targets]]


def review_context(ctx: AnalysisContext, nid: str) -> str:
    n = ctx.nodes[nid]
    b = _block(ctx, nid)
    out = [f"# 待审查条目 {node_header(ctx, nid)}（在全文中以【{nid} …】标记）", "## 陈述", render(ctx, n.statement)]
    for pi in (b.proofs if b else []):
        pp = ctx.paper.proofs[pi]
        file, _ = ctx.flat.loc(pp.start)
        out.append(f"## 证明（来源：{pp.source}，文件 {file}，行号为源文件行号）")
        out.append(numbered(ctx, pp.start, pp.end, max_chars=24000))
    preds = [e for e in ctx.edges if e.target == nid and e.kind in ("explicit", "inferred", "symbol")]
    if preds:
        out.append("## 该条目用到的条目（被引用结论的完整陈述，用于核对前提条件）")
        seen = set()
        for e in sorted(preds, key=lambda e: ctx.nodes[e.source].order if e.source in ctx.nodes else 0):
            if e.source in seen or e.source not in ctx.nodes:
                continue
            seen.add(e.source)
            m = ctx.nodes[e.source]
            kind = {"explicit": "显式引用", "inferred": "推断依赖", "symbol": "记号依赖"}[e.kind]
            if m.kind == "external":
                ev = e.evidence[0].text if e.evidence else ""
                out.append(f"- [{m.id}] {ctx.node_name(m.id)}（{kind}）：{(m.statement or '')[:300]}；引用处：{render(ctx, ev)[:300]}")
            else:
                out.append(f"- {node_header(ctx, m.id)}（{kind}，出现在{ '陈述' if e.context == 'statement' else '证明'}中）：\n  {_clip(render(ctx, m.statement), 2500)}")
    syms = [s for s in ctx.symbols if s.id in set(n.symbols_used)][:25]
    if syms:
        out.append("## 相关记号")
        for s in syms:
            d0 = next((d for d in s.definitions if d.scope != "macro"), None)
            where = f"（定义于 {d0.loc.file}:L{d0.loc.line_start}）" if d0 else ""
            out.append(f"- {s.latex}：{s.meaning or (s.macro_expansion or '')}{where}")
    return "\n".join(out)


async def _review_one(ctx: AnalysisContext, client: LLMClient, nid: str, paper: str) -> list[Risk]:
    system = _system(ctx, client)
    node = ctx.nodes[nid]
    rc = review_context(ctx, nid)
    author_ctx = P.AUTHOR_ROLE + "\n\n" + rc
    reviewer_ctx = P.REVIEWER_ROLE + "\n\n" + rc
    a1 = await client.json("author", system, [paper, author_ctx, P.AUTHOR_STEPS_TASK], P.STEPS_SCHEMA,
                           "author_steps", max_tokens=8000)
    steps_txt = json.dumps(a1, ensure_ascii=False, indent=1)
    r1 = await client.json("reviewer", system,
                           [paper, reviewer_ctx, "## 作者对关键步骤的说明\n" + steps_txt, P.REVIEWER_CHALLENGE_TASK],
                           P.CHALLENGES_SCHEMA, "reviewer_challenges", max_tokens=8000)
    challenges = [c for c in r1.get("challenges", []) if c.get("concern")]
    node.review = {"status": "passed", "steps": len(a1.get("steps", [])), "challenges": len(challenges),
                   "unresolved": 0}
    if not challenges:
        return []
    ch_txt = json.dumps({"challenges": challenges}, ensure_ascii=False, indent=1)
    a2 = await client.json("author", system,
                           [paper, author_ctx, "## 你之前给出的步骤说明\n" + steps_txt, "## 审稿人的质疑\n" + ch_txt,
                            P.AUTHOR_REBUTTAL_TASK], P.REBUTTAL_SCHEMA, "author_rebuttal", max_tokens=8000)
    resp_txt = json.dumps(a2, ensure_ascii=False, indent=1)
    r2 = await client.json("reviewer", system,
                           [paper, reviewer_ctx, "## 作者对关键步骤的说明\n" + steps_txt, "## 你提出的质疑\n" + ch_txt,
                            "## 作者的回应\n" + resp_txt, P.REVIEWER_VERDICT_TASK], P.VERDICT_SCHEMA,
                           "reviewer_verdict", max_tokens=8000)
    regions = node_regions(ctx, nid, include_lead=True) + [(ctx.paper.body_start, ctx.paper.body_end)]
    risks = _debate_risks(ctx, nid, a1, challenges, a2, r2, regions)
    substantive = [r for r in risks if r.status != "minor"]
    node.review.update({"status": "issues" if substantive else "passed", "unresolved": len(substantive),
                        "minor": len(risks) - len(substantive)})
    return risks


def _debate_risks(ctx: AnalysisContext, nid: str, a1: dict, challenges: list[dict], a2: dict, r2: dict,
                  regions: list[tuple[int, int]], step_label: str = "作者 · 步骤依据") -> list[Risk]:
    """Risks for the challenges that the verdict left unresolved / minor, with the debate transcript."""
    node = ctx.nodes[nid]
    steps = {s.get("step_id"): s for s in a1.get("steps", [])}
    responses = {x.get("challenge_id"): x for x in a2.get("responses", [])}
    verdicts = {x.get("challenge_id"): x for x in r2.get("verdicts", [])}
    risks = []
    stance_label = {"defend": "辩护", "minor": "次要", "partial": "部分承认", "concede": "承认"}
    for c in challenges:
        cid = c.get("challenge_id")
        v = verdicts.get(cid)
        if v is None or v.get("verdict") not in ("unresolved", "minor"):
            continue
        resp = responses.get(cid, {})
        step = steps.get(c.get("step_id")) if c.get("step_id") else None
        off = locate_quote(ctx, c.get("quote"), regions, line_hint=c.get("line"))
        loc = loc_at(ctx, off, len(c.get("quote") or "")) if off is not None else node.loc
        related = [nid]
        if step:
            related += [u for u in step.get("uses", []) if u in ctx.nodes and u != nid]
        related += [m for m in re.findall(r"\b((?:lem|thm|prop|cor|def|claim|fact|conj|asm|rem|tab|fig|sec|eq|alg|as)-[\w.]+)",
                                          c.get("concern", ""))
                    if m in ctx.nodes and m not in related]
        sev = v.get("severity") or c.get("severity", "medium")
        if v.get("verdict") == "minor":
            sev = _min_sev(sev, "low")
        error_type = c.get("error_type")
        sev = cap_severity(sev, error_type)
        debate = []
        if step:
            debate.append(DebateTurn(role="author", stage=step_label,
                                     content=_clean(f"{step.get('claim', '')}\n依据：{step.get('justification', '')}"
                                             + (f"\n前提核对：{step['hypotheses_check']}" if step.get("hypotheses_check") not in (None, "", "无") else ""))))
        debate.append(DebateTurn(role="reviewer", stage="审稿人 · 质疑",
                                 content=_clean(c.get("concern", "")) + ("（审稿人认为影响结论本身）" if c.get("affects") == "statement" else "")))
        debate.append(DebateTurn(role="author", stage="作者 · 回应",
                                 content=_clean(f"[{stance_label.get(resp.get('stance'), '无回应')}] "
                                         f"{resp.get('response', '')}" + (f"\n原文：{resp['evidence_quote']}" if resp.get("evidence_quote") else ""))))
        debate.append(DebateTurn(role="reviewer", stage="审稿人 · 裁决", content=_clean(v.get("final_comment", ""))))
        risks.append(Risk(
            id="", category=c.get("category", "other"), severity=sev,
            source="review", title=_clean(v.get("title")) or _clean(c.get("concern", ""))[:30],
            description=_clean(c.get("concern", "")), node_ids=related, loc=loc,
            quote=_clean(c.get("quote")) if off is not None else None,
            status="minor" if v.get("verdict") == "minor" else ("conceded" if resp.get("stance") == "concede" else "unresolved"),
            debate=debate, author_position=_clean(resp.get("response")), reviewer_position=_clean(v.get("final_comment")),
            error_type=error_type))
    return risks


async def stage_review(ctx: AnalysisContext, client: LLMClient, max_targets: int = 30,
                       progress: Progress = _noop) -> dict:
    targets = select_review_targets(ctx, max_targets)
    for n in ctx.nodes.values():
        if n.kind in PROVABLE_KINDS and n.proofs and n.id not in targets:
            n.review = {"status": "skipped"}
    paper = paper_prefix(ctx)
    done = 0
    total_risks: list[Risk] = []

    budget_skipped = 0

    async def run(nid):
        nonlocal done, budget_skipped
        try:
            rs = await _review_one(ctx, client, nid, paper)
        except BudgetExceeded as e:
            ctx.nodes[nid].review = {"status": "skipped", "reason": "budget", "error": str(e)}
            budget_skipped += 1
            rs = []
        except LLMError as e:
            log.warning("审稿对抗失败 %s：%s", nid, e)
            ctx.nodes[nid].review = {"status": "error", "error": str(e)[:200]}
            rs = []
        done += 1
        progress("review", done, len(targets), f"审稿对抗 {done}/{len(targets)}：{ctx.node_name(nid)}")
        return rs

    for rs in await asyncio.gather(*(run(t) for t in targets)):
        total_risks.extend(rs)
    for i, r in enumerate(total_risks):
        r.id = f"R{i + 1}"
    ctx.risks.extend(total_risks)
    return {"targets": len(targets) - budget_skipped, "budget_skipped": budget_skipped,
            "risks": sum(1 for r in total_risks if r.status != "minor"),
            "minor": sum(1 for r in total_risks if r.status == "minor")}


# ---------------------------------------------------------------------------
# Structure mode: core claims, claim review (author / reviewer debate), numeric cross-check
# ---------------------------------------------------------------------------

MAX_CLAIMS = 12


async def stage_claims(ctx: AnalysisContext, client: LLMClient, progress: Progress = _noop) -> dict:
    """Extract the paper's core claims; each must be located verbatim in the text, or it is dropped."""
    paper = paper_prefix(ctx)
    data = await client.json("extract", _system(ctx, client), [paper, P.CLAIMS_TASK], P.CLAIMS_SCHEMA, "claims",
                             max_tokens=12000)
    body = [(ctx.paper.body_start, ctx.paper.body_end)]
    gb = ctx.gb
    claims: list[str] = []
    seen: list[int] = []
    dropped = 0
    per_unit: dict[str, int] = {}
    for c in data.get("claims", []):
        if len(claims) >= MAX_CLAIMS:
            break
        quote = _clean(c.get("quote") or "")
        off = locate_quote(ctx, quote, body, line_hint=c.get("line"), line_fallback=False)
        if off is None or not quote:
            dropped += 1
            continue
        if any(abs(off - o) < 40 for o in seen):
            continue
        seen.append(off)
        unit = gb.unit_at(off)
        un = ctx.nodes.get(unit) if unit else None
        cid = f"as-{len(claims) + 1}"
        per_unit[unit or ""] = per_unit.get(unit or "", 0) + 1
        node = Node(id=cid, kind="assertion", display="论断", number=str(len(claims) + 1),
                    title=_clean(c.get("summary") or "")[:60] or None, statement=quote,
                    loc=loc_at(ctx, off, len(quote)), section_id=un.section_id if un else None,
                    parent=unit, order=(un.order if un else 0) + min(9, per_unit[unit or ""]),
                    flags=[f"claim:{c.get('kind') or 'other'}", f"importance:{c.get('importance') or 'supporting'}"],
                    statement_chars=len(quote))
        ctx.nodes[cid] = node
        for eid in c.get("evidence_ids") or []:
            if eid in ctx.nodes and eid != cid and ctx.nodes[eid].kind not in ("assertion", "external"):
                gb.add_edge(eid, cid, "inferred", "statement", "llm",
                            Evidence(loc=node.loc, text=quote[:300], via="llm", context="statement"),
                            confidence=0.8, reason=f"论断抽取阶段标注的证据：{ctx.node_name(eid)}")
        claims.append(cid)
    ctx.edges[:] = list(gb.edges.values())
    ctx.extra["claims"] = claims
    return {"claims": len(claims), "dropped": dropped}


def claim_context(ctx: AnalysisContext, cid: str) -> str:
    n = ctx.nodes[cid]
    unit = n.parent if n.parent in ctx.nodes else None
    where = f"，位于 {ctx.node_name(unit)}" if unit else ""
    out = [f"# 待审查论断 [{cid}]{where}（{n.loc.file}:L{n.loc.line_start}）" if n.loc else f"# 待审查论断 [{cid}]",
           "## 论断原文", render(ctx, n.statement)]
    if n.title:
        out.append(f"概括：{n.title}")
    evid = [e.source for e in ctx.edges if e.target == cid and e.source in ctx.nodes]
    if evid:
        out.append("## 论断抽取阶段为它标注的证据（内容见全文中的同名标记；全文中也可检索其他证据）")
        for eid in evid:
            m = ctx.nodes[eid]
            rng = m.loc
            span = f"{rng.file}:L{rng.line_start}–{rng.line_end}" if rng else ""
            cap = f"：{_clip(render(ctx, m.statement), 300)}" if m.kind in ("table", "figure", "pseudocode", "equation") else ""
            out.append(f"- [{eid}] {ctx.node_name(eid)}（{span}）{cap}")
    else:
        out.append("## 论断抽取阶段没有为它标注证据（请在全文中检索）")
    cands = [c for c in getattr(ctx.checker, "numeric_candidates", [])
             if {c["a_unit"], c["b_unit"]} & ({unit} | set(evid))]
    if cands:
        out.append("## 程序找到的相关数字不一致候选（启发式配对，需要核实）")
        for c in cands:
            out.append(f"- {c['metric']}：{c['a']}（{c['a_loc'].file}:L{c['a_loc'].line_start}）与 {c['b']}"
                       f"（{c['b_loc'].file}:L{c['b_loc'].line_start}）。{c['table_note']}")
    return "\n".join(out)


async def _review_claim(ctx: AnalysisContext, client: LLMClient, cid: str, paper: str) -> list[Risk]:
    system = _system(ctx, client)
    node = ctx.nodes[cid]
    cc = claim_context(ctx, cid)
    author_ctx = P.CLAIM_AUTHOR_ROLE + "\n\n" + cc
    reviewer_ctx = P.CLAIM_REVIEWER_ROLE + "\n\n" + cc
    a1 = await client.json("author", system, [paper, author_ctx, P.CLAIM_AUTHOR_TASK], P.STEPS_SCHEMA,
                           "claim_steps", max_tokens=8000)
    steps_txt = json.dumps(a1, ensure_ascii=False, indent=1)
    r1 = await client.json("reviewer", system,
                           [paper, reviewer_ctx, "## 作者给出的证据链\n" + steps_txt, P.CLAIM_REVIEWER_TASK],
                           P.CLAIM_CHALLENGES_SCHEMA, "claim_challenges", max_tokens=8000)
    challenges = [c for c in r1.get("challenges", []) if c.get("concern")]
    node.review = {"status": "passed", "steps": len(a1.get("steps", [])), "challenges": len(challenges), "unresolved": 0}
    if not challenges:
        return []
    ch_txt = json.dumps({"challenges": challenges}, ensure_ascii=False, indent=1)
    a2 = await client.json("author", system,
                           [paper, author_ctx, "## 你之前给出的证据链\n" + steps_txt, "## 审稿人的质疑\n" + ch_txt,
                            P.CLAIM_REBUTTAL_TASK], P.REBUTTAL_SCHEMA, "claim_rebuttal", max_tokens=8000)
    resp_txt = json.dumps(a2, ensure_ascii=False, indent=1)
    r2 = await client.json("reviewer", system,
                           [paper, reviewer_ctx, "## 作者给出的证据链\n" + steps_txt, "## 你提出的质疑\n" + ch_txt,
                            "## 作者的回应\n" + resp_txt, P.CLAIM_VERDICT_TASK], P.VERDICT_SCHEMA,
                           "claim_verdict", max_tokens=8000)
    risks = _debate_risks(ctx, cid, a1, challenges, a2, r2, [(ctx.paper.body_start, ctx.paper.body_end)],
                          step_label="作者 · 证据")
    substantive = [r for r in risks if r.status != "minor"]
    node.review.update({"status": "issues" if substantive else "passed", "unresolved": len(substantive),
                        "minor": len(risks) - len(substantive)})
    return risks


async def stage_claim_review(ctx: AnalysisContext, client: LLMClient, progress: Progress = _noop) -> dict:
    claims = list(ctx.extra.get("claims", []))
    # Core claims first, so a spend limit cuts the supporting ones.
    claims.sort(key=lambda c: (0 if "importance:core" in ctx.nodes[c].flags else 1, ctx.nodes[c].order))
    paper = paper_prefix(ctx)
    done = budget_skipped = 0
    out: list[Risk] = []

    async def run(cid):
        nonlocal done, budget_skipped
        try:
            rs = await _review_claim(ctx, client, cid, paper)
        except BudgetExceeded as e:
            ctx.nodes[cid].review = {"status": "skipped", "reason": "budget", "error": str(e)}
            budget_skipped += 1
            rs = []
        except LLMError as e:
            log.warning("论断审查失败 %s：%s", cid, e)
            ctx.nodes[cid].review = {"status": "error", "error": str(e)[:200]}
            rs = []
        done += 1
        progress("claim_review", done, len(claims), f"论断审查 {done}/{len(claims)}：{ctx.node_name(cid)}")
        return rs

    for rs in await asyncio.gather(*(run(c) for c in claims)):
        out.extend(rs)
    for i, r in enumerate(out):
        r.id = f"Q{i + 1}"
    ctx.risks.extend(out)
    return {"claims": len(claims) - budget_skipped, "budget_skipped": budget_skipped,
            "risks": sum(1 for r in out if r.status != "minor"), "minor": sum(1 for r in out if r.status == "minor")}


async def stage_numeric_check(ctx: AnalysisContext, client: LLMClient, progress: Progress = _noop) -> dict:
    """The reviewer confirms or dismisses the program's numeric-inconsistency candidates (one call)."""
    cands = list(getattr(ctx.checker, "numeric_candidates", []))
    if not cands:
        return {"checked": 0}
    lines = []
    for c in cands:
        lines.append(f"- id={c['risk_id']} 指标 {c['metric']}：{c['a']}（{ctx.node_name(c['a_unit'])}，{c['a_loc'].file}:L{c['a_loc'].line_start}）"
                     f" 与 {c['b']}（{ctx.node_name(c['b_unit'])}，{c['b_loc'].file}:L{c['b_loc'].line_start}）。{c['table_note']}\n"
                     f"  · {render(ctx, c['a_sent'])}\n  · {render(ctx, c['b_sent'])}")
    data = await client.json("reviewer", _system(ctx, client),
                             [paper_prefix(ctx), P.NUMERIC_TASK, "## 候选\n" + "\n".join(lines)],
                             P.NUMERIC_SCHEMA, "numeric_check", max_tokens=8000)
    by_id = {r.id: r for r in ctx.risks if r.source == "structural" and r.category == "numeric"}
    confirmed = dismissed = 0
    for it in data.get("items", []):
        r = by_id.get(it.get("id"))
        if r is None:
            continue
        verdict = it.get("verdict")
        comment = _clean(it.get("comment") or "")
        if verdict == "different":
            ctx.risks.remove(r)
            dismissed += 1
            continue
        program = DebateTurn(role="reviewer", stage="程序检测", content=r.description)
        check = DebateTurn(role="reviewer", stage="审稿人 · 核对", content=comment)
        if verdict == "inconsistent":
            sev = it.get("severity") if it.get("severity") in ("high", "medium", "low", "info") else "medium"
            r.source, r.severity, r.status = "review", sev, "unresolved"
            r.title = _clean(it.get("title")) or r.title
            r.reviewer_position = comment
            r.error_type = "logic"
            r.debate = [program, check]
            confirmed += 1
        else:   # unclear: keep the program's candidate, with the reviewer's note
            r.editor_note = comment
            r.debate = [program, check]
    return {"checked": len(cands), "confirmed": confirmed, "dismissed": dismissed}


# ---------------------------------------------------------------------------
# Structure mode: derivation review (sections dense in displayed equations)
# ---------------------------------------------------------------------------

MAX_DERIVATIONS = 12


def select_derivation_targets(ctx: AnalysisContext, max_targets: int = MAX_DERIVATIONS) -> list[str]:
    """Sections whose own text holds a derivation: at least 3 displayed formulas (outside floats); ranked by the
    number of displays, numbered equations, and equation references into or within the section."""
    if ctx.gb.mode != "structure":
        return []
    floats = [(f.env.begin, f.env.end) for f in ctx.paper.floats]
    eq_nodes = [n for n in ctx.nodes.values() if n.kind == "equation"]
    cands = []
    for nid, (a, z) in ctx.gb.unit_range.items():
        n = ctx.nodes.get(nid)
        if n is None or n.kind != "section" or nid == "abstract":
            continue
        disp = [m for m in ctx.paper.math if m.display and a <= m.start < z and not any(x <= m.start < y for x, y in floats)]
        if len(disp) < 3:
            continue
        inside = {e.id for e in eq_nodes if e.id in ctx.gb.start_of and a <= ctx.gb.start_of[e.id] < z}
        eq_refs = sum(1 for e in ctx.edges if e.source.startswith("eq-") and (e.target == nid or e.target in inside))
        score = len(disp) + 2 * len(inside) + eq_refs
        cands.append((score, n.order, nid))
    cands.sort(key=lambda t: (-t[0], t[1]))
    return [nid for _, _, nid in cands[:max_targets]]


def derivation_context(ctx: AnalysisContext, sid: str) -> str:
    n = ctx.nodes[sid]
    a, z = ctx.gb.unit_range[sid]
    title = f" {n.title}" if n.title else ""
    out = [f"# 待审查推导：[{sid}] {ctx.node_name(sid)}{title}（在全文中以【{sid} · …】标记开头）",
           "## 本节原文（行号为源文件行号）", numbered(ctx, a, z, max_chars=24000)]
    inside = {e for e in ctx.nodes if e.startswith("eq-") and e in ctx.gb.start_of and a <= ctx.gb.start_of[e] < z}
    used = []
    for e in ctx.edges:
        if e.source.startswith("eq-") and e.source not in inside and (e.target == sid or e.target in inside) \
                and e.source in ctx.nodes and e.source not in used:
            used.append(e.source)
    if used:
        out.append("## 本节用到的其他公式")
        for eid in used:
            m = ctx.nodes[eid]
            where = ctx.node_name(m.section_id) if m.section_id in ctx.nodes else ""
            out.append(f"- [{eid}] {ctx.node_name(eid)}（{where}）：{_clip(m.statement, 600)}")
    syms = [s for s in ctx.symbols if s.id in set(n.symbols_used)][:25]
    if syms:
        out.append("## 相关记号")
        for s_ in syms:
            out.append(f"- {s_.latex}：{s_.meaning or (s_.macro_expansion or '')}")
    return "\n".join(out)


async def _review_derivation(ctx: AnalysisContext, client: LLMClient, sid: str, paper: str) -> list[Risk]:
    system = _system(ctx, client)
    node = ctx.nodes[sid]
    dc = derivation_context(ctx, sid)
    author_ctx = P.DERIVATION_AUTHOR_ROLE + "\n\n" + dc
    reviewer_ctx = P.DERIVATION_REVIEWER_ROLE + "\n\n" + dc
    a1 = await client.json("author", system, [paper, author_ctx, P.DERIVATION_AUTHOR_TASK], P.STEPS_SCHEMA,
                           "derivation_steps", max_tokens=8000)
    steps_txt = json.dumps(a1, ensure_ascii=False, indent=1)
    r1 = await client.json("reviewer", system,
                           [paper, reviewer_ctx, "## 作者对推导步骤的说明\n" + steps_txt, P.DERIVATION_REVIEWER_TASK],
                           P.DERIVATION_CHALLENGES_SCHEMA, "derivation_challenges", max_tokens=8000)
    challenges = [c for c in r1.get("challenges", []) if c.get("concern")]
    node.review = {"status": "passed", "kind": "derivation", "steps": len(a1.get("steps", [])),
                   "challenges": len(challenges), "unresolved": 0}
    if not challenges:
        return []
    ch_txt = json.dumps({"challenges": challenges}, ensure_ascii=False, indent=1)
    a2 = await client.json("author", system,
                           [paper, author_ctx, "## 你之前给出的步骤说明\n" + steps_txt, "## 审稿人的质疑\n" + ch_txt,
                            P.DERIVATION_REBUTTAL_TASK], P.REBUTTAL_SCHEMA, "derivation_rebuttal", max_tokens=8000)
    resp_txt = json.dumps(a2, ensure_ascii=False, indent=1)
    r2 = await client.json("reviewer", system,
                           [paper, reviewer_ctx, "## 作者对推导步骤的说明\n" + steps_txt, "## 你提出的质疑\n" + ch_txt,
                            "## 作者的回应\n" + resp_txt, P.DERIVATION_VERDICT_TASK], P.VERDICT_SCHEMA,
                           "derivation_verdict", max_tokens=8000)
    a, z = ctx.gb.unit_range[sid]
    risks = _debate_risks(ctx, sid, a1, challenges, a2, r2, [(a, z), (ctx.paper.body_start, ctx.paper.body_end)],
                          step_label="作者 · 推导依据")
    substantive = [r for r in risks if r.status != "minor"]
    node.review.update({"status": "issues" if substantive else "passed", "unresolved": len(substantive),
                        "minor": len(risks) - len(substantive)})
    return risks


async def stage_derivation_review(ctx: AnalysisContext, client: LLMClient, max_targets: int = MAX_DERIVATIONS,
                                  progress: Progress = _noop) -> dict:
    targets = select_derivation_targets(ctx, max_targets)
    paper = paper_prefix(ctx)
    done = budget_skipped = 0
    out: list[Risk] = []

    async def run(sid):
        nonlocal done, budget_skipped
        try:
            rs = await _review_derivation(ctx, client, sid, paper)
        except BudgetExceeded as e:
            ctx.nodes[sid].review = {"status": "skipped", "kind": "derivation", "reason": "budget", "error": str(e)}
            budget_skipped += 1
            rs = []
        except LLMError as e:
            log.warning("推导审查失败 %s：%s", sid, e)
            ctx.nodes[sid].review = {"status": "error", "kind": "derivation", "error": str(e)[:200]}
            rs = []
        done += 1
        progress("derivation_review", done, len(targets), f"推导审查 {done}/{len(targets)}：{ctx.node_name(sid)}")
        return rs

    for rs in await asyncio.gather(*(run(t) for t in targets)):
        out.extend(rs)
    for i, r in enumerate(out):
        r.id = f"D{i + 1}"
    ctx.risks.extend(out)
    return {"targets": len(targets) - budget_skipped, "budget_skipped": budget_skipped,
            "risks": sum(1 for r in out if r.status != "minor"), "minor": sum(1 for r in out if r.status == "minor")}


# ---------------------------------------------------------------------------
# Post-processing: external-knowledge consolidation and meta-review
# ---------------------------------------------------------------------------

async def stage_external_merge(ctx: AnalysisContext, client: LLMClient, progress: Progress = _noop) -> dict:
    ext = [n for n in ctx.nodes.values() if n.id.startswith("ext-k-")]
    if len(ext) < 3:
        return {"groups": 0, "merged": 0, "dropped": 0}
    lines = [f"- id={n.id} 名称：{n.title}｜说明：{(n.statement or '')[:160]}" for n in ext]
    data = await client.json("extract", P.external_merge_system(client.config.language), ["\n".join(lines)],
                             P.EXTERNAL_MERGE_SCHEMA, "external_merge", max_tokens=6000)
    gb = ctx.gb
    valid = {n.id for n in ext}
    seen: set[str] = set()
    merged = dropped = 0
    for g in data.get("groups", []):
        members = [m for m in g.get("member_ids", []) if m in valid and m not in seen]
        if not members:
            continue
        seen.update(members)
        head = members[0]
        if not g.get("keep", True):
            for m in members:
                for key in [k for k in gb.edges if k[0] == m]:
                    del gb.edges[key]
                ctx.nodes.pop(m, None)
                dropped += 1
            continue
        if g.get("canonical_name"):
            ctx.nodes[head].title = g["canonical_name"].strip()
        for m in members[1:]:
            for key in [k for k in gb.edges if k[0] == m]:
                e = gb.edges.pop(key)
                for ev in e.evidence:
                    gb.add_edge(head, e.target, e.kind, e.context, e.via, ev, confidence=e.confidence,
                                reason=f"用到了外部知识：{ctx.nodes[head].title}")
            ctx.nodes.pop(m, None)
            merged += 1
    ctx.edges[:] = list(gb.edges.values())
    return {"groups": len(data.get("groups", [])), "merged": merged, "dropped": dropped}


async def stage_meta_review(ctx: AnalysisContext, client: LLMClient, progress: Progress = _noop) -> dict:
    risks = [r for r in ctx.risks if r.source == "review"]
    if not risks:
        return {"reviewed": 0}
    by_id = {r.id: r for r in risks}
    involved = []
    for r in risks:
        if r.node_ids and r.node_ids[0] in ctx.nodes and r.node_ids[0] not in involved:
            involved.append(r.node_ids[0])
    stmts = "\n".join(f"- {node_header(ctx, nid)}：{_clip(render(ctx, ctx.nodes[nid].statement), 700)}" for nid in involved)
    items = []
    for r in risks:
        items.append(
            f"### {r.id}（{ctx.node_name(r.node_ids[0]) if r.node_ids and r.node_ids[0] in ctx.nodes else '—'}；"
            f"类别 {r.category}；错误类型 {r.error_type or '未标注'}；审稿人定级 {r.severity}；"
            f"{ {'conceded': '作者承认', 'minor': '双方认为次要', 'unresolved': '作者未接受'}.get(r.status, r.status) }）\n"
            f"标题：{r.title}\n质疑：{_clip(r.description or '', 900)}\n审稿人最终意见：{_clip(r.reviewer_position or '', 500)}\n"
            f"作者回应：{_clip(r.author_position or '', 400)}")
    data = await client.json("reviewer", _system(ctx, client),
                             [paper_prefix(ctx), P.EDITOR_ROLE + f"\n\n## 涉及条目的陈述\n{stmts}",
                              "## 未被化解的质疑\n\n" + "\n\n".join(items)],
                             P.META_SCHEMA, "meta_review", max_tokens=12000, budget_reserve=0.0)
    decided = 0
    merged = 0
    changed = 0
    for d in data.get("decisions", []):
        r = by_id.get(d.get("risk_id"))
        if r is None:
            continue
        decided += 1
        if d.get("action") == "merge" and d.get("merge_into") in by_id and d["merge_into"] != r.id:
            target = by_id[d["merge_into"]]
            target.merged_ids.append(r.id)
            for nid in r.node_ids:
                if nid not in target.node_ids:
                    target.node_ids.append(nid)
            if r in ctx.risks:
                ctx.risks.remove(r)
            merged += 1
            continue
        sev = d.get("severity")
        if sev in ("high", "medium", "low", "info"):
            sev = cap_severity(sev, r.error_type)
        if sev in ("high", "medium", "low", "info") and sev != r.severity:
            r.original_severity = r.severity
            r.severity = sev
            changed += 1
        r.editor_note = _clean(d.get("rationale"))
    for r in risks:
        if r.merged_ids and r in ctx.risks:
            extra = "；".join(by_id[m].title for m in r.merged_ids if m in by_id)
            r.editor_note = ((r.editor_note or "") + f"（已合并 {len(r.merged_ids)} 条相近的质疑：{extra}）").strip()
    return {"reviewed": decided, "merged": merged, "regraded": changed}


# ---------------------------------------------------------------------------
# Reading guide for students (summary, mind map, per-section notes, concepts, prerequisites, questions)
# ---------------------------------------------------------------------------

async def stage_guide(ctx: AnalysisContext, client: LLMClient, progress: Progress = _noop) -> dict:
    lines = []
    for sec in ctx.gb.sections:
        if sec.level > 3:
            continue
        num = (f"附录 {sec.number}" if sec.appendix and sec.level <= 1 else sec.number) if sec.number else ""
        where = f"（{sec.loc.file}:L{sec.loc.line_start}）" if sec.loc else ""
        lines.append(f"- {sec.id} · {'  ' * max(0, sec.level - 1)}{num} {sec.title}{where}".rstrip())
    task = P.GUIDE_TASK + "\n\n## 章节目录\n" + ("\n".join(lines) or "（无）")
    claims = [n for n in ctx.nodes.values() if n.kind == "assertion"]
    if claims:
        task += "\n\n## 核心论断（论断审查的对象）\n" + "\n".join(
            f"- {n.id}：{n.title or ''}｜{_clip(render(ctx, n.statement), 200)}" for n in sorted(claims, key=lambda n: n.order))
    risks = [r for r in ctx.risks if r.severity in ("high", "medium")][:30]
    if risks:
        task += "\n\n## 体检发现的风险点（高 / 中）\n" + "\n".join(
            f"- {r.id}（{ {'high': '高', 'medium': '中'}[r.severity] }，{', '.join(r.node_ids[:3])}）{r.title}" for r in risks)
    data = await client.json("extract", _system(ctx, client), [paper_prefix(ctx), task], P.GUIDE_SCHEMA, "guide",
                             max_tokens=16000, budget_reserve=0.0)
    valid = set(ctx.nodes) | {s.id for s in ctx.gb.sections} | {r.id for r in ctx.risks} | \
        {f.id for f in (ctx.extra.get("figures") or [])} | {f.id for f in ctx.paper.floats}

    def ids(xs) -> list[str]:
        return list(dict.fromkeys(x for x in (xs or []) if isinstance(x, str) and x in valid))[:8]

    mm, keys = [], set()
    for it in (data.get("mindmap") or [])[:60]:
        key, label = str(it.get("key") or "").strip(), _clean(it.get("label") or "")
        if not key or not label or key in keys:
            continue
        keys.add(key)
        mm.append({"key": key, "parent": it.get("parent"), "label": label[:40], "note": _clean(it.get("note") or ""),
                   "ids": ids(it.get("ids"))})
    for it in mm:      # dangling parents become first-level branches
        if it["parent"] not in keys:
            it["parent"] = None
    sec_ids = {s.id for s in ctx.gb.sections}
    guide = {
        "summary": _clean(data.get("summary") or ""),
        "audience": _clean(data.get("audience") or ""),
        "organization": _clean(data.get("organization") or ""),
        "mindmap": mm,
        "sections": [{"id": x["id"], "summary": _clean(x.get("summary") or ""),
                      "key_points": [_clean(k) for k in (x.get("key_points") or []) if k][:6]}
                     for x in (data.get("sections") or []) if x.get("id") in sec_ids],
        "concepts": [{"term": _clean(x.get("term") or ""), "explanation": _clean(x.get("explanation") or ""),
                      "ids": ids(x.get("ids"))} for x in (data.get("concepts") or []) if x.get("term")][:20],
        "prerequisites": [{"name": _clean(x.get("name") or ""), "why": _clean(x.get("why") or ""),
                           "level": x.get("level") if x.get("level") in ("basic", "intermediate", "advanced") else "intermediate"}
                          for x in (data.get("prerequisites") or []) if x.get("name")][:10],
        "questions": [{"question": _clean(x.get("question") or ""), "hint": _clean(x.get("hint") or ""),
                       "ids": ids(x.get("ids"))} for x in (data.get("questions") or []) if x.get("question")][:10],
        "model": client.config.models.get("extract"),
    }
    ctx.extra["guide"] = guide
    return {"nodes": len(mm), "sections": len(guide["sections"]), "concepts": len(guide["concepts"])}
