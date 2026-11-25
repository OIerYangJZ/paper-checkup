"""Figure images for the report: the paper's own figure files converted for the browser, or (when a LaTeX
installation is available) the float compiled on its own with the paper's preamble (TikZ, picture, tables).

Everything here is deterministic; nothing is generated. The output directory holds one PNG (or the original
raster / SVG file) per image, named after the float id ("fig-2-1.png", "tab-3.png").
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..latex.scan import read_group
from ..models import FigureInfo
from .graph import loc_of

log = logging.getLogger("paper_checkup.figures")

_INCLUDE_RE = re.compile(r"\\includegraphics\s*\*?\s*(?:\[[^\]]*\])?\s*\{([^{}]+)\}")
_DRAWING_RE = re.compile(r"\\begin\s*\{(?:tikzpicture|picture|pgfpicture|tikzcd|circuitikz|pspicture|xy)\}|"
                         r"\\Qcircuit|\\xymatrix|\\input\s*\{[^{}]*\.(?:tikz|pgf|pstex_t)\}")
_RASTER = {".png", ".jpg", ".jpeg", ".gif", ".svg"}
_TRY_EXT = [".png", ".pdf", ".jpg", ".jpeg", ".eps", ".ps", ".svg", ".gif"]
MAX_FLOATS = 80
COMPILE_TIMEOUT = 60


SANDBOX_EXEC = "/usr/bin/sandbox-exec"


def sandbox_available() -> bool:
    return Path(SANDBOX_EXEC).exists()


def _sandboxed(args: list[str], read: list[str], write: list[str]) -> list[str]:
    """Run `args` under macOS's sandbox: no network, writes only to `write`, and nothing under /Users or the
    temp areas can be read except `read` (TeX's own paranoid mode still lets absolute paths through)."""
    q = lambda p: '"' + os.path.realpath(p).replace("\\", "\\\\").replace('"', '\\"') + '"'
    prof = ("(version 1)(allow default)(deny network*)(deny file-write*)"
            "(allow file-write* " + " ".join(f"(subpath {q(w)})" for w in write) + ' (literal "/dev/null"))'
            '(deny file-read* (subpath "/Users") (subpath "/private/var/folders") (subpath "/private/tmp") (subpath "/Volumes"))'
            "(allow file-read* " + " ".join(f"(subpath {q(r)})" for r in read + write) + ")")
    return [SANDBOX_EXEC, "-p", prof] + args


def _tex_env(src: str, root: Path, home: str) -> dict:
    """A minimal environment for TeX on possibly untrusted input: no inherited variables (API keys live in the
    server's environment), no shell escape, and kpathsea's paranoid mode for reading and writing (no absolute
    paths, no '..', no dot files: only files of the paper itself and of the TeX installation can be read)."""
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin") + ":/Library/TeX/texbin:/opt/homebrew/bin:/usr/local/bin",
            "HOME": home, "TMPDIR": home, "LANG": "C", "TEXINPUTS": f"{src}//:{root}//:", "BIBINPUTS": f"{src}//:",
            "openin_any": "p", "openout_any": "p", "shell_escape": "f"}


def _tool(name: str) -> str | None:
    p = shutil.which(name)
    if p:
        return p
    for d in ("/opt/homebrew/bin", "/usr/local/bin", "/Library/TeX/texbin"):
        if Path(d, name).exists():
            return str(Path(d, name))
    return None


def _run(args: list[str], cwd: Path | None = None, env: dict | None = None, timeout: int = COMPILE_TIMEOUT) -> bool:
    if env is None:   # converters run on uploaded files: do not hand them the server's environment either
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": tempfile.gettempdir(), "LANG": "C"}
    try:
        r = subprocess.run(args, cwd=cwd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           stdin=subprocess.DEVNULL, timeout=timeout)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _wrap(args: list[str], box: tuple[list[str], list[str]] | None) -> list[str]:
    return _sandboxed(args, *box) if box else args


