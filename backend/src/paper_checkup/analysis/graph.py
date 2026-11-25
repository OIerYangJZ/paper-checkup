"""Dependency graph construction (deterministic part) and graph algorithms."""

from __future__ import annotations

import re
from collections import defaultdict

from ..latex.preamble import PROVABLE_KINDS
from ..latex.scan import strip_latex_to_text
from ..latex.structure import Block, ParsedPaper
from ..models import Citation, Edge, Evidence, LabelInfo, Loc, Mention, Node, ProofInfo, SectionInfo

STRUCTURAL_VIAS = {"contained", "nested"}


def loc_of(paper: ParsedPaper, start: int, end: int) -> Loc:
    return Loc(**paper.flat.span(start, end))


def sentence_around(text: str, start: int, end: int, limit: int = 260) -> str:
    """The sentence containing [start, end) (LaTeX source, whitespace-normalized)."""
    lo = max(0, start - limit)
    hi = min(len(text), end + limit)
    left = text[lo:start]
    right = text[end:hi]
    # Never cross an environment boundary.
    envs_left = list(re.finditer(r"\\(?:begin|end)\s*\{[^{}]*\}(?:\s*\[[^\]]*\])?(?:\s*\\label\s*\{[^{}]*\})?", left))
    if envs_left:
        left = left[envs_left[-1].end():]
    env_right = re.search(r"\\(?:begin|end)\s*\{[^{}]*\}", right)
    if env_right:
        right = right[:env_right.start()]
    # ... nor a display-math boundary.
    dl = list(re.finditer(r"\\\]|\$\$", left))
    if dl:
        left = left[dl[-1].end():]
    dr = re.search(r"\\\[|\$\$", right)
    if dr:
        right = right[:dr.start()]
    lm = list(re.finditer(r"(?<=[.!?])\s+(?=[A-Z\\$])|\n\s*\n", left))
    if lm:
        left = left[lm[-1].end():]
    rm = re.search(r"(?<=[.!?])\s|\n\s*\n", right)
    if rm:
        right = right[:rm.start() + 1]
    s = left + text[start:end] + right
    # Keep inline math balanced: drop a dangling "$..." tail.
    dollars = [m.start() for m in re.finditer(r"(?<!\\)\$", s)]
    if len(dollars) % 2 == 1:
        cut = dollars[-1]
        if cut > len(left) + (end - start):
            s = s[:cut]
        else:
            s = s[dollars[0] + 1:] if dollars[0] < len(left) else s
    return re.sub(r"\s+", " ", s).strip()


