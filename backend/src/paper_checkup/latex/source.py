"""Loading a LaTeX project and flattening it into one text with a source map.

Every character of the flattened text can be traced back to (file, line) of the
original sources, which is how all "原文位置" in the report are produced.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass, field
from pathlib import Path

MAX_INPUT_DEPTH = 12

_INPUT_RE = re.compile(
    r"\\(?P<cmd>input|include|subfile|InputIfFileExists)\s*\{(?P<arg>[^{}]+)\}"
    r"|\\(?P<icmd>import|subimport|inputfrom|subinputfrom)\*?\s*\{(?P<dir>[^{}]*)\}\s*\{(?P<arg2>[^{}]+)\}"
    r"|\\input\s+(?P<bare>[A-Za-z0-9_./-]+)"
)
_USEPACKAGE_RE = re.compile(r"\\(?:usepackage|RequirePackage)\s*(?:\[[^\]]*\])?\s*\{([^{}]+)\}")


def read_text_file(path: Path) -> str:
    data = path.read_bytes()
    for enc in ("utf-8", "utf-8-sig"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def strip_comment(line: str) -> str:
    """Remove a LaTeX comment from one line (an unescaped %)."""
    i = 0
    n = len(line)
    while i < n:
        c = line[i]
        if c == "\\":
            i += 2
            continue
        if c == "%":
            return line[:i]
        i += 1
    return line


@dataclass
class FlatDocument:
    """The flattened, comment-stripped document plus a line-level source map."""

    text: str
    line_starts: list[int]
    line_origin: list[tuple[str, int]]
    files: dict[str, str]
    main_file: str
    extra_preambles: dict[str, str] = field(default_factory=dict)

    def loc(self, offset: int) -> tuple[str, int]:
        offset = max(0, min(offset, max(len(self.text) - 1, 0)))
        i = bisect_right(self.line_starts, offset) - 1
        return self.line_origin[max(i, 0)]

    def span(self, start: int, end: int) -> dict:
        f1, l1 = self.loc(start)
        f2, l2 = self.loc(max(start, end - 1))
        if f2 != f1:
            # The span crosses into an included file; report the starting file only.
            l2 = l1
            # Walk back to the last line of the starting file inside the span.
            i = bisect_right(self.line_starts, max(start, end - 1)) - 1
            while i >= 0 and self.line_starts[i] >= start:
                if self.line_origin[i][0] == f1:
                    l2 = self.line_origin[i][1]
                    break
                i -= 1
        return {"file": f1, "line_start": l1, "line_end": max(l1, l2)}


def _blank_disabled_blocks(lines: list[str]) -> list[str]:
    """Blank out \\iffalse ... \\fi and comment environments, keeping line count."""
    out: list[str] = []
    depth_iffalse = 0
    in_comment_env = False
    for line in lines:
        stripped = line.strip()
        if in_comment_env:
            if re.search(r"\\end\s*\{comment\}", line):
                in_comment_env = False
            out.append("")
            continue
        if depth_iffalse:
            depth_iffalse += len(re.findall(r"\\iffalse\b", line))
            if re.search(r"\\fi\b", line):
                depth_iffalse -= len(re.findall(r"\\fi\b", line))
                if depth_iffalse < 0:
                    depth_iffalse = 0
            out.append("")
            continue
        if re.match(r"\\begin\s*\{comment\}", stripped):
            in_comment_env = True
            out.append("")
            continue
        if re.match(r"\\iffalse\b", stripped):
            rest = stripped[len("\\iffalse"):]
            if re.search(r"\\fi\b", rest):
                out.append("")
                continue
            depth_iffalse = 1
            out.append("")
            continue
        out.append(line)
    return out


class ProjectLoader:
    """Loads a LaTeX project directory and flattens the main file."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.files: dict[str, str] = {}
        self._stack: list[str] = []

    # -- file discovery -------------------------------------------------
    def _rel(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except ValueError:
            return path.name

    def _resolve(self, name: str, base_dir: Path) -> Path | None:
        name = name.strip().strip('"')
        candidates = []
        for base in (base_dir, self.root):
            p = (base / name)
            candidates.append(p)
            if not name.endswith((".tex", ".sty", ".cls", ".ltx")):
                candidates.append(base / (name + ".tex"))
        for c in candidates:
            try:
                rc = c.resolve()
            except OSError:
                continue
            # Never escape the project root.
            if self.root not in rc.parents and rc != self.root:
                continue
            if rc.is_file():
                return rc
        return None

    def find_main_file(self) -> Path:
        tex_files = sorted(p for p in self.root.rglob("*.tex") if p.is_file())
        if not tex_files:
            raise FileNotFoundError("没有找到 .tex 文件")
        mains: list[tuple[int, Path]] = []
        for p in tex_files:
            txt = "\n".join(strip_comment(l) for l in read_text_file(p).splitlines())
            if "\\documentclass" in txt and "\\begin{document}" in txt:
                score = len(txt)
                if p.stem.lower() in ("main", "paper", "ms", "article", "manuscript"):
                    score += 10_000_000
                mains.append((score, p))
        if not mains:
            # Fall back: the largest .tex file.
            return max(tex_files, key=lambda p: p.stat().st_size)
        mains.sort(key=lambda t: -t[0])
        return mains[0][1]

    # -- flattening -------------------------------------------------------
    def _file_lines(self, path: Path) -> list[str]:
        rel = self._rel(path)
        if rel not in self.files:
            self.files[rel] = read_text_file(path)
        raw_lines = self.files[rel].split("\n")
        stripped = [strip_comment(l.rstrip("\r")) for l in raw_lines]
        return _blank_disabled_blocks(stripped)

    def _expand(self, path: Path, depth: int, out_lines: list[str], out_origin: list[tuple[str, int]]):
        rel = self._rel(path)
        if depth > MAX_INPUT_DEPTH or rel in self._stack:
            return
        self._stack.append(rel)
        lines = self._file_lines(path)
        for lineno, line in enumerate(lines, start=1):
            self._expand_line(line, rel, lineno, path.parent, depth, out_lines, out_origin)
        self._stack.pop()

    def _expand_line(self, line, rel, lineno, base_dir, depth, out_lines, out_origin):
        pos = 0
        pending = ""
        for m in _INPUT_RE.finditer(line):
            if m.group("cmd") or m.group("bare"):
                target = m.group("arg") or m.group("bare")
                tdir = base_dir
            else:
                target = m.group("arg2")
                tdir = base_dir / (m.group("dir") or "")
            resolved = self._resolve(target, tdir) if target else None
            if resolved is None:
                continue
            pending += line[pos:m.start()]
            if pending.strip():
                out_lines.append(pending)
                out_origin.append((rel, lineno))
            pending = ""
            self._expand(resolved, depth + 1, out_lines, out_origin)
            pos = m.end()
        pending += line[pos:]
        out_lines.append(pending)
        out_origin.append((rel, lineno))

    def load(self, main: Path | None = None) -> FlatDocument:
        main = main or self.find_main_file()
        out_lines: list[str] = []
        out_origin: list[tuple[str, int]] = []
        self._expand(main.resolve(), 0, out_lines, out_origin)
        line_starts = []
        off = 0
        for l in out_lines:
            line_starts.append(off)
            off += len(l) + 1
        text = "\n".join(out_lines)

        # Local packages referenced via \usepackage{name} (name.sty in the project).
        extra: dict[str, str] = {}
        preamble = text.split("\\begin{document}", 1)[0]
        for m in _USEPACKAGE_RE.finditer(preamble):
            for name in m.group(1).split(","):
                name = name.strip()
                if not name:
                    continue
                p = self._resolve(name + ".sty", main.parent)
                if p is not None:
                    rel = self._rel(p)
                    if rel not in self.files:
                        self.files[rel] = read_text_file(p)
                    extra[rel] = "\n".join(strip_comment(l) for l in self.files[rel].split("\n"))
        # Also read any .bbl / .bib files for the bibliography.
        for p in sorted(self.root.rglob("*")):
            if p.is_file() and p.suffix in (".bbl", ".bib"):
                rel = self._rel(p)
                if rel not in self.files:
                    try:
                        self.files[rel] = read_text_file(p)
                    except OSError:
                        pass
        return FlatDocument(
            text=text,
            line_starts=line_starts,
            line_origin=out_origin,
            files=self.files,
            main_file=self._rel(main),
            extra_preambles=extra,
        )


def load_project(root: Path, main: Path | None = None) -> FlatDocument:
    root = Path(root)
    if root.is_file():
        main = root
        root = root.parent
    return ProjectLoader(root).load(main)