def _pdf_to_png(src: Path, out: Path, dpi: int = 160, box=None) -> bool:
    pdftoppm = _tool("pdftoppm")
    if pdftoppm and _run(_wrap([pdftoppm, "-png", "-r", str(dpi), "-f", "1", "-l", "1", "-singlefile", str(src),
                                str(out.with_suffix(""))], box)):
        return out.exists()
    gs = _tool("gs")
    if gs and _run(_wrap([gs, "-dSAFER", "-dBATCH", "-dNOPAUSE", "-dQUIET", "-sDEVICE=png16m", f"-r{dpi}",
                          "-dFirstPage=1", "-dLastPage=1", "-dTextAlphaBits=4", "-dGraphicsAlphaBits=4",
                          f"-sOutputFile={out}", str(src)], box)):
        return out.exists()
    sips = _tool("sips")   # macOS built-in; renders at the PDF's own size
    if sips and _run(_wrap([sips, "-s", "format", "png", str(src), "--out", str(out)], box)):
        return out.exists()
    return False


def _eps_to_png(src: Path, out: Path, dpi: int = 160, box=None) -> bool:
    gs = _tool("gs")
    return bool(gs) and _run(_wrap([gs, "-dSAFER", "-dBATCH", "-dNOPAUSE", "-dQUIET", "-dEPSCrop", "-sDEVICE=png16m",
                                    f"-r{dpi}", "-dTextAlphaBits=4", "-dGraphicsAlphaBits=4", f"-sOutputFile={out}",
                                    str(src)], box)) and out.exists()


def _graphics_dirs(preamble: str) -> list[str]:
    m = re.search(r"\\graphicspath\s*\{((?:\s*\{[^{}]*\})+)\s*\}", preamble)
    return re.findall(r"\{([^{}]*)\}", m.group(1)) if m else []


def _resolve(root: Path, main_dir: Path, name: str, dirs: list[str]) -> Path | None:
    name = name.strip().strip('"')
    bases = [root / main_dir, root] + [root / main_dir / d for d in dirs] + [root / d for d in dirs]
    for b in bases:
        p = b / name
        if p.suffix.lower() in _TRY_EXT and p.is_file():
            return p
        for ext in _TRY_EXT:
            q = p.with_name(p.name + ext)
            if q.is_file():
                return q
    return None


def _strip_cmd(body: str, cmd: str) -> str:
    """Remove every \\cmd[..]{...} (brace-matched) from a float body."""
    out, i = [], 0
    for m in re.finditer(r"\\" + cmd + r"\s*(?:\[[^\]]*\])?\s*", body):
        if m.start() < i:
            continue
        g = read_group(body, m.end())
        if g is None:
            continue
        out.append(body[i:m.start()])
        i = g[2]
    out.append(body[i:])
    return "".join(out)


def _compile_float(root: Path, main_dir: Path, preamble: str, body: str, out: Path, defs: str = "",
                   aux: str = "", timeout: int = COMPILE_TIMEOUT, sandbox: bool = False) -> bool:
    """Compile the float body on its own page with the paper's preamble, crop, rasterize."""
    pdflatex = _tool("pdflatex")
    if not pdflatex:
        return False
    body = _strip_cmd(_strip_cmd(body, "caption"), "label")
    doc = (preamble.rstrip() + "\n\\begin{document}\n" + defs + "\n\\pagestyle{empty}\\thispagestyle{empty}\n"
           "\\noindent\\begin{minipage}{\\textwidth}\\centering\n" + body + "\n\\end{minipage}\n\\end{document}\n")
    with tempfile.TemporaryDirectory(prefix="pc-fig-") as td:
        td = os.path.realpath(td)
        box = ([str(root)], [td]) if sandbox else None
        tex = Path(td) / "fig.tex"
        tex.write_text(doc)
        if aux:
            (Path(td) / "fig.aux").write_text(aux)   # citation numbers, so \\cite does not print [?]
        src = str(root / main_dir)
        env = _tex_env(src, root, td)
        ok = _run(_wrap([pdflatex, "-interaction=nonstopmode", "-halt-on-error", "-no-shell-escape",
                         "-output-directory", td, str(tex)], box), cwd=Path(td), env=env, timeout=timeout)
        pdf = Path(td) / "fig.pdf"
        if not ok or not pdf.exists():
            return False
        pdfcrop = _tool("pdfcrop")
        cropped = Path(td) / "fig-crop.pdf"
        if pdfcrop and _run(_wrap([pdfcrop, "--margins", "6", str(pdf), str(cropped)], box), cwd=Path(td), env=env,
                            timeout=timeout) and cropped.exists():
            pdf = cropped
        if not _pdf_to_png(pdf, Path(td) / "fig.png", dpi=180, box=box):
            return False
        shutil.copyfile(Path(td) / "fig.png", out)
        return True


