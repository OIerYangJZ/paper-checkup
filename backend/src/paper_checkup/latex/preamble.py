"""Preamble parsing: theorem declarations, macros, counters, title/authors."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .scan import match_brace, read_group, read_optional, strip_latex_to_text

# Ordered: earlier entries win. Matched against the display name (lowercase, contains)
# and against the environment name (startswith) as a fallback.
KIND_KEYWORDS: list[tuple[str, list[str]]] = [
    ("corollary", ["corollary", "corollaire", "推论", "folgerung"]),
    ("conjecture", ["conjecture", "猜想", "vermutung"]),
    ("proposition", ["proposition", "命题"]),
    ("lemma", ["lemma", "lemme", "引理", "hilfssatz"]),
    ("theorem", ["theorem", "théorème", "定理", "satz"]),
    ("definition", ["definition", "définition", "定义"]),
    ("claim", ["claim", "断言"]),
    ("fact", ["fact", "observation", "事实"]),
    ("assumption", ["assumption", "hypothesis", "axiom", "postulate", "假设", "公理"]),
    ("notation", ["notation", "convention", "记号", "约定"]),
    ("remark", ["remark", "note", "注记", "注"]),
    ("example", ["example", "例"]),
    ("problem", ["problem", "question", "exercise", "问题", "open"]),
    ("algorithm", ["algorithm", "procedure", "算法"]),
]

ENV_PREFIX_KIND: list[tuple[str, str]] = [
    ("cor", "corollary"), ("conj", "conjecture"), ("prop", "proposition"), ("lem", "lemma"),
    ("thm", "theorem"), ("theo", "theorem"), ("def", "definition"), ("dfn", "definition"),
    ("claim", "claim"), ("fact", "fact"), ("obs", "fact"), ("ass", "assumption"), ("hyp", "assumption"),
    ("ax", "assumption"), ("not", "notation"), ("conv", "notation"), ("rem", "remark"), ("rmk", "remark"),
    ("ex", "example"), ("prob", "problem"), ("ques", "problem"), ("alg", "algorithm"),
]

# Kinds whose statements are expected to be justified (proof or citation).
PROVABLE_KINDS = {"theorem", "lemma", "proposition", "corollary", "claim", "fact"}

DEFAULT_ENV_NAMES = {
    "theorem": "Theorem", "thm": "Theorem", "lemma": "Lemma", "lem": "Lemma",
    "proposition": "Proposition", "prop": "Proposition", "corollary": "Corollary", "cor": "Corollary",
    "definition": "Definition", "defn": "Definition", "defi": "Definition", "dfn": "Definition",
    "conjecture": "Conjecture", "conj": "Conjecture", "claim": "Claim", "fact": "Fact",
    "observation": "Observation", "remark": "Remark", "rem": "Remark", "example": "Example",
    "assumption": "Assumption", "hypothesis": "Hypothesis", "notation": "Notation",
    "problem": "Problem", "question": "Question", "axiom": "Axiom", "convention": "Convention",
}


def classify_kind(display: str, env: str) -> str:
    d = display.lower()
    for kind, words in KIND_KEYWORDS:
        if any(w in d for w in words):
            return kind
    e = env.lower()
    for p in ("un-", "n-"):
        e = e.removeprefix(p)
    for prefix, kind in ENV_PREFIX_KIND:
        if e.startswith(prefix):
            return kind
    return "other"


@dataclass
class TheoremSpec:
    env: str
    display: str
    kind: str
    counter: str | None          # None => unnumbered
    source: str = "newtheorem"    # newtheorem | declaretheorem | alias | default
    raw_display: str = ""


@dataclass
class MacroDef:
    name: str
    nargs: int
    has_optional: bool
    body: str
    kind: str          # newcommand | def | operator
    offset: int = -1   # offset in flat text (-1 if from an extra preamble)
    file: str | None = None


@dataclass
class PreambleInfo:
    documentclass: str = ""
    class_options: list[str] = field(default_factory=list)
    theorems: dict[str, TheoremSpec] = field(default_factory=dict)
    counter_within: dict[str, str] = field(default_factory=dict)   # counter -> parent counter
    counter_alias: dict[str, str] = field(default_factory=dict)    # aliascnt name -> real counter
    macros: dict[str, MacroDef] = field(default_factory=dict)
    title: str = ""
    authors: list[str] = field(default_factory=list)
    ref_macros: dict[str, str] = field(default_factory=dict)       # \lemref -> 'ref' (expands to a \ref)
    cite_macros: set[str] = field(default_factory=set)

    def resolve_counter(self, c: str) -> str:
        seen = set()
        while c in self.counter_alias and c not in seen:
            seen.add(c)
            c = self.counter_alias[c]
        return c


_NEWTHEOREM_RE = re.compile(r"\\(newtheorem|spnewtheorem)(\*?)\s*\{([^{}]+)\}")
_DECLARETHEOREM_RE = re.compile(r"\\declaretheorem\s*")
_NEWALIASCNT_RE = re.compile(r"\\newaliascnt\s*\{([^{}]+)\}\s*\{([^{}]+)\}")
_NUMBERWITHIN_RE = re.compile(r"\\(?:numberwithin|counterwithin\*?)\s*\{([^{}]+)\}\s*\{([^{}]+)\}")
_ADDTORESET_RE = re.compile(r"\\@addtoreset\s*\{([^{}]+)\}\s*\{([^{}]+)\}")
_NEWENV_RE = re.compile(r"\\(?:re)?newenvironment\*?\s*\{([^{}]+)\}")
_NEWCOMMAND_RE = re.compile(r"\\(newcommand|renewcommand|providecommand|DeclareRobustCommand)\*?\s*")
_DEF_RE = re.compile(r"\\(?:g|e|x)?def\s*(\\[A-Za-z@]+)((?:#\d)*)\s*\{")
_OPERATOR_RE = re.compile(r"\\DeclareMathOperator(\*?)\s*\{?\s*(\\[A-Za-z@]+)\s*\}?\s*\{")


def _parse_newtheorems(text: str, info: PreambleInfo):
    for m in _NEWTHEOREM_RE.finditer(text):
        starred = m.group(2) == "*"
        env = m.group(3).strip()
        i = m.end()
        shared = None
        within = None
        opt = read_optional(text, i)
        if opt is not None:
            shared = opt[0].strip()
            i = opt[2]
        grp = read_group(text, i)
        if grp is None:
            continue
        raw_display = grp[0]
        display = strip_latex_to_text(grp[0], keep_math=False)
        i = grp[2]
        if shared is None and not starred:
            opt2 = read_optional(text, i)
            if opt2 is not None:
                within = opt2[0].strip()
        kind = classify_kind(display, env)
        if starred:
            counter = None
        elif shared:
            counter = shared
        else:
            counter = env
            if within:
                info.counter_within[env] = within
        info.theorems[env] = TheoremSpec(env=env, display=display or env, kind=kind, counter=counter,
                                         raw_display=raw_display)


def _parse_kv(s: str) -> dict[str, str]:
    out = {}
    depth = 0
    cur = ""
    parts = []
    for c in s:
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        if c == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += c
    parts.append(cur)
    for p in parts:
        if "=" in p:
            k, v = p.split("=", 1)
            out[k.strip().lower()] = v.strip().strip("{}")
        elif p.strip():
            out[p.strip().lower()] = ""
    return out


def _parse_declaretheorems(text: str, info: PreambleInfo):
    for m in _DECLARETHEOREM_RE.finditer(text):
        i = m.end()
        opts = {}
        opt = read_optional(text, i)
        if opt is not None:
            opts = _parse_kv(opt[0])
            i = opt[2]
        grp = read_group(text, i)
        if grp is None:
            continue
        i = grp[2]
        opt2 = read_optional(text, i)
        if opt2 is not None:
            opts.update(_parse_kv(opt2[0]))
        for env in grp[0].split(","):
            env = env.strip()
            if not env:
                continue
            display = opts.get("name") or opts.get("title") or env.capitalize()
            numbered = opts.get("numbered", "yes").lower()
            sibling = opts.get("sibling") or opts.get("sharenumber") or opts.get("sharecounter")
            within = opts.get("numberwithin") or opts.get("parent") or opts.get("within")
            if numbered == "no":
                counter = None
            elif sibling:
                counter = sibling
            else:
                counter = env
                if within:
                    info.counter_within[env] = within
            info.theorems[env] = TheoremSpec(env=env, display=display, kind=classify_kind(display, env),
                                             counter=counter, source="declaretheorem")


def _parse_newenv_aliases(text: str, info: PreambleInfo):
    for m in _NEWENV_RE.finditer(text):
        name = m.group(1).strip()
        i = m.end()
        # skip [n][default]
        for _ in range(2):
            opt = read_optional(text, i)
            if opt is None:
                break
            i = opt[2]
        grp = read_group(text, i)
        if grp is None:
            continue
        inner = re.search(r"\\begin\s*\{([^{}]+)\}", grp[0])
        if inner:
            target = inner.group(1).strip()
            if target in info.theorems and name not in info.theorems:
                t = info.theorems[target]
                info.theorems[name] = TheoremSpec(env=name, display=t.display, kind=t.kind,
                                                  counter=t.counter, source="alias")


_UNSAFE_MACRO = re.compile(
    r"\\(setbox|hbox|vbox|raisebox|scalebox|resizebox|includegraphics|tikz|draw|fill|node|foreach|"
    r"def|gdef|edef|newcommand|renewcommand|ifx|ifmmode|ifnum|ifdim|futurelet|expandafter|csname|"
    r"makeatletter|makeatother|par|vspace|hspace\*|smallskip|medskip|bigskip|newline|"
    r"index|marginpar|footnote|textcolor|color|ref|cite|label|input|Qcircuit|xymatrix|qw|gate)\b|@"
)
# Environments KaTeX can render inside a macro body.
KATEX_ENVS = {"array", "matrix", "pmatrix", "bmatrix", "Bmatrix", "vmatrix", "Vmatrix", "smallmatrix", "cases",
              "aligned", "gathered", "alignedat", "split", "subarray", "darray", "rcases", "dcases", "CD",
              "matrix*", "pmatrix*", "bmatrix*"}


def _sanitize_macro_body(body: str) -> str:
    """Drop layout-only assignments that KaTeX does not understand (array spacing etc.)."""
    body = re.sub(r"\\renewcommand\s*\{?\s*\\arraystretch\s*\}?\s*\{[^{}]*\}", "", body)
    body = re.sub(r"\\def\s*\\arraystretch\s*\{[^{}]*\}", "", body)
    body = re.sub(r"\\setlength\s*\{?\s*\\arraycolsep\s*\}?\s*\{[^{}]*\}", "", body)
    body = re.sub(r"\\arraycolsep\s*=?\s*-?(?:[\d.]+|#\d)\s*(?:ex|em|pt|mu)?", "", body)
    body = re.sub(r"\\ensuremath\s*", "", body)
    body = body.replace("\\xspace", "").replace("\\protect", "")
    body = re.sub(r"\\(?:mbox|hbox)\s*\{", r"\\text{", body)
    return body.strip()


def _parse_macros(text: str, info: PreambleInfo, base_offset_valid: bool, file: str | None):
    for m in _NEWCOMMAND_RE.finditer(text):
        i = m.end()
        g = read_group(text, i)
        if g is None:
            continue
        name = g[0].strip()
        if not name.startswith("\\"):
            continue
        i = g[2]
        nargs = 0
        has_opt = False
        o1 = read_optional(text, i)
        if o1 is not None:
            try:
                nargs = int(o1[0].strip())
            except ValueError:
                nargs = 0
            i = o1[2]
            o2 = read_optional(text, i)
            if o2 is not None:
                has_opt = True
                i = o2[2]
        body = read_group(text, i)
        if body is None:
            continue
        _record_macro(info, name, nargs, has_opt, body[0], "newcommand",
                      m.start() if base_offset_valid else -1, file)
    for m in _DEF_RE.finditer(text):
        name = m.group(1)
        nargs = len(re.findall(r"#\d", m.group(2) or ""))
        k = match_brace(text, m.end() - 1)
        if k < 0:
            continue
        _record_macro(info, name, nargs, False, text[m.end():k], "def",
                      m.start() if base_offset_valid else -1, file)
    for m in _OPERATOR_RE.finditer(text):
        star = m.group(1) == "*"
        name = m.group(2)
        k = match_brace(text, m.end() - 1)
        if k < 0:
            continue
        opname = text[m.end():k]
        body = ("\\operatorname*{%s}" if star else "\\operatorname{%s}") % opname
        _record_macro(info, name, 0, False, body, "operator", m.start() if base_offset_valid else -1, file)


_INTERNAL_PARAMS = re.compile(r"^\\(?:arraystretch|baselinestretch|arraycolsep|tabcolsep|the[a-z]+|label[a-z]+|"
                              r"[a-z]*name|thefootnote|qedsymbol|bibname|refname|abstractname|appendixname|"
                              r"contentsname|figurename|tablename|proofname|textfraction|topfraction|"
                              r"bottomfraction|floatpagefraction|dbl[a-z]+)$")


def _record_macro(info, name, nargs, has_opt, body, kind, offset, file):
    # Re-definitions of LaTeX internals (\arraystretch, \thesection, ...) are layout, not notation;
    # \xxxname macros are kept (LyX theorem names) unless they are parametrized.
    if _INTERNAL_PARAMS.match(name) and (not name.endswith("name") or "#" in body):
        return
    # Detect ref-like / cite-like wrapper macros, e.g. \newcommand{\lemref}[1]{Lemma~\ref{#1}}
    if re.search(r"\\(ref|eqref|cref|Cref|autoref)\s*\{#1\}", body) and nargs >= 1:
        info.ref_macros[name] = "eqref" if "\\eqref" in body else "ref"
    if re.search(r"\\cite[pt]?\s*\{#1\}", body) and nargs >= 1:
        info.cite_macros.add(name)
    # Later definitions (renewcommand) overwrite earlier ones, like LaTeX.
    info.macros[name] = MacroDef(name=name, nargs=nargs, has_optional=has_opt, body=body.strip(),
                                 kind=kind, offset=offset, file=file)


def katex_macros(info: PreambleInfo) -> dict[str, str]:
    """Macros safe to hand to KaTeX for rendering statements in the browser."""
    out: dict[str, str] = {}
    for name, md in info.macros.items():
        if md.has_optional or md.nargs > 9:
            continue
        body = _sanitize_macro_body(md.body)
        if md.nargs == 0 and "#" in body:
            continue
        if _INTERNAL_PARAMS.match(name):
            continue
        if _UNSAFE_MACRO.search(body):
            continue
        if any(env.strip() not in KATEX_ENVS for env in re.findall(r"\\begin\s*\{([^{}]*)\}", body)):
            continue
        if len(body) > 400:
            continue
        # KaTeX infers the arity from the #n it sees; keep arguments that sanitizing removed.
        missing = [i for i in range(1, md.nargs + 1) if f"#{i}" not in body]
        if missing:
            body = "".join(f"\\vphantom{{#{i}}}" for i in missing) + body
        out[name] = body
    # Drop macros that (transitively) use a user macro we could not translate.
    changed = True
    while changed:
        changed = False
        for name, body in list(out.items()):
            for ref in re.findall(r"\\[A-Za-z]+", body):
                if ref in info.macros and ref not in out and ref != name:
                    del out[name]
                    changed = True
                    break
    return out


def _parse_title_authors(text: str, info: PreambleInfo):
    m = re.search(r"\\title\s*", text)
    if m:
        i = m.end()
        o = read_optional(text, i)
        if o is not None:
            i = o[2]
        g = read_group(text, i, allow_par=True)
        if g:
            t = re.sub(r"\\(thanks|footnote)\s*\{", r"\\IGNORE{", g[0])
            t = _drop_cmd_args(t, "IGNORE")
            info.title = strip_latex_to_text(t.replace("\\\\", " "))
    authors: list[str] = []
    for m in re.finditer(r"\\author\s*", text):
        i = m.end()
        o = read_optional(text, i)
        if o is not None:
            i = o[2]
        g = read_group(text, i, allow_par=True)
        if not g:
            continue
        a = g[0]
        for cmd in ("thanks", "footnote", "inst", "email", "affiliation", "address", "IEEEauthorblockA",
                    "textsuperscript", "orcidlink", "footnotemark"):
            a = re.sub(r"\\%s\s*\{" % cmd, r"\\IGNORE{", a)
        a = _drop_cmd_args(a, "IGNORE")
        a = re.sub(r"\$[^$]*\$", "", a)
        a = re.sub(r"\\begin\s*\{tabular\}\s*(?:\[[^\]]*\])?\s*\{[^{}]*\}", r"\\and ", a)
        a = re.sub(r"\\end\s*\{tabular\}", "", a)
        for chunk in re.split(r"\\and\b|\\AND\b", a):
            # Lines after the first "\\" are usually affiliations.
            first = re.split(r"\\\\", chunk.strip(), maxsplit=1)[0]
            for part in re.split(r",\s*and\s+|\s+and\s+|,", first):
                p = strip_latex_to_text(part, keep_math=False).strip(" ,.*")
                p = re.sub(r"\\[A-Za-z]+\s*", "", p).strip()
                if p and len(p) < 60 and not re.search(r"@|university|institute|department|dept\.|laborator",
                                                       p, re.I):
                    authors.append(p)
    seen = set()
    info.authors = [a for a in authors if not (a in seen or seen.add(a))]


def _drop_cmd_args(s: str, cmd: str) -> str:
    while True:
        m = re.search(r"\\%s\{" % cmd, s)
        if not m:
            return s
        k = match_brace(s, m.end() - 1)
        if k < 0:
            return s[:m.start()]
        s = s[:m.start()] + s[k + 1:]


def parse_preamble(full_text: str, preamble_end: int, extra_preambles: dict[str, str]) -> PreambleInfo:
    """Parse declarations from the preamble, local .sty files and the body (for late \\newcommand)."""
    info = PreambleInfo()
    m = re.search(r"\\documentclass\s*(?:\[([^\]]*)\])?\s*\{([^{}]+)\}", full_text)
    if m:
        info.documentclass = m.group(2).strip()
        info.class_options = [o.strip() for o in (m.group(1) or "").split(",") if o.strip()]
    preamble = full_text[:preamble_end]
    sources: list[tuple[str, bool, str | None]] = [(t, False, f) for f, t in extra_preambles.items()]
    sources.append((preamble, True, None))
    for text, _, _ in sources:
        for am in _NEWALIASCNT_RE.finditer(text):
            info.counter_alias[am.group(1).strip()] = am.group(2).strip()
    for text, _, _ in sources:
        _parse_newtheorems(text, info)
        _parse_declaretheorems(text, info)
    for text, _, _ in sources:
        for nm in _NUMBERWITHIN_RE.finditer(text):
            info.counter_within[nm.group(1).strip()] = nm.group(2).strip()
        for nm in _ADDTORESET_RE.finditer(text):
            info.counter_within[nm.group(1).strip()] = nm.group(2).strip()
    for text, valid, f in sources:
        _parse_macros(text, info, valid, f)
    # Macros defined in the body (some authors do this).
    body = full_text[preamble_end:]
    body_info = PreambleInfo()
    _parse_macros(body, body_info, False, None)
    for k, v in body_info.macros.items():
        info.macros.setdefault(k, v)
    info.ref_macros.update({k: v for k, v in body_info.ref_macros.items() if k not in info.ref_macros})
    _resolve_theorem_displays(info)
    for text, _, _ in sources:
        _parse_newenv_aliases(text, info)
    _parse_title_authors(full_text, info)
    return info


def _resolve_theorem_displays(info: PreambleInfo):
    """LyX-style names such as \\protect\\theoremname -> 'Theorem' via \\providecommand."""
    for spec in info.theorems.values():
        raw = spec.raw_display or ""
        if "\\" not in raw:
            continue
        s = raw.replace("\\protect", "")
        for _ in range(3):
            def sub(m):
                md = info.macros.get("\\" + m.group(1))
                return md.body if md is not None and md.nargs == 0 else m.group(0)
            s2 = re.sub(r"\\([A-Za-z]+)", sub, s)
            if s2 == s:
                break
            s = s2
        s = re.sub(r"\\inputencoding\s*\{[^{}]*\}", "", s)
        disp = strip_latex_to_text(s, keep_math=False)
        disp = re.sub(r"\\[A-Za-z]+", "", disp).strip()
        if disp:
            spec.display = disp
            spec.kind = classify_kind(disp, spec.env)


def ensure_default_theorem(info: PreambleInfo, env: str) -> TheoremSpec | None:
    """Undeclared but conventional environments (e.g. from llncs/svjour classes)."""
    if env in info.theorems:
        return info.theorems[env]
    if env in DEFAULT_ENV_NAMES:
        display = DEFAULT_ENV_NAMES[env]
        spec = TheoremSpec(env=env, display=display, kind=classify_kind(display, env), counter=env,
                           source="default")
        info.theorems[env] = spec
        return spec
    if env.endswith("*") and env[:-1] in DEFAULT_ENV_NAMES:
        display = DEFAULT_ENV_NAMES[env[:-1]]
        spec = TheoremSpec(env=env, display=display, kind=classify_kind(display, env), counter=None,
                           source="default")
        info.theorems[env] = spec
        return spec
    return None
