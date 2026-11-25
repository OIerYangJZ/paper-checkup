"""Low-level, brace-aware scanning helpers for LaTeX source."""

from __future__ import annotations

import re
from dataclasses import dataclass

_WS = " \t\r\n"


def skip_ws(text: str, i: int, allow_par: bool = True) -> int:
    n = len(text)
    newlines = 0
    while i < n and text[i] in _WS:
        if text[i] == "\n":
            newlines += 1
            if newlines >= 2 and not allow_par:
                return i
        i += 1
    return i


def match_brace(text: str, i: int, open_ch: str = "{", close_ch: str = "}") -> int:
    """Given text[i] == open_ch, return the index of the matching close_ch (or -1)."""
    depth = 0
    n = len(text)
    j = i
    while j < n:
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return j
        j += 1
    return -1


def read_group(text: str, i: int, allow_par: bool = False) -> tuple[str, int, int] | None:
    """Read a mandatory argument at/after i. Returns (content, start, end_after)."""
    j = skip_ws(text, i, allow_par=allow_par)
    if j >= len(text):
        return None
    if text[j] == "{":
        k = match_brace(text, j)
        if k < 0:
            return None
        return text[j + 1:k], j + 1, k + 1
    if text[j] == "\\":
        m = re.match(r"\\([A-Za-z@]+|.)", text[j:])
        if m:
            return m.group(0), j, j + len(m.group(0))
    if text[j] not in "}]":
        return text[j], j, j + 1
    return None


def read_optional(text: str, i: int) -> tuple[str, int, int] | None:
    """Read an optional [..] argument (nesting-aware). Returns (content, start, end_after)."""
    j = skip_ws(text, i, allow_par=False)
    if j >= len(text) or text[j] != "[":
        return None
    depth_sq = 0
    depth_br = 0
    k = j
    n = len(text)
    while k < n:
        c = text[k]
        if c == "\\":
            k += 2
            continue
        if c == "{":
            depth_br += 1
        elif c == "}":
            depth_br -= 1
        elif c == "[" and depth_br == 0:
            depth_sq += 1
        elif c == "]" and depth_br == 0:
            depth_sq -= 1
            if depth_sq == 0:
                return text[j + 1:k], j + 1, k + 1
        k += 1
    return None


@dataclass
class Env:
    name: str
    begin: int          # offset of "\begin"
    body_start: int     # offset right after "\begin{name}"
    body_end: int       # offset of "\end{name}" (or end of text if unterminated)
    end: int            # offset right after "\end{name}"
    parent: int | None
    depth: int
    index: int = -1

    def contains(self, offset: int) -> bool:
        return self.body_start <= offset < self.body_end


_BEGIN_END_RE = re.compile(r"\\(begin|end)\s*\{([^{}]+)\}")

# Environments whose contents must not be parsed as LaTeX structure.
VERBATIM_ENVS = {"verbatim", "verbatim*", "lstlisting", "minted", "Verbatim", "comment", "alltt"}


def scan_environments(text: str, start: int = 0, end: int | None = None) -> list[Env]:
    """Build a flat list of environments with parent links, tolerant of imbalance."""
    end = len(text) if end is None else end
    envs: list[Env] = []
    stack: list[int] = []
    skip_until_end: str | None = None
    for m in _BEGIN_END_RE.finditer(text, start, end):
        kind, name = m.group(1), m.group(2).strip()
        if skip_until_end is not None:
            if kind == "end" and name == skip_until_end:
                env = envs[stack.pop()]
                env.body_end = m.start()
                env.end = m.end()
                skip_until_end = None
            continue
        if kind == "begin":
            parent = stack[-1] if stack else None
            env = Env(name=name, begin=m.start(), body_start=m.end(), body_end=end, end=end,
                      parent=parent, depth=len(stack))
            env.index = len(envs)
            envs.append(env)
            stack.append(env.index)
            if name in VERBATIM_ENVS:
                skip_until_end = name
        else:
            # Find the nearest open env with this name.
            for pos in range(len(stack) - 1, -1, -1):
                if envs[stack[pos]].name == name:
                    for idx in stack[pos:]:
                        e = envs[idx]
                        if e.body_end == end:
                            e.body_end = m.start()
                            e.end = m.end()
                    del stack[pos:]
                    break
            # Unmatched \end: ignore.
    return envs


def split_top_level(text: str, sep: str = "\\\\") -> list[tuple[int, int]]:
    """Split text on `sep` occurring at brace depth 0 and outside nested environments.

    Returns a list of (start, end) spans relative to text.
    """
    spans = []
    depth = 0
    env_depth = 0
    i = 0
    last = 0
    n = len(text)
    while i < n:
        c = text[i]
        if text.startswith("\\begin", i):
            env_depth += 1
            i += 6
            continue
        if text.startswith("\\end", i):
            env_depth = max(0, env_depth - 1)
            i += 4
            continue
        if text.startswith(sep, i) and depth == 0 and env_depth == 0:
            spans.append((last, i))
            i += len(sep)
            last = i
            continue
        if c == "\\":
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth = max(0, depth - 1)
        i += 1
    spans.append((last, n))
    return spans


