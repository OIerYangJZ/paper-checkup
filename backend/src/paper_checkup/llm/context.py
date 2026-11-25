"""Turning parsed LaTeX into compact, reference-resolved text for prompts, and mapping quotes back."""

from __future__ import annotations

import re
from bisect import bisect_right

from ..deterministic import AnalysisContext
from ..latex.structure import CITE_COMMANDS, REF_COMMANDS
from ..latex.scan import read_group, read_optional
from ..models import Loc

_REF_RE = re.compile(r"\\(" + "|".join(sorted(REF_COMMANDS, key=len, reverse=True)) + r")\*?\s*\{([^{}]*)\}")
_CITE_RE = re.compile(r"\\(" + "|".join(sorted(CITE_COMMANDS, key=len, reverse=True)) + r")\*?\s*((?:\[[^\]]*\]\s*){0,2})\{([^{}]*)\}")


def render(ctx: AnalysisContext, s: str) -> str:
    """Resolve \\ref / \\eqref / \\cite to their displayed form (+ node id) so the model sees what readers see."""
    labels = ctx.gb.labels
    ref_macros = ctx.paper.info.ref_macros

    def ref_sub(m):
        cmd, keys = m.group(1), [k.strip() for k in m.group(2).split(",") if k.strip()]
        out = []
        for k in keys:
            li = labels.get(k)
            if li is None:
                out.append("??")
                continue
            disp = li.display
            num = disp.split(" ", 1)[1] if " " in disp and cmd in ("ref", "vref", "subref") else disp
            if li.kind == "equation":
                bare = disp[1:-1] if disp.startswith("(") and disp.endswith(")") else disp
                num = f"({bare})" if cmd == "eqref" else bare
            tag = f"〔{li.node_id}〕" if li.node_id else ""
            out.append(f"{num}{tag}")
        return ", ".join(out)

    for name in ref_macros:
        md = ctx.paper.info.macros.get(name)
        if md is not None:
            body = md.body
            s = re.sub(re.escape(name) + r"(?![A-Za-z])\s*\{([^{}]*)\}", lambda m: body.replace("#1", m.group(1)), s)
    s = _REF_RE.sub(ref_sub, s)

    def cite_sub(m):
        notes = re.findall(r"\[([^\]]*)\]", m.group(2) or "")
        keys = [k.strip() for k in m.group(3).split(",") if k.strip()]
        names = []
        for k in keys:
            c = ctx.gb.citations.get(k)
            lab = (c.label if c and c.label else (str(c.number) if c and c.number else k))
            names.append(lab)
        note = notes[-1] if notes and notes[-1].strip() else ""
        return "[" + ", ".join(names) + (", " + note if note else "") + "]"

    s = _CITE_RE.sub(cite_sub, s)
    s = re.sub(r"\\label\s*\{[^{}]*\}", "", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n\s*\n+", "\n\n", s)
    return s.strip()


def node_header(ctx: AnalysisContext, nid: str) -> str:
    n = ctx.nodes[nid]
    name = ctx.node_name(nid)
    t = f"（{render(ctx, n.title)}）" if n.title and n.kind not in ("external",) else ""
    return f"[{nid}] {name}{t}"


def digest(ctx: AnalysisContext, max_statement: int = 420) -> str:
    """Catalog of all numbered items: shared, cacheable prefix for every prompt."""
    lines = [f"论文：{ctx.meta.title}", f"作者：{', '.join(ctx.meta.authors)}", "", "## 条目目录（id、编号、所在章节、陈述摘录）"]
    secs = {s.id: s for s in ctx.gb.sections}
    for n in sorted(ctx.nodes.values(), key=lambda n: (n.kind == "external", n.kind == "equation", n.order)):
        if n.kind == "external":
            continue
        sec = secs.get(n.section_id)
        sec_s = f"§{sec.number} {sec.title[:40]}" if sec and sec.number else (sec.title[:40] if sec else "")
        st = render(ctx, n.statement)
        st = re.sub(r"\s+", " ", st)
        if len(st) > max_statement:
            st = st[:max_statement] + " …"
        proof_flag = "有证明" if n.proofs else ("无证明" if n.kind in ("theorem", "lemma", "proposition", "corollary", "claim") else "")
        lines.append(f"- {node_header(ctx, n.id)} | {sec_s} | {proof_flag} | {st}")
    ext = [n for n in ctx.nodes.values() if n.kind == "external"]
    if ext:
        lines.append("")
        lines.append("## 被证明/陈述引用的文献")
        for n in ext:
            lines.append(f"- [{n.id}] [{n.title}] {(n.statement or '')[:200]}")
    return "\n".join(lines)


def numbered(ctx: AnalysisContext, start: int, end: int, collapse_nested: bool = True, max_chars: int = 40000) -> str:
    """Rendered source of [start, end) with source line numbers 'L<n>|' for precise quoting."""
    text = ctx.flat.text
    seg_start, seg_end = start, end
    pieces: list[tuple[int, int, str | None]] = []   # (start, end, replacement)
    if collapse_nested:
        for b in ctx.paper.blocks:
            if seg_start < b.env.begin and b.env.end <= seg_end:
                # Keep the nested statement but replace its proofs with a placeholder.
                for pi in b.proofs:
                    pp = ctx.paper.proofs[pi]
                    if seg_start <= pp.outer_start and pp.outer_end <= seg_end and pp.source != "section":
                        pieces.append((pp.outer_start, pp.outer_end,
                                       f"[{b.id} 的证明从略（单独审查）]"))
    pieces.sort()
    out_lines = []
    pos = seg_start
    chunks: list[tuple[int, str]] = []
    for a, b, rep in pieces:
        if a < pos:
            continue
        chunks.append((pos, text[pos:a]))
        chunks.append((a, rep or ""))
        pos = b
    chunks.append((pos, text[pos:seg_end]))
    total = 0
    for off, chunk in chunks:
        if not chunk:
            continue
        lines = chunk.split("\n")
        cur = off
        for ln in lines:
            file, lineno = ctx.flat.loc(cur)
            cur += len(ln) + 1
            if not ln.strip():
                continue
            r = render(ctx, ln)
            out_lines.append(f"L{lineno}| {r}")
            total += len(r)
            if total > max_chars:
                out_lines.append("…（内容过长，已截断）")
                return "\n".join(out_lines)
    return "\n".join(out_lines)


_PICTURE_ENVS = {"tikzpicture", "pgfpicture", "picture", "figure", "figure*", "tikzcd", "wrapfigure"}


def full_paper(ctx: AnalysisContext, max_chars: int = 900_000) -> str:
    """The whole paper body as one shared, cacheable prompt prefix.

    Line prefixes `L<n>|` are source line numbers (a `### 文件 …` header marks file changes),
    【id …】 markers delimit numbered items and their proofs, references are resolved to what readers see.
    Picture environments are elided (their coordinates are noise), algorithms and tables are kept.
    """
    flat = ctx.flat
    text = flat.text
    pb, pe = ctx.paper.body_start, ctx.paper.body_end
    skip = sorted((e.begin, e.end) for e in ctx.paper.envs
                  if e.name in _PICTURE_ENVS and pb <= e.begin < pe)
    opens: dict[int, list[str]] = {}
    closes: dict[int, list[str]] = {}

    for b in ctx.paper.blocks:
        name = ctx.node_name(b.id)
        opens.setdefault(b.env.begin, []).append(f"【{b.id} · {name} 开始】")
        closes.setdefault(max(b.env.begin, b.env.end - 1), []).append(f"【{b.id} 结束】")
        for pi in b.proofs:
            pp = ctx.paper.proofs[pi]
            opens.setdefault(pp.outer_start, []).append(f"【{b.id} 的证明开始】")
            closes.setdefault(max(pp.outer_start, pp.outer_end - 1), []).append(f"【{b.id} 的证明结束】")
    out: list[str] = [f"# 论文全文：{ctx.meta.title}"]
    cur_file = None
    open_at = sorted(opens)
    close_at = sorted(closes)
    oi = ci = 0
    k = 0
    total = 0
    starts = flat.line_starts
    i = max(0, bisect_right(starts, pb) - 1)
    while i < len(starts) and starts[i] < pe:
        ls = starts[i]
        le = starts[i + 1] - 1 if i + 1 < len(starts) else len(text)
        while oi < len(open_at) and open_at[oi] <= le:
            out.extend(opens[open_at[oi]])
            oi += 1
        while k < len(skip) and skip[k][1] <= ls:
            k += 1
        if k < len(skip) and skip[k][0] <= ls < skip[k][1]:
            if not out[-1].startswith("[图"):
                out.append("[图，略]")
            i += 1
            continue
        line = text[ls:le]
        if line.strip():
            file, lineno = flat.line_origin[i]
            if file != cur_file:
                out.append(f"### 文件 {file}")
                cur_file = file
            r = render(ctx, line)
            out.append(f"L{lineno}| {r}")
            total += len(r)
            if total > max_chars:
                out.append("…（全文过长，已截断）")
                break
        while ci < len(close_at) and close_at[ci] <= le:
            out.extend(closes[close_at[ci]])
            ci += 1
        i += 1
    return "\n".join(out)


def _norm_map(s: str) -> tuple[str, list[int]]:
    out = []
    idx = []
    prev_space = False
    for i, c in enumerate(s):
        if c in " \t\r\n~":
            if prev_space:
                continue
            out.append(" ")
            idx.append(i)
            prev_space = True
        else:
            out.append(c)
            idx.append(i)
            prev_space = False
    return "".join(out), idx


def locate_quote(ctx: AnalysisContext, quote: str | None, regions: list[tuple[int, int]],
                 line_hint: int | None = None) -> int | None:
    """Find a model-provided quote inside the given source regions; return an offset or None."""
    if not quote:
        return None
    q = re.sub(r"^L\d+\|\s*", "", quote.strip())
    q = re.sub(r"〔[^〕]*〕", "", q)
    q = q.strip(" .…\"'“”")
    text = ctx.flat.text
    variants = [q]
    if len(q) > 50:
        variants += [q[:50], q[-50:]]
    if len(q) > 24:
        variants.append(q[:24])
    for (a, b) in regions:
        region = text[a:b]
        nreg, idx = _norm_map(region)
        nreg_l = nreg.lower()
        for v in variants:
            nv, _ = _norm_map(v)
            nv = nv.strip().lower()
            if len(nv) < 6:
                continue
            k = nreg_l.find(nv)
            if k >= 0:
                return a + idx[k]
    if line_hint is not None:
        # fall back to the line number the model gave, if it lies inside a region
        for (a, b) in regions:
            fa, la = ctx.flat.loc(a)
            fb, lb = ctx.flat.loc(max(a, b - 1))
            if fa == fb and la <= line_hint <= lb:
                i = 0
                starts = ctx.flat.line_starts
                k = bisect_right(starts, a) - 1
                while k < len(starts) and starts[k] < b:
                    if ctx.flat.line_origin[k] == (fa, line_hint):
                        return starts[k]
                    k += 1
    return None


def loc_at(ctx: AnalysisContext, off: int, length: int = 1) -> Loc:
    return Loc(**ctx.flat.span(off, off + max(1, length)))


def node_regions(ctx: AnalysisContext, nid: str, include_lead: bool = True) -> list[tuple[int, int]]:
    b = next((b for b in ctx.paper.blocks if b.id == nid), None)
    if b is None:
        return []
    regs = [(b.env.begin, b.env.end)]
    for pi in b.proofs:
        pp = ctx.paper.proofs[pi]
        regs.append((pp.outer_start, pp.outer_end))
    if include_lead and b.lead_in:
        regs.append(b.lead_in)
    return regs
