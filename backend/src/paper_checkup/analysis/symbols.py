"""Symbol table: deterministic detection of notation definitions and first uses.

Detection is pattern-based ("Let $X$ be", "we write $X$ for", "$X := ...$", \\emph{term} inside
definition environments, user macros). Meanings are filled in later by the LLM stage; locations,
first uses and flags are computed here.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, field

from ..latex.scan import MathSegment, normalize_ws, strip_latex_to_text
from ..latex.structure import ParsedPaper
from ..models import Edge, Evidence, Loc, Node, Symbol, SymbolDef
from .graph import GraphBuilder, loc_of, sentence_around

FONT_CMDS = {"\\mathcal", "\\mathbb", "\\mathrm", "\\mathbf", "\\mathfrak", "\\mathsf", "\\mathit", "\\mathtt",
             "\\boldsymbol", "\\bm", "\\mathscr", "\\operatorname", "\\hat", "\\widehat", "\\tilde", "\\widetilde",
             "\\bar", "\\overline", "\\vec", "\\dot", "\\ddot", "\\check", "\\breve", "\\underline", "\\mathds",
             "\\text", "\\textrm", "\\mbox", "\\textup", "\\textit", "\\textbf", "\\mathring"}
SPACING = {"\\,", "\\;", "\\!", "\\:", "\\ ", "\\quad", "\\qquad", "\\left", "\\right", "\\big", "\\Big",
           "\\bigg", "\\Bigg", "\\bigl", "\\bigr", "\\Bigl", "\\Bigr", "\\biggl", "\\biggr", "\\displaystyle",
           "\\textstyle", "\\scriptstyle", "\\nonumber", "\\notag", "\\limits", "\\nolimits", "&", "\\\\", "~",
           "\\middle", "\\phantom", "\\hfill", "\\allowbreak"}
RELATIONS = {"=", ":", "\\in", "\\subseteq", "\\subset", "\\supseteq", "\\to", "\\rightarrow", "\\leq", "\\geq",
             "\\le", "\\ge", "<", ">", "\\neq", "\\ne", "\\sim", "\\cong", "\\simeq", "\\approx", "\\mapsto",
             "\\colon", "\\equiv", "\\notin", "\\leftarrow", "\\longrightarrow", "\\Rightarrow", "\\iff",
             "\\coloneqq", ":=", "\\ll", "\\gg", "\\propto", "\\mid", "|", "\\vdash", "\\models", "\\perp",
             "\\triangleq", "\\defeq", "\\eqqcolon"}
COMMON_FUNCS = {"\\sin", "\\cos", "\\tan", "\\log", "\\ln", "\\exp", "\\max", "\\min", "\\sup", "\\inf", "\\lim",
                "\\det", "\\dim", "\\ker", "\\deg", "\\gcd", "\\Pr", "\\arg", "\\mod", "\\bmod", "\\pmod",
                "\\sum", "\\prod", "\\int", "\\cdot", "\\cdots", "\\ldots", "\\dots", "\\times", "\\otimes",
                "\\oplus", "\\infty", "\\pm", "\\mp", "\\circ", "\\partial", "\\nabla", "\\forall", "\\exists",
                "\\neg", "\\wedge", "\\vee", "\\cap", "\\cup", "\\emptyset", "\\varnothing", "\\setminus",
                "\\frac", "\\sqrt", "\\binom", "\\dagger", "\\top", "\\prime", "\\ell", "\\Re", "\\Im"}
SKIP_ENVS = {"tikzpicture", "pgfpicture", "picture", "tikzcd", "figure", "figure*", "table", "table*",
             "tabular", "thebibliography"}

STANDARD_COMMANDS = {
    "alpha", "beta", "gamma", "delta", "epsilon", "varepsilon", "zeta", "eta", "theta", "vartheta", "iota", "kappa",
    "lambda", "mu", "nu", "xi", "pi", "varpi", "rho", "varrho", "sigma", "varsigma", "tau", "upsilon", "phi", "varphi",
    "chi", "psi", "omega", "Gamma", "Delta", "Theta", "Lambda", "Xi", "Pi", "Sigma", "Upsilon", "Phi", "Psi", "Omega",
    "le", "ge", "leq", "geq", "emptyset", "setminus", "colon", "mid", "Re", "Im", "hat", "tilde", "bar", "vec", "dot",
    "phantom", "implies", "iff", "qedsymbol", "qed", "square", "Box", "dag", "ddag", "S", "P", "L", "l", "o", "O",
}

_TOK_RE = re.compile(r"\\[A-Za-z@]+\*?|\\.|[A-Za-z]|\d+(?:\.\d+)?|\S")


def _brace_end(tokens: list[str], i: int) -> int:
    depth = 0
    for j in range(i, len(tokens)):
        if tokens[j] == "{":
            depth += 1
        elif tokens[j] == "}":
            depth -= 1
            if depth == 0:
                return j
    return -1


def canon_tokens(math: str) -> list[str]:
    """Canonical token list: font/accent groups merged, spacing dropped, single-token braces removed."""
    math = re.sub(r"\\(?:label|tag\*?|ref|eqref|cite)\s*\{[^{}]*\}", " ", math)
    raw = [t for t in _TOK_RE.findall(math) if t not in SPACING]
    out: list[str] = []
    i = 0
    n = len(raw)
    while i < n:
        t = raw[i]
        if t in FONT_CMDS and i + 1 < n:
            if raw[i + 1] == "{":
                j = _brace_end(raw, i + 1)
                if j > 0:
                    inner = "".join(raw[i + 2:j])
                    out.append(f"{t}{{{inner}}}")
                    i = j + 1
                    continue
            else:
                out.append(f"{t}{{{raw[i + 1]}}}")
                i += 2
                continue
        if t in ("_", "^") and i + 1 < n and raw[i + 1] == "{":
            j = _brace_end(raw, i + 1)
            if j == i + 3:      # single token in braces
                out.extend([t, raw[i + 2]])
                i = j + 1
                continue
        out.append(t)
        i += 1
    # Merge "{X}" around a single token anywhere (e.g. {G}_n)
    res: list[str] = []
    k = 0
    while k < len(out):
        if out[k] == "{" and k + 2 < len(out) and out[k + 2] == "}":
            res.append(out[k + 1])
            k += 3
            continue
        res.append(out[k])
        k += 1
    return res


def canon(math: str) -> str:
    return "".join(canon_tokens(math))


def _split_top_commas(tokens: list[str]) -> list[list[str]]:
    parts, cur, depth = [], [], 0
    for t in tokens:
        if t in ("{", "(", "[", "\\{", "\\langle"):
            depth += 1
        elif t in ("}", ")", "]", "\\}", "\\rangle"):
            depth -= 1
        if t == "," and depth == 0:
            parts.append(cur)
            cur = []
        else:
            cur.append(t)
    parts.append(cur)
    return [p for p in parts if p]


def defined_symbols_from_math(math: str, rhs: bool = False) -> list[list[str]]:
    """Extract the defined symbol(s) from the math of a defining phrase ("Let $x, y \\in X$ ...")."""
    toks = canon_tokens(math)
    # explicit definitional operator
    for op_i, t in enumerate(toks):
        if t in ("\\coloneqq", "\\triangleq", "\\defeq") or (t == ":" and op_i + 1 < len(toks) and toks[op_i + 1] == "="):
            toks = toks[:op_i]
            break
        if t == "\\eqqcolon" or (t == "=" and op_i + 1 < len(toks) and toks[op_i + 1] == ":"):
            toks = toks[op_i + (1 if t == "\\eqqcolon" else 2):]
            break
    else:
        for op_i, t in enumerate(toks):
            if t in RELATIONS:
                toks = toks[:op_i]
                break
    out = []
    for part in _split_top_commas(toks):
        # function notation f(x) -> f ; but keep things like \mathcal{D}_n
        if len(part) > 1 and part[1] == "(" and part[-1] == ")":
            part = part[:1]
        if len(part) > 1 and part[1] == "\\colon":
            part = part[:1]
        if not part or len(part) > 8:
            continue
        s = "".join(part)
        if not re.search(r"[A-Za-z]", s) or re.fullmatch(r"[\d.]+", s):
            continue
        if part[0] in COMMON_FUNCS or s in COMMON_FUNCS:
            continue
        if any(t in ("+", "-", "\\cdot", "\\frac", "\\sum", "\\prod") for t in part):
            continue
        out.append(part)
    return out


_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("let", re.compile(r"\b(?:let|suppose|assume|fix|consider)\s+((?:⟦\d+⟧\s*(?:,|and|,\s*and)?\s*)+)"
                       r"(?:be|denote|denotes|stand|stands|is|are|represent|represents)\b", re.I)),
    ("write", re.compile(r"\b(?:we\s+)?(?:write|use|reserve)\s+((?:⟦\d+⟧\s*(?:,|and)?\s*)+)\s*(?:for|to\s+denote|to\s+mean|as)\b", re.I)),
    ("denote-by", re.compile(r"\b(?:denote[ds]?|written|write|abbreviate[ds]?)\s+(?:it\s+|this\s+|them\s+)?(?:by|as)\s+((?:⟦\d+⟧\s*(?:,|and)?\s*)+)", re.I)),
    ("x-denotes", re.compile(r"((?:⟦\d+⟧\s*(?:,|and)?\s*)+)\s+(?:denotes|denote|will\s+denote|stands\s+for|is\s+defined|are\s+defined|is\s+called|are\s+called|is\s+the\s+set|is\s+given\s+by|refers\s+to)\b", re.I)),
    ("define", re.compile(r"\b(?:we\s+)?(?:define|defined|defines|introduce)\s+(?:the\s+[\w-]+\s+(?:[\w-]+\s+)?)?((?:⟦\d+⟧\s*(?:,|and)?\s*)+)", re.I)),
    ("where", re.compile(r"\b(?:where|here)\s+((?:⟦\d+⟧\s*(?:,|and)?\s*)+)\s*(?:is|are|denotes|denote|stands)\b", re.I)),
]
_TERM_TRIGGER = re.compile(r"(?:called|we\s+say|is\s+said\s+to\s+be|are\s+said\s+to\s+be|we\s+call|refer\s+to|termed|known\s+as|define)\s*(?:an?\s+|the\s+)?[^.]{0,40}$", re.I)
_EMPH_RE = re.compile(r"\\(?:emph|textit|textbf|textsl|defn|term|dfn|textsc)\s*\{([^{}]{2,80})\}"
                      r"|\{\\(?:em|it|bf|sl)\s+([^{}]{2,80})\}")
_NOTATION_BODY = re.compile(r"\\(?:mathbb|mathcal|mathfrak|mathscr|mathsf|mathbf|operatorname|mathrm|mathit|mathds|bm|boldsymbol)\b")
_LAYOUT_BODY = re.compile(r"\\(?:left|right|begin|arraystretch|renewcommand|quad|qquad|hspace|vspace|lfloor|rfloor|"
                          r"langle|rangle|rule|kern|mskip|hskip|xmatrix|scriptsize|footnotesize|tiny|small|large|"
                          r"arraycolsep|setlength|ensuremath\s*\{\s*\})\b|^\s*[\^_]|\\\{|#1\s*\\rangle")


@dataclass
class _Cand:
    key: str
    tokens: list[str]
    latex: str
    kind: str
    defs: list[tuple[int, int, str, str, int | None]] = field(default_factory=list)  # (off, end, scope, pattern, block)
    macro_expansion: str | None = None


class SymbolExtractor:
    def __init__(self, paper: ParsedPaper, gb: GraphBuilder):
        self.p = paper
        self.gb = gb
        self.text = paper.text
        skip = [(e.begin, e.end) for e in paper.envs if e.name in SKIP_ENVS]
        self.math: list[MathSegment] = [m for m in paper.math if not any(a <= m.start < b for a, b in skip)]
        self.math_starts = [m.start for m in self.math]
        self.math_tokens = [canon_tokens(m.inner(self.text)) for m in self.math]
        self.cands: dict[str, _Cand] = {}
        self._build_masked()
        self._regions()

    # -- context helpers -----------------------------------------------------
    def _regions(self):
        """For each block: (start, end, block index, 'statement'|'proof')."""
        self.regions: list[tuple[int, int, int, str]] = []
        for b in self.p.blocks:
            self.regions.append((b.body_start, b.body_end, b.index, "statement"))
        for pp in self.p.proofs:
            if pp.owner is not None:
                self.regions.append((pp.start, pp.end, pp.owner, "proof"))

    def context_of(self, off: int) -> tuple[str, int | None]:
        best = None
        for (a, b, bi, ctx) in self.regions:
            if a <= off < b and (best is None or a > best[0]):
                best = (a, b, bi, ctx)
        if best is None:
            return "prose", None
        bi, ctx = best[2], best[3]
        if ctx == "statement" and self.p.blocks[bi].kind in ("definition", "notation"):
            return "definition", bi
        return ctx, bi

    def _build_masked(self):
        """Body text with inline math replaced by ⟦i⟧ placeholders (offset map kept)."""
        pieces = []
        self.mask_map: list[tuple[int, int]] = []   # (masked offset, original offset) breakpoints
        pos = self.p.body_start
        moff = 0
        for i, m in enumerate(self.math):
            if m.start < pos:
                continue
            chunk = self.text[pos:m.start]
            pieces.append(chunk)
            self.mask_map.append((moff, pos))
            moff += len(chunk)
            ph = f"⟦{i}⟧"
            pieces.append(ph)
            self.mask_map.append((moff, m.start))
            moff += len(ph)
            pos = m.end
        pieces.append(self.text[pos:self.p.body_end])
        self.mask_map.append((moff, pos))
        self.masked = "".join(pieces)
        self._mask_keys = [a for a, _ in self.mask_map]

    def orig_offset(self, masked_off: int) -> int:
        i = bisect_right(self._mask_keys, masked_off) - 1
        a, o = self.mask_map[max(i, 0)]
        return o + (masked_off - a)

    # -- candidates ------------------------------------------------------------
    def _add_def(self, tokens: list[str], latex: str, off: int, end: int, pattern: str, kind: str = "symbol"):
        key = "".join(tokens)
        if not key:
            return
        scope, bi = self.context_of(off)
        c = self.cands.get(key)
        if c is None:
            c = self.cands[key] = _Cand(key=key, tokens=tokens, latex=latex, kind=kind)
        if any(abs(d[0] - off) < 5 for d in c.defs):
            return
        c.defs.append((off, end, scope, pattern, bi))

    def find_definitions(self):
        # 1) phrase patterns on masked text
        for pname, rx in _PATTERNS:
            for m in rx.finditer(self.masked):
                for pm in re.finditer(r"⟦(\d+)⟧", m.group(1)):
                    mi = int(pm.group(1))
                    seg = self.math[mi]
                    if seg.display:
                        continue
                    for part in defined_symbols_from_math(seg.inner(self.text)):
                        self._add_def(part, "".join(part), seg.start, seg.end, pname)
        # 2) definitional operators inside any math (not algorithmic assignments in lists/algorithms)
        algo_ranges = [(e.begin, e.end) for e in self.p.envs
                       if e.name.rstrip("*") in ("enumerate", "itemize", "algorithm", "algorithmic", "algorithmic*",
                                                  "description", "procedure", "lstlisting")]
        for mi, seg in enumerate(self.math):
            if any(a <= seg.start < b for a, b in algo_ranges):
                continue
            inner = seg.inner(self.text)
            if not re.search(r":=|\\coloneqq|\\triangleq|\\defeq|=:|\\eqqcolon|\\stackrel\s*\{[^{}]*def", inner):
                continue
            for line in re.split(r"\\\\", inner):
                if re.search(r":=|\\coloneqq|\\triangleq|\\defeq", line) or re.search(r"\\stackrel\s*\{[^{}]*def[^{}]*\}\s*\{\s*=\s*\}", line):
                    line2 = re.sub(r"\\stackrel\s*\{[^{}]*def[^{}]*\}\s*\{\s*=\s*\}", ":=", line)
                    for part in defined_symbols_from_math(line2):
                        self._add_def(part, "".join(part), seg.start, seg.end, "assign")
        # 3) terms: \emph{..} in definition environments and after "called"/"we say"
        for b in self.p.blocks:
            if b.kind not in ("definition", "notation"):
                continue
            body = self.text[b.body_start:b.body_end]
            for em in _EMPH_RE.finditer(body):
                self._add_term(em.group(1) or em.group(2), b.body_start + em.start(), b.body_start + em.end(),
                               "emph-in-definition")
            if b.title:
                self._add_term(b.title, b.body_start, b.body_start + 1, "definition-title")
        for em in _EMPH_RE.finditer(self.text, self.p.body_start, self.p.body_end):
            before = self.text[max(0, em.start() - 60):em.start()]
            if _TERM_TRIGGER.search(before):
                scope, bi = self.context_of(em.start())
                if scope in ("prose", "definition"):
                    self._add_term(em.group(1) or em.group(2), em.start(), em.end(), "emph-after-called")
        # 4) user macros used in math
        info = self.p.info
        used_macros: dict[str, int] = {}
        for toks in self.math_tokens:
            for t in toks:
                base = t.split("{", 1)[0]
                if base in info.macros:
                    used_macros[base] = used_macros.get(base, 0) + 1
        for name in used_macros:
            md = info.macros[name]
            if name[1:] in STANDARD_COMMANDS:
                continue
            body = md.body
            if md.kind == "def" and ("#" in body and md.nargs == 0):
                continue
            if re.search(r"\\(?:begin|tikz|draw|hbox|setbox|raisebox|scalebox|includegraphics)\b", body):
                continue
            if len(body) > 160 or not body.strip():
                continue
            has_text_def = name in self.cands and any(d[2] in ("definition", "prose") for d in self.cands[name].defs)
            notation_like = (bool(_NOTATION_BODY.search(body)) or
                             bool(re.fullmatch(r"\\?[A-Za-z]+\s*(?:[_^]\s*\{?[^{}]{1,12}\}?)?", body.strip())))
            if not has_text_def and (not notation_like or _LAYOUT_BODY.search(body)):
                continue
            c = self.cands.get(name)
            if c is None:
                c = self.cands[name] = _Cand(key=name, tokens=[name], latex=name, kind="macro")
            c.macro_expansion = body
            if c.kind == "symbol":
                c.kind = "macro"

    def _add_term(self, raw: str, off: int, end: int, pattern: str):
        if "$" in raw or "\\(" in raw:
            return      # math-bearing phrases are notations, not plain terms
        t = strip_latex_to_text(raw, keep_math=False)
        t = re.sub(r"\$[^$]*\$", "", t)
        t = re.sub(r"\\[A-Za-z]+", "", t)
        t = re.sub(r"^[-\s]+|[-\s.,:;]+$", "", t).strip()
        if len(t) < 4 or len(t) > 50 or not re.search(r"[A-Za-z]{3}", t):
            return
        if t.lower() in {"note", "proof", "remark", "example", "definition", "lemma", "theorem", "i.e", "e.g",
                         "not", "only", "all", "each", "every", "must", "unique", "exactly", "precisely"}:
            return
        low = t.lower()
        if len(low) > 4 and low.endswith("s") and not low.endswith(("ss", "is", "us", "ics")):
            low = low[:-1]
            t = t[:-1]
        key = "term:" + low
        scope, bi = self.context_of(off)
        c = self.cands.get(key)
        if c is None:
            c = self.cands[key] = _Cand(key=key, tokens=[], latex=t, kind="term")
        if any(abs(d[0] - off) < 5 for d in c.defs):
            return
        c.defs.append((off, end, scope, pattern, bi))

    # -- occurrences -------------------------------------------------------------
    def occurrences(self, c: _Cand) -> list[int]:
        """Offsets of math segments (or text) where the symbol occurs."""
        if c.kind == "term":
            word = re.escape(c.latex.lower()).replace("\\ ", r"[\s~-]+")
            rx = re.compile(r"(?<![A-Za-z])" + word + r"(?:s|es)?(?![A-Za-z])", re.I)
            return [self.orig_offset(m.start()) for m in rx.finditer(self.masked)]
        first = c.tokens[0]
        k = len(c.tokens)
        out = []
        for idx in self._token_index.get(first, []):
            mi, pos = idx
            toks = self.math_tokens[mi]
            if toks[pos:pos + k] != c.tokens:
                continue
            nxt = toks[pos + k] if pos + k < len(toks) else None
            if nxt == "_" and "_" not in c.tokens:
                continue
            if nxt == "(" and k == 1 and len(first) == 1 and False:
                continue
            out.append(self.math[mi].start)
        return out

    def _index_tokens(self):
        self._token_index: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for mi, toks in enumerate(self.math_tokens):
            for pos, t in enumerate(toks):
                self._token_index[t].append((mi, pos))
                base = t.split("{", 1)[0]
                if base != t and base in self.p.info.macros:
                    self._token_index[base].append((mi, pos))

    # -- main ----------------------------------------------------------------------
    def run(self, nodes: dict[str, Node]) -> tuple[list[Symbol], list[Edge]]:
        self.find_definitions()
        self._index_tokens()
        symbols: list[Symbol] = []
        sym_edges: list[Edge] = []
        abstract = self.p.abstract
        first_section_end = None
        tech_sections = [s for s in self.p.sections if s.level <= 1]
        if tech_sections:
            first_section_end = tech_sections[0].end
        block_by_index = {b.index: b for b in self.p.blocks}
        used_by_node: dict[str, set[str]] = defaultdict(set)

        sid = 0
        first_offset: dict[str, int] = {}
        for key, c in self.cands.items():
            occ = sorted(self.occurrences(c))
            if c.kind == "macro" and c.tokens == [key]:
                # macro tokens may carry arguments: count tokens starting with the macro name
                pass
            global_defs = [d for d in c.defs if d[2] in ("definition", "prose")]
            if c.kind == "symbol":
                if not global_defs:
                    continue          # purely local variables (in a proof / statement) are not listed
                if self._is_trivial(c.tokens):
                    continue
            if c.kind == "term" and not c.defs:
                continue
            if c.kind == "macro" and not occ:
                continue
            sid += 1
            defs_models = []
            for (off, end, scope, pattern, bi) in sorted(c.defs)[:6]:
                defs_models.append(SymbolDef(
                    loc=loc_of(self.p, off, end), node_id=block_by_index[bi].id if bi is not None else None,
                    context=sentence_around(self.text, off, end, limit=220), scope=scope, pattern=pattern))
            if c.kind == "macro":
                md = self.p.info.macros.get(c.key)
                if md is not None and md.offset >= 0:
                    defs_models.insert(0, SymbolDef(loc=loc_of(self.p, md.offset, md.offset + 10), node_id=None,
                                                    context=f"\\newcommand{{{md.name}}}{{{md.body}}}", scope="macro",
                                                    pattern="macro"))
            sym = Symbol(id=f"sym-{sid}", latex=c.latex, kind=c.kind, macro_expansion=c.macro_expansion,
                         definitions=defs_models, uses=len(occ))
            defining_block = next((d[4] for d in sorted(c.defs) if d[2] == "definition"), None)
            if defining_block is not None:
                sym.defined_in_node = block_by_index[defining_block].id
            if occ:
                fu = occ[0]
                sym.first_use = loc_of(self.p, fu, fu + 1)
                ctx, bi = self.context_of(fu)
                sym.first_use_node = block_by_index[bi].id if bi is not None else None
                sym.first_use_context = sentence_around(self.text, fu, fu + 1, limit=200)
            # flags (algorithmic ':=' assignments alone are not treated as definitions for flagging)
            flag_defs = [d for d in global_defs if d[3] != "assign" or d[2] == "definition"]
            if flag_defs and occ:
                first_def = min(d[0] for d in flag_defs)
                sec_of_def = self.p.section_at(first_def)
                early = [o for o in occ if o < first_def - 1200 or
                         (o < first_def - 100 and self.p.section_at(o) is not sec_of_def)]
                if early and c.kind != "macro":
                    in_intro = (abstract is not None and abstract[0] <= early[0] < abstract[1]) or \
                               (first_section_end is not None and early[0] < first_section_end) or \
                               early[0] < (tech_sections[0].offset if tech_sections else 0)
                    sym.flags.append("used_before_definition_intro" if in_intro else "used_before_definition")
            if len(flag_defs) >= 2:
                spread = max(d[0] for d in flag_defs) - min(d[0] for d in flag_defs)
                if spread > 400:
                    sym.flags.append("multiple_definitions")
            symbols.append(sym)
            positions = [d[0] for d in c.defs]
            if occ:
                positions.append(occ[0])
            first_offset[sym.id] = min(positions) if positions else 10 ** 12

            # usage per node and symbol edges from definition blocks
            occ_nodes: dict[str, tuple[int, str]] = {}
            for o in occ:
                ctx, bi = self.context_of(o)
                if bi is None:
                    continue
                nid = block_by_index[bi].id
                used_by_node[nid].add(sym.id)
                if nid not in occ_nodes:
                    occ_nodes[nid] = (o, "statement" if ctx in ("statement", "definition") else "proof")
            if defining_block is not None:
                src = block_by_index[defining_block].id
                src_order = nodes[src].order
                for nid, (o, ctx) in occ_nodes.items():
                    if nid == src or nodes[nid].order < src_order:
                        continue
                    sym_edges.append(Edge(
                        id="", source=src, target=nid, kind="symbol", context=ctx,
                        via="term" if c.kind == "term" else "symbol",
                        evidence=[Evidence(loc=loc_of(self.p, o, o + 1), text=sentence_around(self.text, o, o + 1),
                                           via="term" if c.kind == "term" else "symbol", context=ctx)],
                        reason=(f"使用了 {nodes[src].display} {nodes[src].number or ''} 中定义的"
                                + (f"术语“{c.latex}”" if c.kind == "term" else f"记号 ${c.latex}$"))))
        for nid, syms in used_by_node.items():
            if nid in nodes:
                nodes[nid].symbols_used = sorted(syms, key=lambda s: int(s.split("-")[1]))
        symbols.sort(key=lambda s: first_offset.get(s.id, 10 ** 12))
        return symbols, sym_edges

    @staticmethod
    def _is_trivial(tokens: list[str]) -> bool:
        if len(tokens) == 1:
            t = tokens[0]
            if len(t) == 1 and t.islower():
                return True           # i, j, k, n, x ...
            if t in ("\\epsilon", "\\varepsilon", "\\delta", "\\alpha", "\\beta", "\\lambda", "\\theta"):
                return True
        return False


def extract_symbols(paper: ParsedPaper, gb: GraphBuilder) -> tuple[list[Symbol], list[Edge]]:
    ex = SymbolExtractor(paper, gb)
    return ex.run(gb.nodes)