@dataclass
class MathSegment:
    start: int          # offset of the opening delimiter
    end: int            # offset after the closing delimiter
    inner_start: int
    inner_end: int
    display: bool
    env: str | None = None

    def inner(self, text: str) -> str:
        return text[self.inner_start:self.inner_end]


MATH_ENVS = {
    "equation", "equation*", "align", "align*", "alignat", "alignat*", "flalign", "flalign*",
    "gather", "gather*", "multline", "multline*", "eqnarray", "eqnarray*", "displaymath", "math",
    "dmath", "dmath*",
}
NON_TEXT_ENVS = {"tikzpicture", "pgfpicture", "picture", "tikzcd", "xy", "pspicture", "circuitikz",
                 "quantikz", "qcircuit"} | VERBATIM_ENVS


def find_math_segments(text: str, envs: list[Env], start: int = 0, end: int | None = None) -> list[MathSegment]:
    """Find inline and display math in [start, end), skipping verbatim/picture environments."""
    end = len(text) if end is None else end
    segs: list[MathSegment] = []
    # Environment-based display math first.
    skip_ranges: list[tuple[int, int]] = []
    for e in envs:
        if e.begin < start or e.begin >= end:
            continue
        if e.name in MATH_ENVS:
            segs.append(MathSegment(e.begin, e.end, e.body_start, e.body_end, True, e.name))
            skip_ranges.append((e.begin, e.end))
        elif e.name in NON_TEXT_ENVS:
            skip_ranges.append((e.begin, e.end))
    skip_ranges.sort()

    def next_skip(i: int, k: int) -> tuple[int, int]:
        while k < len(skip_ranges) and skip_ranges[k][1] <= i:
            k += 1
        return k, (skip_ranges[k] if k < len(skip_ranges) else (end + 1, end + 1))

    i = start
    k = 0
    k, cur = next_skip(i, k)
    while i < end:
        if cur[0] <= i < cur[1]:
            i = cur[1]
            k, cur = next_skip(i, k)
            continue
        c = text[i]
        if c == "\\":
            nxt = text[i + 1:i + 2]
            if nxt in ("(", "["):
                close = "\\)" if nxt == "(" else "\\]"
                j = text.find(close, i + 2, end)
                if j < 0:
                    i += 2
                    continue
                segs.append(MathSegment(i, j + 2, i + 2, j, nxt == "[", None))
                i = j + 2
                continue
            i += 2
            continue
        if c == "$":
            if text.startswith("$$", i):
                j = text.find("$$", i + 2, end)
                if j < 0:
                    i += 2
                    continue
                segs.append(MathSegment(i, j + 2, i + 2, j, True, None))
                i = j + 2
                continue
            # inline $...$
            j = i + 1
            while j < end:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == "$":
                    break
                if text[j] == "\n" and text[j - 1:j] == "\n":
                    break
                j += 1
            if j < end and text[j] == "$":
                segs.append(MathSegment(i, j + 1, i + 1, j, False, None))
                i = j + 1
                continue
            i += 1
            continue
        i += 1
    segs.sort(key=lambda s: s.start)
    # Remove segments nested inside other segments (e.g. $..$ inside \text{} in display math).
    result: list[MathSegment] = []
    last_end = -1
    for s in segs:
        if s.start < last_end:
            continue
        result.append(s)
        last_end = s.end
    return result


_CMD_RE = re.compile(r"\\([A-Za-z@]+)\*?")


def strip_latex_to_text(s: str, keep_math: bool = True) -> str:
    """Rough LaTeX -> plain text conversion (for titles, quotes, prompts)."""
    s = re.sub(r"\\label\s*\{[^{}]*\}", "", s)
    s = re.sub(r"\\(?:emph|textit|textbf|textrm|textsf|texttt|textsc|text|mbox|underline)\s*\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\\(?:footnote|thanks)\s*\{[^{}]*\}", "", s)
    s = s.replace("~", " ").replace("\\\\", " ").replace("``", '"').replace("''", '"')
    if not keep_math:
        s = re.sub(r"\$[^$]*\$", "", s)
    s = re.sub(r"\\(?:it|bf|em|rm|sf|tt|sc|small|large|Large|normalsize|noindent|medskip|bigskip|smallskip)\b\s*", "", s)
    s = re.sub(r"[{}]", "", s) if not keep_math else s
    s = re.sub(r"[ \t\r\n]+", " ", s)
    return s.strip()


def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()
