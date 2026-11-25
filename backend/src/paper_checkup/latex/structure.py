"""Document structure: sections, theorem-like blocks, proofs, equations, labels, refs, cites.

Everything here is deterministic. Numbering follows LaTeX counter semantics
(shared counters, reset-within, \\numberwithin, \\appendix, \\tag, \\nonumber).
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from .preamble import (
    PROVABLE_KINDS,
    PreambleInfo,
    TheoremSpec,
    ensure_default_theorem,
    parse_preamble,
)
from .scan import (
    MATH_ENVS,
    Env,
    MathSegment,
    find_math_segments,
    match_brace,
    read_group,
    read_optional,
    scan_environments,
    split_top_level,
    strip_latex_to_text,
)
from .source import FlatDocument

PROOF_ENVS = {"proof", "proof*", "pf", "pf*", "Proof", "IEEEproof", "proofof", "proofsketch", "proof-sketch",
              "sketch", "proofof*"}
MULTILINE_EQ_ENVS = {"align", "alignat", "flalign", "gather", "eqnarray", "align*", "alignat*", "flalign*",
                     "gather*", "eqnarray*"}
NUMBERED_EQ_ENVS = {"equation", "align", "alignat", "flalign", "gather", "multline", "eqnarray", "dmath"}
FIGURE_ENVS = {"figure", "figure*", "table", "table*", "algorithm", "algorithm*", "wrapfigure", "subfigure",
               "lstlisting", "listing"}
LIST_ENVS = {"enumerate", "itemize", "description", "enumerate*", "compactenum", "inparaenum"}
SECTION_LEVELS = {"part": -1, "chapter": 0, "section": 1, "subsection": 2, "subsubsection": 3, "paragraph": 4}

REF_COMMANDS = ["ref", "eqref", "cref", "Cref", "autoref", "Autoref", "vref", "Vref", "nameref", "labelcref",
                "subref", "thmref", "cpageref", "Cpageref", "crefrange", "Crefrange"]
CITE_COMMANDS = ["cite", "citep", "citet", "citealp", "citealt", "citeauthor", "citeyear", "Cite", "Citep",
                 "Citet", "parencite", "textcite", "autocite", "footcite", "nocite", "citenum", "Parencite",
                 "Textcite", "supercite", "smartcite"]

KIND_PREFIX = {
    "theorem": "thm", "lemma": "lem", "proposition": "prop", "corollary": "cor", "definition": "def",
    "conjecture": "conj", "claim": "claim", "fact": "fact", "assumption": "asm", "notation": "notn",
    "remark": "rem", "example": "ex", "problem": "prob", "algorithm": "alg", "other": "env",
}


@dataclass
class Section:
    id: str
    level: int
    command: str
    starred: bool
    number: str | None
    title: str
    short_title: str | None
    offset: int           # offset of the command
    heading_end: int      # offset after the title argument
    end: int = -1         # offset where the section ends (next section of same or higher level)
    appendix: bool = False
    parent: str | None = None
    labels: list[str] = field(default_factory=list)


@dataclass
class ProofPart:
    start: int        # body start
    end: int          # body end
    outer_start: int
    outer_end: int
    source: str       # env | section | paragraph
    title: str | None
    owner: int | None = None   # block index
    how: str = ""              # how the owner was determined


@dataclass
class Block:
    index: int
    env: Env
    spec: TheoremSpec
    body_start: int
    body_end: int
    title: str | None
    number: str | None = None
    section_id: str | None = None
    labels: list[str] = field(default_factory=list)
    proofs: list[int] = field(default_factory=list)     # indices into ParsedPaper.proofs
    parent_block: int | None = None                      # nesting (e.g. claim inside a proof)
    contained_in_proof_of: int | None = None             # inside a "Proof of X" section
    id: str = ""
    lead_in: tuple[int, int] | None = None

    @property
    def kind(self) -> str:
        return self.spec.kind

    @property
    def display(self) -> str:
        return self.spec.display

    def display_name(self) -> str:
        return f"{self.spec.display} {self.number}" if self.number else self.spec.display


@dataclass
class Equation:
    index: int
    env: Env
    start: int
    end: int
    number: str | None
    labels: list[str]
    owner_block: int | None = None
    owner_context: str | None = None     # statement | proof | None
    section_id: str | None = None
    id: str = ""


@dataclass
class LabelTarget:
    label: str
    kind: str        # block | equation | section | figure | item | other
    index: int | str | None
    offset: int
    display: str
    block: int | None = None   # for item/equation: the enclosing block (if any)


@dataclass
class Occurrence:
    offset: int
    end: int
    cmd: str
    keys: list[str]
    note: str | None
    context: str              # statement | proof | prose | heading | caption | title
    block: int | None
    section_id: str | None


@dataclass
class ParsedPaper:
    flat: FlatDocument
    info: PreambleInfo
    body_start: int
    body_end: int
    envs: list[Env]
    sections: list[Section]
    blocks: list[Block]
    proofs: list[ProofPart]
    equations: list[Equation]
    labels: dict[str, LabelTarget]
    duplicate_labels: list[tuple[str, int]]
    refs: list[Occurrence]
    cites: list[Occurrence]
    math: list[MathSegment]
    abstract: tuple[int, int] | None

    @property
    def text(self) -> str:
        return self.flat.text

    def section_at(self, offset: int) -> Section | None:
        best = None
        for s in self.sections:
            if s.offset <= offset and (s.end < 0 or offset < s.end):
                if best is None or s.level >= best.level:
                    best = s
        return best


# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------

def _roman(n: int) -> str:
    vals = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"),
            (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
    out = ""
    for v, s in vals:
        while n >= v:
            out += s
            n -= v
    return out


def _alph(n: int) -> str:
    if n <= 0:
        return "0"
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


class Counters:
    def __init__(self, info: PreambleInfo, has_chapters: bool):
        self.info = info
        self.val: dict[str, int] = defaultdict(int)
        self.within: dict[str, str] = {}
        if has_chapters:
            self.within["section"] = "chapter"
        self.within["subsection"] = "section"
        self.within["subsubsection"] = "subsection"
        self.within["paragraph"] = "subsubsection"
        for c, p in info.counter_within.items():
            self.within[info.resolve_counter(c)] = info.resolve_counter(p)
        if has_chapters and "equation" not in self.within:
            self.within["equation"] = "chapter"
        self.appendix = False
        self.revtex = info.documentclass.lower().startswith("revtex")
        self.children: dict[str, list[str]] = defaultdict(list)
        for c, p in self.within.items():
            self.children[p].append(c)

    def step(self, c: str):
        c = self.info.resolve_counter(c)
        self.val[c] += 1
        stack = list(self.children.get(c, []))
        seen = set()
        while stack:
            ch = stack.pop()
            if ch in seen:
                continue
            seen.add(ch)
            self.val[ch] = 0
            stack.extend(self.children.get(ch, []))

    def fmt(self, c: str) -> str:
        v = self.val[c]
        top = "chapter" if "chapter" in self.within.values() and self.within.get("section") == "chapter" else "section"
        if self.appendix and c == top:
            return _alph(v)
        if self.revtex and c == "section":
            return _roman(v)
        if self.revtex and c == "subsection":
            return _alph(v)
        return str(v)

    def the(self, c: str) -> str:
        c = self.info.resolve_counter(c)
        parent = self.within.get(c)
        if parent:
            return f"{self.the(parent)}.{self.fmt(c)}"
        return self.fmt(c)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_SECTION_RE = re.compile(r"\\(part|chapter|section|subsection|subsubsection|paragraph)(\*?)(?![A-Za-z])")
_LABEL_RE = re.compile(r"\\label\s*\{([^{}]*)\}")
_APPENDIX_RE = re.compile(r"\\appendix\b|\\begin\s*\{appendix\}|\\begin\s*\{appendices\}")


def _build_ref_re(info: PreambleInfo) -> re.Pattern:
    names = sorted(set(REF_COMMANDS) | {m.lstrip("\\") for m in info.ref_macros}, key=len, reverse=True)
    return re.compile(r"\\(" + "|".join(map(re.escape, names)) + r")\*?(?![A-Za-z])")


def _build_cite_re(info: PreambleInfo) -> re.Pattern:
    names = sorted(set(CITE_COMMANDS) | {m.lstrip("\\") for m in info.cite_macros}, key=len, reverse=True)
    return re.compile(r"\\(" + "|".join(map(re.escape, names)) + r")\*?(?![A-Za-z])")


def _in_env_names(envs: list[Env], offset: int) -> list[Env]:
    """Environments containing offset, innermost last."""
    out = [e for e in envs if e.body_start <= offset < e.body_end]
    out.sort(key=lambda e: e.depth)
    return out


def parse_document(flat: FlatDocument) -> ParsedPaper:
    text = flat.text
    m = re.search(r"\\begin\s*\{document\}", text)
    body_start = m.end() if m else 0
    m2 = re.search(r"\\end\s*\{document\}", text[body_start:])
    body_end = body_start + m2.start() if m2 else len(text)

    info = parse_preamble(text, m.start() if m else 0, flat.extra_preambles)
    envs = scan_environments(text, body_start, body_end)

    # ---- theorem-like blocks -------------------------------------------------
    blocks: list[Block] = []
    for e in envs:
        name = e.name
        if name in ("restatable", "restatable*"):
            i = e.body_start
            title = None
            o = read_optional(text, i)
            if o is not None:
                title = o[0]
                i = o[2]
            g1 = read_group(text, i)
            if g1 is None:
                continue
            g2 = read_group(text, g1[2])
            inner = g1[0].strip()
            spec = info.theorems.get(inner) or ensure_default_theorem(info, inner)
            if spec is None:
                continue
            blocks.append(Block(index=len(blocks), env=e, spec=spec,
                                body_start=(g2[2] if g2 else g1[2]), body_end=e.body_end, title=title))
            continue
        if name in PROOF_ENVS:
            continue
        spec = info.theorems.get(name) or ensure_default_theorem(info, name)
        if spec is None:
            continue
        # Skip an alias env's inner env (e.g. \newenvironment{lemma}{\begin{n-lemma}} is expanded by us).
        title = None
        bstart = e.body_start
        o = read_optional(text, bstart)
        if o is not None and "\n\n" not in text[bstart:o[1]]:
            title = o[0].strip()
            bstart = o[2]
        blocks.append(Block(index=len(blocks), env=e, spec=spec, body_start=bstart, body_end=e.body_end,
                            title=title))
    blocks.sort(key=lambda b: b.env.begin)
    for i, b in enumerate(blocks):
        b.index = i
    env_to_block = {b.env.index: b.index for b in blocks}
    for b in blocks:
        p = b.env.parent
        while p is not None:
            if p in env_to_block:
                b.parent_block = env_to_block[p]
                break
            p = envs[p].parent

    # ---- sections ----------------------------------------------------------
    verbatim_ranges = [(e.begin, e.end) for e in envs if e.name in ("verbatim", "lstlisting", "minted")]
    sections: list[Section] = []
    appendix_offsets = [am.start() for am in _APPENDIX_RE.finditer(text, body_start, body_end)]
    first_appendix = appendix_offsets[0] if appendix_offsets else None
    for sm in _SECTION_RE.finditer(text, body_start, body_end):
        if any(a <= sm.start() < b for a, b in verbatim_ranges):
            continue
        cmd, star = sm.group(1), sm.group(2) == "*"
        i = sm.end()
        short = None
        o = read_optional(text, i)
        if o is not None:
            short = o[0]
            i = o[2]
        g = read_group(text, i)
        if g is None:
            continue
        sections.append(Section(id="", level=SECTION_LEVELS[cmd], command=cmd, starred=star, number=None,
                                title=g[0], short_title=short, offset=sm.start(), heading_end=g[2],
                                appendix=(first_appendix is not None and sm.start() > first_appendix)))
    has_chapters = any(s.command == "chapter" for s in sections)

    # ---- numbering (single ordered pass) -------------------------------------
    counters = Counters(info, has_chapters)
    events: list[tuple[int, int, str, object]] = []
    for s in sections:
        events.append((s.offset, 0, "section", s))
    if first_appendix is not None:
        events.append((first_appendix, -1, "appendix", None))
    for b in blocks:
        events.append((b.env.begin, 1, "block", b))
    eq_envs = [e for e in envs if e.name.rstrip("*") in NUMBERED_EQ_ENVS or e.name in MATH_ENVS]
    for e in eq_envs:
        events.append((e.begin, 2, "eq", e))
    events.sort(key=lambda t: (t[0], t[1]))

    equations: list[Equation] = []
    eq_line_spans: list[tuple[int, int, str | None, Env]] = []  # (start, end, number, env)
    sec_count = 0
    for _, _, kind, obj in events:
        if kind == "appendix":
            counters.appendix = True
            top = "chapter" if has_chapters else "section"
            counters.val[top] = 0
            continue
        if kind == "section":
            s: Section = obj  # type: ignore
            sec_count += 1
            s.id = f"sec-{sec_count}"
            if not s.starred and s.command not in ("paragraph", "part"):
                counters.step(s.command)
                s.number = counters.the(s.command)
            continue
        if kind == "block":
            b: Block = obj  # type: ignore
            if b.spec.counter:
                counters.step(b.spec.counter)
                b.number = counters.the(b.spec.counter)
            continue
        if kind == "eq":
            e: Env = obj  # type: ignore
            base = e.name.rstrip("*")
            starred = e.name.endswith("*") or e.name in ("displaymath", "math")
            body = text[e.body_start:e.body_end]
            if base in {n.rstrip("*") for n in MULTILINE_EQ_ENVS}:
                spans = split_top_level(body)
            else:
                spans = [(0, len(body))]
            for (a, bnd) in spans:
                line = body[a:bnd]
                if not line.strip() and len(spans) > 1:
                    continue
                tag = re.search(r"\\tag\*?\s*\{([^{}]*)\}", line)
                no_num = re.search(r"\\(nonumber|notag)\b", line)
                number = None
                if tag:
                    number = tag.group(1)
                elif not starred and not no_num:
                    counters.step("equation")
                    number = counters.the("equation")
                eq_line_spans.append((e.body_start + a, e.body_start + bnd, number, e))

    # Section end offsets and parents.
    for i, s in enumerate(sections):
        s.end = body_end
        for t in sections[i + 1:]:
            if t.level <= s.level:
                s.end = t.offset
                break
        for t in reversed(sections[:i]):
            if t.level < s.level:
                s.parent = t.id
                break

    def section_id_at(offset: int) -> str | None:
        best = None
        for s in sections:
            if s.offset <= offset < s.end and (best is None or s.level >= best.level):
                best = s
        return best.id if best else None

    for b in blocks:
        b.section_id = section_id_at(b.env.begin)

    # ---- proofs --------------------------------------------------------------
    proofs: list[ProofPart] = []
    ref_re = _build_ref_re(info)
    for e in envs:
        if e.name not in PROOF_ENVS:
            continue
        title = None
        bstart = e.body_start
        o = read_optional(text, bstart)
        if o is not None and "\n\n" not in text[bstart:o[1]]:
            title = o[0]
            bstart = o[2]
        proofs.append(ProofPart(start=bstart, end=e.body_end, outer_start=e.begin, outer_end=e.end,
                                source="env", title=title))
    # "Proof of ..." sections.
    for s in sections:
        t = strip_latex_to_text(s.short_title or "", keep_math=False) + " | " + s.title
        if re.match(r"\s*\\?(?:textbf\{|emph\{)?\s*(?:Proofs?|Proving)\b", s.title, re.I) or \
                re.match(r"\s*Proofs?\b", s.short_title or "", re.I):
            proofs.append(ProofPart(start=s.heading_end, end=s.end, outer_start=s.offset, outer_end=s.end,
                                    source="section", title=s.title + (" || " + s.short_title if s.short_title else "")))
    # Paragraph-style proofs (no environment) — only outside existing proofs/blocks.
    para_re = re.compile(
        r"(?:^|\n)[ \t]*(?:\\noindent\s*)?(?:\{\s*)?\\(?:textit|textbf|emph|it|bf|em|textsc|paragraph|underline)\s*\{?\s*"
        r"(Proof(?:\s+of\s+[^.:}\n]{0,80})?)\s*[.:]?\s*\}?")
    occupied = [(p.outer_start, p.outer_end) for p in proofs] + [(b.env.begin, b.env.end) for b in blocks]
    qed_re = re.compile(r"\\qed\b|\\qedsymbol|\\qedhere|\\(?:hfill|hspace\*?\{[^}]*\})\s*\$?\s*\\(?:square|Box|blacksquare|qed)\b|"
                        r"\$\\(?:square|Box|blacksquare)\$|\\endproof\b|\\QED\b")
    next_starts = sorted([b.env.begin for b in blocks] + [s.offset for s in sections] + [body_end])
    for pm in para_re.finditer(text, body_start, body_end):
        st = pm.start(1)
        if any(a <= st < b for a, b in occupied):
            continue
        limit = next((x for x in next_starts if x > st), body_end)
        limit = min(limit, st + 8000)
        qm = qed_re.search(text, pm.end(), limit)
        end = qm.end() if qm else limit
        proofs.append(ProofPart(start=pm.end(), end=end, outer_start=st, outer_end=end, source="paragraph",
                                title=pm.group(1)))
    proofs.sort(key=lambda p: p.outer_start)

    # ---- labels --------------------------------------------------------------
    labels: dict[str, LabelTarget] = {}
    duplicates: list[tuple[str, int]] = []
    block_by_env = env_to_block

    def eq_line_for(offset: int):
        for (a, bnd, num, e) in eq_line_spans:
            if a <= offset <= bnd and e.body_start <= offset < e.body_end:
                return (a, bnd, num, e)
        return None

    proof_env_offsets = {(p.outer_start): p for p in proofs if p.source == "env"}
    label_occ = []
    for lm in _LABEL_RE.finditer(text, body_start, body_end):
        label_occ.append((lm.start(), lm.group(1).strip()))

    eq_index_by_line: dict[tuple[int, int], int] = {}
    for off, lab in label_occ:
        containing = _in_env_names(envs, off)
        target: LabelTarget | None = None
        enclosing_block = None
        for e in reversed(containing):
            if e.index in block_by_env:
                enclosing_block = block_by_env[e.index]
                break
        for e in reversed(containing):
            base = e.name.rstrip("*")
            if e.name in MATH_ENVS or base in NUMBERED_EQ_ENVS:
                ln = eq_line_for(off)
                if ln and ln[2] is not None:
                    key = (ln[0], ln[1])
                    if key not in eq_index_by_line:
                        eq = Equation(index=len(equations), env=ln[3], start=ln[0], end=ln[1], number=ln[2],
                                      labels=[])
                        eq_index_by_line[key] = eq.index
                        equations.append(eq)
                    eq = equations[eq_index_by_line[key]]
                    eq.labels.append(lab)
                    target = LabelTarget(lab, "equation", eq.index, off, f"({ln[2]})", block=enclosing_block)
                    break
                continue  # unnumbered math: label refers to something outside
            if e.name in LIST_ENVS:
                target = LabelTarget(lab, "item", None, off, "item", block=enclosing_block)
                break
            if e.name in FIGURE_ENVS:
                target = LabelTarget(lab, "figure", None, off, e.name.rstrip("*").capitalize())
                break
            if e.index in block_by_env:
                bi = block_by_env[e.index]
                blocks[bi].labels.append(lab)
                target = LabelTarget(lab, "block", bi, off, blocks[bi].display_name(), block=bi)
                break
            if e.name in PROOF_ENVS:
                p = proof_env_offsets.get(e.begin)
                # resolved to the owner later
                target = LabelTarget(lab, "proof", e.begin, off, "proof")
                break
        if target is None:
            sec = None
            for s in sections:
                if s.offset <= off < s.end and (sec is None or s.level >= sec.level):
                    sec = s
            if sec is not None:
                sec.labels.append(lab)
                kind_name = {"chapter": "Chapter", "section": "Section", "subsection": "Section",
                             "subsubsection": "Section", "paragraph": "Paragraph"}.get(sec.command, "Section")
                if sec.appendix and sec.level <= 1:
                    kind_name = "Appendix"
                target = LabelTarget(lab, "section", sec.id, off, f"{kind_name} {sec.number or ''}".strip())
            else:
                target = LabelTarget(lab, "other", None, off, lab)
        if lab in labels:
            duplicates.append((lab, off))
            continue
        labels[lab] = target

    # Resolve proof owners now that labels exist.
    _assign_proof_owners(text, blocks, proofs, labels, ref_re, info)
    for pi, p in enumerate(proofs):
        if p.owner is not None:
            blocks[p.owner].proofs.append(pi)
    for lab, t in labels.items():
        if t.kind == "proof":
            p = proof_env_offsets.get(t.index)  # type: ignore[arg-type]
            if p is not None and p.owner is not None:
                labels[lab] = LabelTarget(lab, "block", p.owner, t.offset, blocks[p.owner].display_name(),
                                          block=p.owner)
            else:
                labels[lab] = LabelTarget(lab, "other", None, t.offset, lab)
    # Blocks living inside a "Proof of X" section belong to X's proof.
    for p in proofs:
        if p.source == "section" and p.owner is not None:
            for b in blocks:
                if p.start <= b.env.begin < p.end and b.index != p.owner and b.parent_block is None:
                    b.contained_in_proof_of = p.owner

    # ---- equation owners -----------------------------------------------------
    def context_at(offset: int) -> tuple[str, int | None]:
        best_start = -1
        best: tuple[str, int | None] = ("prose", None)
        for b in blocks:
            if b.body_start <= offset < b.body_end and b.body_start > best_start:
                best_start = b.body_start
                best = ("statement", b.index)
        for p in proofs:
            if p.start <= offset < p.end and p.start > best_start:
                best_start = p.start
                best = ("proof", p.owner)
        return best

    for eq in equations:
        ctx, bi = context_at(eq.start)
        eq.owner_context = ctx if bi is not None else None
        eq.owner_block = bi
        eq.section_id = section_id_at(eq.start)
        eq.id = "eq-" + re.sub(r"[^A-Za-z0-9.]+", "", eq.number or str(eq.index))

    # ---- refs and cites ------------------------------------------------------
    heading_spans = [(s.offset, s.heading_end) for s in sections]
    caption_spans = []
    for cm in re.finditer(r"\\caption\s*", text[body_start:body_end]):
        g = read_group(text, body_start + cm.end())
        if g:
            caption_spans.append((body_start + cm.start(), g[2]))

    def classify(offset: int) -> tuple[str, int | None]:
        if any(a <= offset < b for a, b in heading_spans):
            return "heading", None
        if any(a <= offset < b for a, b in caption_spans):
            return "caption", None
        # optional title of a proof env / block
        for p in proofs:
            if p.source == "env" and p.outer_start <= offset < p.start:
                return "title", p.owner
        for b in blocks:
            if b.env.body_start <= offset < b.body_start:
                return "title", b.index
        return context_at(offset)

    refs: list[Occurrence] = []
    for rm in ref_re.finditer(text, body_start, body_end):
        cmd = rm.group(1)
        i = rm.end()
        keys: list[str] = []
        if cmd in ("crefrange", "Crefrange"):
            g1 = read_group(text, i)
            g2 = read_group(text, g1[2]) if g1 else None
            if g1 and g2:
                keys = [g1[0].strip(), g2[0].strip()]
                end = g2[2]
            else:
                continue
        else:
            g = read_group(text, i)
            if g is None or not g[0].strip() or g[0].startswith("\\"):
                continue
            keys = [k.strip() for k in g[0].split(",") if k.strip()]
            end = g[2]
        ctx, bi = classify(rm.start())
        refs.append(Occurrence(rm.start(), end, cmd, keys, None, ctx, bi, section_id_at(rm.start())))
    # \hyperref[label]{text}
    for hm in re.finditer(r"\\hyperref\s*\[([^\]]+)\]", text[body_start:body_end]):
        off = body_start + hm.start()
        ctx, bi = classify(off)
        refs.append(Occurrence(off, body_start + hm.end(), "hyperref", [hm.group(1).strip()], None, ctx, bi,
                               section_id_at(off)))
    refs.sort(key=lambda r: r.offset)

    cite_re = _build_cite_re(info)
    cites: list[Occurrence] = []
    for cm in cite_re.finditer(text, body_start, body_end):
        i = cm.end()
        notes = []
        for _ in range(2):
            o = read_optional(text, i)
            if o is None:
                break
            notes.append(o[0])
            i = o[2]
        g = read_group(text, i)
        if g is None:
            continue
        keys = [k.strip() for k in g[0].split(",") if k.strip()]
        ctx, bi = classify(cm.start())
        note = notes[-1] if notes else None
        cites.append(Occurrence(cm.start(), g[2], cm.group(1), keys, note or None, ctx, bi,
                                section_id_at(cm.start())))

    # ---- misc ----------------------------------------------------------------
    math = find_math_segments(text, envs, body_start, body_end)
    abstract = None
    for e in envs:
        if e.name == "abstract":
            abstract = (e.body_start, e.body_end)
            break
    if abstract is None:
        am = re.search(r"\\abstract\s*\{", text)
        if am:
            k = match_brace(text, am.end() - 1)
            if k > 0:
                abstract = (am.end(), k)

    # Lead-in paragraph before each top-level block.
    stops = sorted([b.env.end for b in blocks] + [p.outer_end for p in proofs] + [s.heading_end for s in sections])
    for b in blocks:
        prev_stop = max([x for x in stops if x <= b.env.begin] or [body_start])
        start = max(prev_stop, b.env.begin - 1500)
        seg = text[start:b.env.begin]
        # Keep only the last paragraph (plus the one before if very short).
        paras = re.split(r"\n[ \t]*\n", seg)
        lead = paras[-1] if paras else ""
        if len(lead.strip()) < 80 and len(paras) >= 2:
            lead = paras[-2] + "\n\n" + lead
        ls = b.env.begin - len(lead)
        if lead.strip():
            b.lead_in = (ls, b.env.begin)

    _assign_ids(blocks, sections)

    return ParsedPaper(flat=flat, info=info, body_start=body_start, body_end=body_end, envs=envs,
                       sections=sections, blocks=blocks, proofs=proofs, equations=equations, labels=labels,
                       duplicate_labels=duplicates, refs=refs, cites=cites, math=math, abstract=abstract)


# ---------------------------------------------------------------------------

_NUM_RE = r"([A-Z]?\d+(?:\.\d+)*|[IVX]+(?:\.\d+)*)"


def _find_block_by_text(desc: str, blocks: list[Block]) -> int | None:
    """Match 'Theorem 5.16' / 'Lemma 3' / 'the Step Lemma' against blocks."""
    d = strip_latex_to_text(desc, keep_math=False)
    m = re.search(r"(Theorem|Lemma|Proposition|Corollary|Claim|Fact|Conjecture|Definition)s?\s*~?\s*" + _NUM_RE, d, re.I)
    if m:
        kind_word = m.group(1).lower()
        num = m.group(2)
        cands = [b for b in blocks if b.number == num]
        exact = [b for b in cands if b.spec.display.lower().startswith(kind_word)]
        pick = exact or cands
        if len(pick) == 1:
            return pick[0].index
        if pick:
            return pick[0].index
    # Title match ("Proof of the Step Lemma")
    tm = re.search(r"of\s+(?:the\s+)?(.+)$", d, re.I)
    if tm:
        want = tm.group(1).strip().lower().rstrip(". ")
        for b in blocks:
            if b.title:
                t = strip_latex_to_text(b.title, keep_math=False).lower()
                full = (t + " " + b.spec.display.lower()).strip()
                if t and (want == t or want == full or want.startswith(t) or (len(t) > 4 and t in want)):
                    return b.index
    return None


_PROVE_PHRASE = re.compile(r"\b(?:prove|proving|proof\s+of|establish|demonstrate|verify)\b", re.I)
_STRICT_FIRST = re.compile(
    r"\s*(?:\\(?:textit|textbf|emph)\s*\{)?\s*(?:We\s+(?:now\s+|first\s+|will\s+)?(?:prove|establish|show)|"
    r"(?:The\s+)?[Pp]roof\s+of|To\s+prove|In\s+order\s+to\s+prove|We\s+(?:now\s+)?(?:give|present)\s+the\s+proof\s+of)", re.I)
_NOT_OWNERSHIP = re.compile(r"similar|analog|modif|as\s+in|same\s+as|recall|follows?\s+the|like\s+the|mimic|adapt", re.I)


def _owner_from_prove_phrase(s: str, labels, ref_re, last: bool) -> int | None:
    """Find 'prove ... \\ref{x}' in s and return the block index of x."""
    candidates = []
    for pm in _PROVE_PHRASE.finditer(s):
        window = s[pm.end():pm.end() + 80]
        rm = ref_re.search(window)
        if not rm:
            continue
        # Stop at sentence end between the verb and the ref.
        if re.search(r"[.;]\s", window[:rm.start()]):
            continue
        g = read_group(window, rm.end())
        if not g:
            continue
        t = labels.get(g[0].split(",")[0].strip())
        if t is not None and t.kind == "block":
            candidates.append(t.index)
        elif t is not None and t.block is not None:
            candidates.append(t.block)
    if not candidates:
        return None
    if last:
        # The phrase must be in the final sentence before the proof.
        tail = re.split(r"(?<=[.!?])\s+(?=\S)", s.strip())
        if tail and (not _PROVE_PHRASE.search(tail[-1]) or _NOT_OWNERSHIP.search(tail[-1])):
            return None
        return candidates[-1]
    return candidates[0]


def _assign_proof_owners(text, blocks, proofs, labels, ref_re, info):
    for p in proofs:
        title = p.title or ""
        owner = None
        how = ""
        # 1) explicit \ref in the title
        for rm in ref_re.finditer(title):
            g = read_group(title, rm.end())
            if g:
                lab = g[0].split(",")[0].strip()
                t = labels.get(lab)
                if t is not None and t.kind == "block":
                    owner, how = t.index, "ref-in-title"
                    break
                if t is not None and t.kind in ("equation", "item") and t.block is not None:
                    owner, how = t.block, "ref-in-title"
                    break
        # 2) textual number / title
        if owner is None and title:
            for part in title.split("||"):
                bi = _find_block_by_text(part, blocks)
                if bi is not None:
                    owner, how = bi, "text-in-title"
                    break
        # 3) the sentence right before the proof: "... required to prove Lemma \ref{x}."
        if owner is None and p.source in ("env", "paragraph"):
            prev_stop = max([b.env.end for b in blocks if b.env.end <= p.outer_start] +
                            [q.outer_end for q in proofs if q.outer_end <= p.outer_start] + [0])
            lead = text[max(prev_stop, p.outer_start - 400):p.outer_start]
            owner = _owner_from_prove_phrase(lead, labels, ref_re, last=True)
            if owner is not None:
                how = "lead-in-sentence"
        # 4) the first sentence of the proof, strictly: "We now prove Theorem \ref{x}." / "Proof of Lemma \ref{x}."
        if owner is None and p.source in ("env", "paragraph"):
            head = text[p.start:min(p.end, p.start + 300)]
            first_sentence = re.split(r"(?<=[.!?])\s", head, maxsplit=1)[0]
            if _STRICT_FIRST.match(first_sentence) and not _NOT_OWNERSHIP.search(first_sentence):
                owner = _owner_from_prove_phrase(first_sentence, labels, ref_re, last=False)
                if owner is not None:
                    how = "first-sentence"
        # 5) adjacency: the closest preceding block, if no other proof lies in between
        if owner is None and p.source in ("env", "paragraph"):
            prev = [b for b in blocks if b.env.end <= p.outer_start]
            if prev:
                b = max(prev, key=lambda b: b.env.end)
                between = text[b.env.end:p.outer_start]
                intervening = [x for x in blocks if b.env.end <= x.env.begin < p.outer_start]
                other_proofs = [q for q in proofs if q is not p and b.env.end <= q.outer_start < p.outer_start
                                and q.source != "section"]
                if not intervening and not other_proofs and len(between) < 4000:
                    owner, how = b.index, "adjacent"
                elif not intervening:
                    how = "ambiguous"
        if owner is not None and blocks[owner].spec.kind not in PROVABLE_KINDS | {"conjecture", "other",
                                                                                   "problem", "algorithm"}:
            # A proof right after a definition/remark: keep it but note.
            how += ":non-provable-kind"
        p.owner = owner
        p.how = how


def _assign_ids(blocks: list[Block], sections: list[Section]):
    used: set[str] = set()
    unnumbered: dict[str, int] = defaultdict(int)
    for b in blocks:
        prefix = KIND_PREFIX.get(b.spec.kind, "env")
        if b.number:
            base = f"{prefix}-{b.number}"
        else:
            unnumbered[prefix] += 1
            base = f"{prefix}-u{unnumbered[prefix]}"
        bid = base
        k = 2
        while bid in used:
            bid = f"{base}-{k}"
            k += 1
        used.add(bid)
        b.id = bid
