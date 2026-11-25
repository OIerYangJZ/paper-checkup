"""Deterministic structural checks -> risk points (source = "structural")."""

from __future__ import annotations

import re
from collections import defaultdict

from ..latex.preamble import PROVABLE_KINDS
from ..latex.structure import ParsedPaper
from ..models import Edge, Node, Risk, Symbol
from .graph import GraphBuilder, loc_of, sentence_around, strongly_connected_components

GAP_PHRASES = [
    r"clearly", r"obviously", r"evidently", r"trivially", r"it is (?:easy|straightforward|not hard|not difficult|routine) to (?:see|check|verify|show)",
    r"one (?:can|may) (?:easily )?(?:show|check|verify|see)", r"it (?:easily )?follows(?: easily| immediately)?",
    r"(?:is|are) (?:left )?to the reader", r"we leave .{0,30} to the reader", r"by a similar argument",
    r"(?:similarly|analogously)", r"without loss of generality", r"(?:a |the )?standard argument",
    r"well[- ]known", r"it is (?:clear|obvious|evident)", r"straightforward", r"routine (?:calculation|computation|check)",
    r"(?:direct|simple|easy) (?:calculation|computation)", r"we omit the (?:proof|details)",
]
_GAP_RE = re.compile(r"\b(?:" + "|".join(GAP_PHRASES) + r")\b", re.I)