class GraphBuilder:
    def __init__(self, paper: ParsedPaper, bibliography: dict[str, dict]):
        self.p = paper
        self.bib = bibliography
        self.text = paper.text
        self.nodes: dict[str, Node] = {}
        self.edges: dict[tuple[str, str], Edge] = {}
        self.block_ids = {b.index: b.id for b in paper.blocks}
        self.undefined_refs: list[tuple[str, int, str | None]] = []   # (label, offset, node id)
        self.citations: dict[str, Citation] = {}

    # ------------------------------------------------------------------
    def build(self):
        self._sections()
        self._block_nodes()
        self._labels()
        self._ref_edges()
        self._cite_edges()
        self._containment_edges()
        self._text_number_edges()
        return self

    def _sections(self):
        self.sections = []
        for s in self.p.sections:
            self.sections.append(SectionInfo(
                id=s.id, number=s.number, title=strip_latex_to_text(s.title), level=s.level, parent=s.parent,
                loc=loc_of(self.p, s.offset, s.heading_end), appendix=s.appendix))

    def _block_nodes(self):
        p = self.p
        for order, b in enumerate(p.blocks):
            stmt = self.text[b.body_start:b.body_end].strip("\n")
            proofs = []
            for pi in b.proofs:
                pp = p.proofs[pi]
                proofs.append(ProofInfo(loc=loc_of(p, pp.outer_start, pp.outer_end), text=self.text[pp.start:pp.end].strip("\n"),
                                        source=pp.source, how=pp.how, title=pp.title))
            node = Node(
                id=b.id, kind=b.kind, env=b.env.name, display=b.display, number=b.number, title=b.title,
                labels=list(b.labels), statement=stmt, proofs=proofs,
                loc=loc_of(p, b.env.begin, b.env.end), section_id=b.section_id, order=(order + 1) * 10,
                lead_in=(self.text[b.lead_in[0]:b.lead_in[1]].strip() if b.lead_in else None),
                statement_chars=len(stmt), proof_chars=sum(len(x.text) for x in proofs),
            )
            if b.kind in PROVABLE_KINDS and not b.proofs:
                node.flags.append("no_proof")
            self.nodes[b.id] = node

    def _labels(self):
        self.labels: dict[str, LabelInfo] = {}
        for lab, t in self.p.labels.items():
            if t.kind == "block":
                self.labels[lab] = LabelInfo(kind="node", node_id=self.block_ids[t.index], display=t.display)
            elif t.kind == "equation":
                eq = self.p.equations[t.index]
                nid = self.block_ids[eq.owner_block] if eq.owner_block is not None else eq.id
                self.labels[lab] = LabelInfo(kind="equation", node_id=nid, display=t.display)
            elif t.kind == "section":
                self.labels[lab] = LabelInfo(kind="section", section_id=t.index, display=t.display)
            elif t.kind == "item":
                self.labels[lab] = LabelInfo(kind="item", node_id=self.block_ids.get(t.block) if t.block is not None else None,
                                             display="item")
            else:
                self.labels[lab] = LabelInfo(kind=t.kind, display=t.display)

    def _ensure_equation_node(self, eq_index: int) -> str:
        eq = self.p.equations[eq_index]
        if eq.id not in self.nodes:
            latex = self.text[eq.start:eq.end]
            latex = re.sub(r"\\label\s*\{[^{}]*\}", "", latex).strip()
            self.nodes[eq.id] = Node(
                id=eq.id, kind="equation", env=eq.env.name, display="Equation", number=eq.number,
                labels=list(eq.labels), statement=latex, loc=loc_of(self.p, eq.start, eq.end),
                section_id=eq.section_id, order=sum(1 for b in self.p.blocks if b.env.begin < eq.start) * 10 + 5,
                statement_chars=len(latex))
        return eq.id

    def add_edge(self, source: str, target: str, kind: str, context: str, via: str, ev: Evidence,
                 confidence: float | None = None, reason: str | None = None) -> Edge | None:
        if source == target:
            return None
        key = (source, target)
        e = self.edges.get(key)
        rank = {"explicit": 3, "inferred": 2, "symbol": 1}
        if e is None:
            e = Edge(id=f"e{len(self.edges) + 1}", source=source, target=target, kind=kind, context=context,
                     via=via, evidence=[ev], confidence=confidence, reason=reason)
            self.edges[key] = e
            return e
        if rank[kind] > rank[e.kind]:
            e.kind = kind
            e.via = via
            e.confidence = confidence
            e.reason = reason or e.reason
        if context == "statement":
            e.context = "statement"
        if len(e.evidence) < 6 and not any(x.text == ev.text for x in e.evidence):
            e.evidence.append(ev)
        return e

    def resolve_label(self, label: str) -> tuple[str | None, str]:
        """label -> (node id or None, kind of target)."""
        t = self.p.labels.get(label)
        if t is None:
            return None, "undefined"
        if t.kind == "block":
            return self.block_ids[t.index], "block"
        if t.kind == "equation":
            eq = self.p.equations[t.index]
            if eq.owner_block is not None:
                return self.block_ids[eq.owner_block], "equation"
            return self._ensure_equation_node(t.index), "equation"
        if t.kind == "item" and t.block is not None:
            return self.block_ids[t.block], "item"
        return None, t.kind

    def _ref_edges(self):
        p = self.p
        for r in p.refs:
            if r.context in ("heading", "caption"):
                for k in r.keys:
                    if k not in p.labels:
                        self.undefined_refs.append((k, r.offset, None))
                continue
            dependent = self.block_ids.get(r.block) if r.block is not None else None
            for k in r.keys:
                target, tkind = self.resolve_label(k)
                if tkind == "undefined":
                    self.undefined_refs.append((k, r.offset, dependent))
                    continue
                if target is None:
                    continue
                if dependent is None or r.context == "prose":
                    # A mention in running text: recorded on the target node.
                    node = self.nodes.get(target)
                    if node is not None and len(node.mentions) < 12:
                        node.mentions.append(Mention(loc=loc_of(p, r.offset, r.end),
                                                     text=sentence_around(self.text, r.offset, r.end),
                                                     section_id=r.section_id))
                    continue
                ctx = "statement" if r.context in ("statement", "title") else "proof"
                ev = Evidence(loc=loc_of(p, r.offset, r.end), text=sentence_around(self.text, r.offset, r.end),
                              via="eqref" if tkind == "equation" else "ref", context=ctx)
                self.add_edge(target, dependent, "explicit", ctx, ev.via, ev)

    def _cite_edges(self):
        p = self.p
        for key, entry in self.bib.items():
            self.citations[key] = Citation(key=key, label=entry.get("label"), number=entry.get("number"),
                                           text=entry.get("text"), title=entry.get("title"),
                                           authors=entry.get("authors"), year=entry.get("year"))
        for c in p.cites:
            if c.cmd == "nocite":
                continue
            dependent = self.block_ids.get(c.block) if c.block is not None else None
            for key in c.keys:
                if key not in self.citations:
                    self.citations[key] = Citation(key=key)
                if dependent is None or c.context == "prose":
                    continue
                if dependent not in self.citations[key].used_in:
                    self.citations[key].used_in.append(dependent)
                node = self.nodes[dependent]
                if c.context in ("statement", "title"):
                    if key not in node.cites:
                        node.cites.append(key)
                    if "from_literature" not in node.flags and c.context == "title":
                        node.flags.append("from_literature")
                ext_id = "ext-" + re.sub(r"[^A-Za-z0-9_.-]+", "_", key)
                if ext_id not in self.nodes:
                    cit = self.citations[key]
                    self.nodes[ext_id] = Node(
                        id=ext_id, kind="external", display="文献", number=None,
                        title=cit.label or key, statement=cit.title or cit.text or key, order=-1,
                        external={"source": "citation", "cite_key": key, "description": cit.text})
                ctx = "statement" if c.context in ("statement", "title") else "proof"
                note = f"[{c.note}]" if c.note else ""
                ev = Evidence(loc=loc_of(p, c.offset, c.end),
                              text=(note + " " + sentence_around(self.text, c.offset, c.end)).strip(),
                              via="cite", context=ctx)
                self.add_edge(ext_id, dependent, "explicit", ctx, "cite", ev)

    def _containment_edges(self):
        """A block stated inside the proof of X is a step of X's proof."""
        p = self.p
        for b in p.blocks:
            best = None
            for pp in p.proofs:
                if pp.owner is None or pp.owner == b.index:
                    continue
                if pp.start <= b.env.begin < pp.end:
                    if best is None or pp.start > best.start:
                        best = pp
            if best is None:
                continue
            owner_id = self.block_ids[best.owner]
            via = "contained" if best.source == "section" else "nested"
            ev = Evidence(loc=loc_of(p, b.env.begin, b.env.end),
                          text=f"{b.display_name()} 位于 {p.blocks[best.owner].display_name()} 的证明之中",
                          via=via, context="proof")
            self.add_edge(b.id, owner_id, "explicit", "proof", via, ev)
            self.nodes[b.id].parent = owner_id

    _TEXTNUM_RE = re.compile(
        r"\b(Theorem|Lemma|Proposition|Corollary|Definition|Claim|Remark|Conjecture|Fact|Hypothesis|Thm\.|Lem\.|Prop\.|Cor\.|Def\.)"
        r"(s?)\s*~?\s*((?:[A-Z]?\d+(?:\.\d+)*)(?:\s*(?:,|and|&|or|--|–)\s*(?:[A-Z]?\d+(?:\.\d+)*))*)")

    def _text_number_edges(self):
        """Hard-coded references such as 'by Lemma 3.2' written without \\ref."""
        p = self.p
        word_to_kinds = {"theorem": "theorem", "thm.": "theorem", "lemma": "lemma", "lem.": "lemma",
                         "proposition": "proposition", "prop.": "proposition", "corollary": "corollary",
                         "cor.": "corollary", "definition": "definition", "def.": "definition", "claim": "claim",
                         "remark": "remark", "conjecture": "conjecture", "fact": "fact", "hypothesis": "assumption"}
        by_kind_num: dict[tuple[str, str], list[Block]] = defaultdict(list)
        by_display_num: dict[tuple[str, str], list[Block]] = defaultdict(list)
        for b in p.blocks:
            if b.number:
                by_kind_num[(b.kind, b.number)].append(b)
                by_display_num[(b.display.lower(), b.number)].append(b)

        def scan(region_start: int, region_end: int, dependent: Block, ctx: str):
            seg = self.text[region_start:region_end]
            # Blank out \ref{...}/\cite{...} constructs and math so only literal numbers remain.
            masked = re.sub(r"\\[A-Za-z]*ref\*?\s*\{[^{}]*\}", lambda m: " " * len(m.group(0)), seg)
            masked = re.sub(r"\$[^$]*\$", lambda m: " " * len(m.group(0)), masked)
            for m in self._TEXTNUM_RE.finditer(masked):
                word = m.group(1).lower()
                after = masked[m.end():m.end() + 25]
                if re.match(r"\s*(?:of|in|from)\s*(?:\\cite|\[|the\s+(?:paper|work|book))", after) or \
                        re.match(r"\s*\\cite", after) or re.match(r"\s*(?:of|in)\s+\[", after):
                    continue
                kind = word_to_kinds.get(word)
                nums = re.findall(r"[A-Z]?\d+(?:\.\d+)*", m.group(3))
                for num in nums:
                    cands = by_display_num.get((word.rstrip("."), num)) or by_kind_num.get((kind, num), [])
                    if len(cands) != 1:
                        continue
                    target = cands[0]
                    if target.index == dependent.index:
                        continue
                    off = region_start + m.start()
                    ev = Evidence(loc=loc_of(p, off, region_start + m.end()),
                                  text=sentence_around(self.text, off, region_start + m.end()),
                                  via="text-number", context=ctx)
                    key = (target.id, dependent.id)
                    if key in self.edges and self.edges[key].kind == "explicit":
                        continue
                    self.add_edge(target.id, dependent.id, "inferred", ctx, "text-number", ev, confidence=0.9,
                                  reason=f"正文直接写出编号“{m.group(0).strip()}”（未使用 \\ref），按本工具重建的编号匹配")

        for b in p.blocks:
            scan(b.body_start, b.body_end, b, "statement")
            for pi in b.proofs:
                pp = p.proofs[pi]
                scan(pp.start, pp.end, b, "proof")