def _raw_preamble(ctx) -> str | None:
    """The main file's own preamble (comments kept: comment-only lines matter inside macro definitions)."""
    raw = ctx.flat.files.get(ctx.flat.main_file, "")
    for m in re.finditer(r"\\begin\s*\{document\}", raw):
        line_start = raw.rfind("\n", 0, m.start()) + 1
        if "%" not in raw[line_start:m.start()]:
            return raw[:m.start()]
    return None


_DEF_LINE = re.compile(r"^\s*(?:\\global\s*)?(?:\\long\s*)?\\(?:newcommand|renewcommand|providecommand|def|gdef|"
                       r"DeclareMathOperator|newtheorem|theoremstyle)\b")


def _body_definitions(ctx) -> str:
    """Macro definitions written after \\begin{document} (LyX exports put \\global\\long\\def lines there)."""
    out = []
    for name, raw in ctx.flat.files.items():
        if not name.endswith(".tex"):
            continue
        start = raw.find("\\begin{document}") if name == ctx.flat.main_file else 0
        for line in raw[max(start, 0):].split("\n"):
            code = re.sub(r"(?<!\\)%.*", "", line)
            if _DEF_LINE.match(code) and code.count("{") == code.count("}"):
                out.append(code.strip())
    return "\n".join(dict.fromkeys(out))


def _raw_float_body(ctx, f) -> str | None:
    """The float's body from its original file (not the comment-stripped flattened text)."""
    loc = loc_of(ctx.paper, f.env.begin, f.env.end)
    raw = ctx.flat.files.get(loc.file)
    if raw is None:
        return None
    lines = raw.split("\n")
    seg = "\n".join(lines[loc.line_start - 1:loc.line_end])
    name = re.escape(f.env.name)
    b = re.search(r"\\begin\s*\{" + name + r"\}(?:\s*\[[^\]]*\])?", seg)
    e = list(re.finditer(r"\\end\s*\{" + name + r"\}", seg))
    if not b or not e or e[-1].start() < b.end():
        return None
    return seg[b.end():e[-1].start()]