class Checker:
    def __init__(self, paper: ParsedPaper, gb: GraphBuilder, nodes: dict[str, Node], edges: list[Edge],
                 symbols: list[Symbol]):
        self.p = paper
        self.gb = gb
        self.nodes = nodes
        self.edges = edges
        self.symbols = symbols
        self.risks: list[Risk] = []
        self.gap_hints: dict[str, list[dict]] = defaultdict(list)   # node id -> [{phrase, loc, sentence}]

    def add(self, **kw) -> Risk:
        r = Risk(id=f"S{len(self.risks) + 1}", source="structural", **kw)
        self.risks.append(r)
        return r

    def run(self) -> list[Risk]:
        self.undefined_refs()
        self.duplicate_labels()
        self.cycles()
        self.unsupported()
        self.forward_dependencies()
        self.unowned_proofs()
        self.symbol_flags()
        self.gap_phrases()
        return self.risks

    # ------------------------------------------------------------------
    def undefined_refs(self):
        grouped: dict[str, list[tuple[int, str | None]]] = defaultdict(list)
        for lab, off, nid in self.gb.undefined_refs:
            grouped[lab].append((off, nid))
        for lab, occ in grouped.items():
            off, nid = occ[0]
            self.add(category="reference", severity="medium",
                     title=f"引用了不存在的标签 \\ref{{{lab}}}",
                     description=f"源码中有 {len(occ)} 处引用 `{lab}`，但全文没有对应的 \\label。编译后会显示为“??”，"
                                 f"读者无法确定此处依赖的是哪一个结论（也可能是缺失了某个源文件）。",
                     node_ids=[nid] if nid else [], loc=loc_of(self.p, off, off + 1),
                     quote=sentence_around(self.p.text, off, off + 1), status="flagged")

    def duplicate_labels(self):
        for lab, off in self.p.duplicate_labels:
            self.add(category="reference", severity="low", title=f"标签重复定义：{lab}",
                     description="同一个 \\label 被定义了多次，\\ref 只会指向其中一个，可能导致引用指向错误的结论。",
                     loc=loc_of(self.p, off, off + 1), status="flagged")

    def cycles(self):
        ids = [n for n in self.nodes]
        for kinds, sev, label in ((("explicit",), "high", "显式"), (("explicit", "inferred"), "medium", "含推断")):
            pairs = [(e.source, e.target) for e in self.edges if e.kind in kinds and e.via not in ("contained",)]
            for comp in strongly_connected_components(ids, pairs):
                if len(comp) < 2:
                    continue
                if label == "含推断":
                    # Skip cycles already reported with explicit edges only.
                    if any(set(comp) <= set(r.node_ids) for r in self.risks if r.category == "cycle"):
                        continue
                comp_sorted = sorted(comp, key=lambda x: self.nodes[x].order)
                names = "、".join(self._name(x) for x in comp_sorted)
                inner = [e for e in self.edges if e.kind in kinds and e.source in comp and e.target in comp]
                detail = "；".join(f"{self._name(e.target)} 用到 {self._name(e.source)}" for e in inner[:8])
                first = self.nodes[comp_sorted[0]]
                self.add(category="cycle", severity=sev, title=f"循环依赖（{label}依赖）：{names}",
                         description=f"这些结论在依赖图中构成环：{detail}。若环上每一步都确实依赖对方，"
                                     f"则论证是循环的；也可能其中一条只是“参见”式引用。",
                         node_ids=comp_sorted, loc=first.loc, status="flagged")

    def unsupported(self):
        for nid, n in self.nodes.items():
            if n.kind not in PROVABLE_KINDS or n.proofs:
                continue
            if "from_literature" in n.flags or n.cites:
                continue
            # Proved later in the text?  e.g. a theorem in the intro restated & proved later.
            has_restatement = any(
                m for m in self.nodes.values()
                if m.id != nid and m.kind == n.kind and m.proofs and _similar(m.statement, n.statement))
            if has_restatement:
                n.flags.append("restated_later")
                continue
            lead = (n.lead_in or "")
            if re.search(r"\\cite", lead[-300:]):
                n.flags.append("cited_in_lead_in")
                continue
            sev = "medium" if n.kind in ("theorem", "proposition", "lemma") else "low"
            self.add(category="unsupported", severity=sev,
                     title=f"{self._name(nid)} 没有找到证明或文献来源",
                     description="没有找到与该结论对应的证明环境、“Proof of …”章节或证明段落，陈述和其前文也没有引用文献。"
                                 "它可能在正文中被非正式地论证，也可能被默认为已知结论；审稿对抗阶段会进一步检查。",
                     node_ids=[nid], loc=n.loc, status="flagged")

    def forward_dependencies(self):
        """Proof of X uses Y where Y appears after X *and* Y's proof uses X (transitively) -> already a cycle.
        Here we only flag forward references in proofs of results whose own dependency is unproven."""
        for e in self.edges:
            if e.kind != "explicit" or e.via in ("cite", "contained", "nested"):
                continue
            s, t = self.nodes.get(e.source), self.nodes.get(e.target)
            if not s or not t or s.order < 0 or t.order < 0:
                continue
            if s.order > t.order and s.kind in PROVABLE_KINDS and not s.proofs and "from_literature" not in s.flags \
                    and e.context == "proof":
                t.flags.append("uses_later_unproved")

    def unowned_proofs(self):
        for pp in self.p.proofs:
            if pp.owner is not None:
                continue
            if pp.source == "section":
                # A section called "Proof of ..." we could not match.
                pass
            self.add(category="structure", severity="info",
                     title="无法确定归属的证明",
                     description="这个证明环境前后没有明确说明它证明的是哪个结论（标题中没有引用，前一个结论已有证明）。"
                                 "大模型阶段会尝试判断其归属。",
                     loc=loc_of(self.p, pp.outer_start, pp.outer_end),
                     quote=re.sub(r"\s+", " ", self.p.text[pp.start:pp.start + 200]).strip(), status="flagged")

    def symbol_flags(self):
        for s in self.symbols:
            if "used_before_definition" in s.flags and s.definitions and s.first_use:
                d = next((d for d in s.definitions if d.scope in ("definition", "prose")), s.definitions[0])
                self.add(category="symbol", severity="low",
                         title=f"符号 ${s.latex}$ 在定义之前被使用" if s.kind != "term" else f"术语“{s.latex}”在定义之前被使用",
                         description=f"首次出现于 {s.first_use.file}:{s.first_use.line_start}，"
                                     f"而定义位于 {d.loc.file}:{d.loc.line_start}。",
                         node_ids=[x for x in (s.first_use_node, s.defined_in_node) if x],
                         loc=s.first_use, quote=s.first_use_context, status="flagged", symbol_id=s.id)
            if "multiple_definitions" in s.flags:
                locs = "；".join(f"{d.loc.file}:{d.loc.line_start}" for d in s.definitions if d.scope != "macro")
                single_letter = s.kind == "symbol" and len(s.latex) == 1
                self.add(category="symbol", severity="info" if single_letter else "low",
                         title=(f"符号 ${s.latex}$ 被多次定义" if s.kind != "term" else f"术语“{s.latex}”被多次定义"),
                         description=f"在多处出现了定义性表述（{locs}）。需要确认这些定义是否一致；"
                                     f"大模型阶段会判断它们是同一含义的重述还是不同含义。",
                         node_ids=[d.node_id for d in s.definitions if d.node_id][:4],
                         loc=s.definitions[0].loc, status="flagged", symbol_id=s.id)

    def gap_phrases(self):
        for b in self.p.blocks:
            nid = b.id
            for pi in b.proofs:
                pp = self.p.proofs[pi]
                seg = self.p.text[pp.start:pp.end]
                for m in _GAP_RE.finditer(seg):
                    off = pp.start + m.start()
                    if len(self.gap_hints[nid]) >= 8:
                        break
                    self.gap_hints[nid].append({
                        "phrase": m.group(0), "loc": loc_of(self.p, off, off + len(m.group(0))).model_dump(),
                        "sentence": sentence_around(self.p.text, off, off + len(m.group(0)))})

    def _name(self, nid: str) -> str:
        n = self.nodes[nid]
        return f"{n.display} {n.number}" if n.number else (n.title or n.display)


def _similar(a: str, b: str) -> bool:
    def norm(s):
        s = re.sub(r"\\label\s*\{[^{}]*\}", "", s)
        return re.sub(r"\s+", "", s)[:400]
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    # crude overlap ratio on character trigrams
    ta = {na[i:i + 3] for i in range(len(na) - 2)}
    tb = {nb[i:i + 3] for i in range(len(nb) - 2)}
    if not ta or not tb:
        return False
    return len(ta & tb) / min(len(ta), len(tb)) > 0.85