# ---------------------------------------------------------------------------
# Graph algorithms (also mirrored in the frontend for interactive use)
# ---------------------------------------------------------------------------

def strongly_connected_components(node_ids: list[str], edges: list[tuple[str, str]]) -> list[list[str]]:
    adj: dict[str, list[str]] = defaultdict(list)
    for s, t in edges:
        adj[s].append(t)
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    out: list[list[str]] = []
    counter = [0]

    for root in node_ids:
        if root in index:
            continue
        # iterative Tarjan
        work = [(root, 0)]
        while work:
            v, i = work.pop()
            if i == 0:
                index[v] = low[v] = counter[0]
                counter[0] += 1
                stack.append(v)
                on_stack.add(v)
            recurse = False
            nbrs = adj[v]
            while i < len(nbrs):
                w = nbrs[i]
                i += 1
                if w not in index:
                    work.append((v, i))
                    work.append((w, 0))
                    recurse = True
                    break
                elif w in on_stack:
                    low[v] = min(low[v], index[w])
            if recurse:
                continue
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                out.append(comp)
            if work:
                u = work[-1][0]
                low[u] = min(low[u], low[v])
    return out


def reading_path(nodes: dict[str, Node], edges: list[Edge], target: str, mode: str = "understand",
                 include_inferred: bool = True, include_symbol: bool = True) -> list[dict]:
    """Minimal prerequisite set for `target`, in topological (then document) order.

    mode="understand": read the target's statement and proof, and the *statements* of what they use
                       (recursively through statement-level dependencies).
    mode="verify":     recursively read every prerequisite's statement and proof.
    """
    allowed = {"explicit"} | ({"inferred"} if include_inferred else set()) | ({"symbol"} if include_symbol else set())
    preds: dict[str, list[Edge]] = defaultdict(list)
    for e in edges:
        if e.kind in allowed:
            preds[e.target].append(e)
    need: dict[str, str] = {target: "statement+proof"}
    reasons: dict[str, list[str]] = defaultdict(list)
    queue = [target]
    while queue:
        v = queue.pop()
        read_proof = need[v] == "statement+proof"
        for e in preds[v]:
            if not read_proof and e.context == "proof":
                continue
            u = e.source
            want = "statement+proof" if (mode == "verify" and nodes.get(u) and nodes[u].proofs) else "statement"
            reasons[u].append(v)
            if u not in need or (want == "statement+proof" and need[u] == "statement"):
                need[u] = want
                queue.append(u)
    # Topological order restricted to the needed set (Kahn), ties by document order.
    sub = set(need)
    indeg = {v: 0 for v in sub}
    succ: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        if e.kind in allowed and e.source in sub and e.target in sub:
            indeg[e.target] += 1
            succ[e.source].append(e.target)

    def order_key(v):
        n = nodes.get(v)
        return (n.order if n and n.order >= 0 else 10 ** 6, v)

    ready = sorted([v for v in sub if indeg[v] == 0], key=order_key)
    out = []
    while ready:
        v = ready.pop(0)
        out.append(v)
        for w in succ[v]:
            indeg[w] -= 1
            if indeg[w] == 0:
                ready.append(w)
                ready.sort(key=order_key)
    remaining = sorted([v for v in sub if v not in out], key=order_key)  # cycles
    out.extend(remaining)
    return [{"id": v, "read": need[v], "needed_by": sorted(set(reasons.get(v, [])))} for v in out]