def build_figures(ctx, out_dir: Path | None = None, render_latex: bool = False,
                  latex_budget_s: float | None = None, sandbox: bool = False) -> list[FigureInfo]:
    """FigureInfo for every float; with out_dir, also write the images there.

    latex_budget_s caps the total time spent compiling floats (web uploads); None means no cap.
    sandbox=True (untrusted uploads): every converter and TeX run inside the OS sandbox; without one, uploaded
    LaTeX is not compiled at all."""
    import time

    started = time.monotonic()
    p, text = ctx.paper, ctx.paper.text
    root = Path(ctx.root).resolve()
    main_dir = Path(ctx.flat.main_file).parent
    preamble = text[:p.body_start]
    dirs = _graphics_dirs(preamble)
    raw_preamble = _raw_preamble(ctx) if render_latex else None
    body_defs = _body_definitions(ctx) if render_latex else ""
    # natbib-shaped \\bibcite entries also work with plain \\cite (the empty groups print nothing).
    aux = "\\relax\n" + "".join(
        f"\\bibcite{{{k}}}{{{{{c.number or c.label or '?'}}}{{}}{{{{}}}}{{{{}}}}}}\n"
        for k, c in ctx.gb.citations.items() if re.fullmatch(r"[^{}\\%#]+", k))
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
    latex_ok = render_latex and raw_preamble is not None and _tool("pdflatex") is not None and \
        (not sandbox or sandbox_available())
    conv_box = ([str(root)], [str(out_dir)]) if sandbox and sandbox_available() and out_dir is not None else None

    # Which nodes refer to which float (\ref to a float label, inside a node's region).
    label_float = {lab: f.id for f in p.floats for lab in f.labels}
    refs: dict[str, list[str]] = {}
    gb = ctx.gb
    for r in p.refs:
        for k in r.keys:
            fid = label_float.get(k)
            if fid is None:
                continue
            dep = gb.block_ids.get(r.block) if r.block is not None else (gb.unit_at(r.offset) if gb.mode == "structure" else None)
            if dep and dep != fid and dep not in refs.setdefault(fid, []):
                refs[fid].append(dep)

    infos: list[FigureInfo] = []
    for f in p.floats[:MAX_FLOATS]:
        body = text[f.env.body_start:f.env.body_end]
        info = FigureInfo(id=f.id, kind=f.kind, number=f.number, caption=(f.caption or "").strip(), labels=list(f.labels),
                          loc=loc_of(p, f.env.begin, f.env.end), section_id=f.section_id,
                          referenced_by=refs.get(f.id, []))
        infos.append(info)
        if out_dir is None or f.kind == "listing":
            continue
        drawing = bool(_DRAWING_RE.search(body))
        graphics = _INCLUDE_RE.findall(body)
        notes = []
        if graphics and not drawing:
            for k, name in enumerate(graphics, 1):
                src = _resolve(root, main_dir, name, dirs)
                if src is None:
                    notes.append(f"找不到图片文件 {name}")
                    continue
                ext = src.suffix.lower()
                if ext in _RASTER:
                    dst = out_dir / f"{f.id}-{k}{ext}"
                    shutil.copyfile(src, dst)
                    info.images.append(dst.name)
                    continue
                dst = out_dir / f"{f.id}-{k}.png"
                ok = _pdf_to_png(src, dst, box=conv_box) if ext == ".pdf" else _eps_to_png(src, dst, box=conv_box)
                if ok:
                    info.images.append(dst.name)
                else:
                    notes.append(f"{src.name}：服务器没有可用的 {'PDF' if ext == '.pdf' else 'EPS'} 转换工具")
            if info.images:
                info.source = "files"
        over_budget = latex_budget_s is not None and time.monotonic() - started > latex_budget_s
        if not info.images and latex_ok and over_budget:
            notes.append("本次分析的 LaTeX 编译时间已用完，其余浮动体没有编译")
        if not info.images and latex_ok and not over_budget:
            dst = out_dir / f"{f.id}.png"
            per = COMPILE_TIMEOUT if latex_budget_s is None else 30
            raw_body = _raw_float_body(ctx, f)
            # Original text first (comment-only lines matter inside macro arguments), then the flattened text.
            attempts = ([(raw_preamble, raw_body)] if raw_body is not None else []) + [(raw_preamble, body)]
            if any(_compile_float(root, main_dir, pre, b, dst, body_defs, aux, timeout=per, sandbox=sandbox)
                   for pre, b in attempts):
                info.images.append(dst.name)
                info.source = "latex"
                notes = []
            else:
                notes.append("用论文的导言区单独编译这个浮动体失败")
        if not info.images:
            if drawing and not latex_ok:
                notes.append("这是用 TikZ / picture 等命令画的图，需要 LaTeX 编译，当前服务器不支持")
            elif f.kind in ("table", "algorithm") and not latex_ok:
                notes.append("表格 / 算法需要 LaTeX 编译才能显示为图片，当前服务器不支持")
        info.note = "；".join(notes) or None
    return infos
