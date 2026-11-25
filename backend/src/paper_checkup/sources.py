"""Getting LaTeX sources onto disk safely: uploads (.tex/.zip/.tar.gz/.gz) and arXiv e-prints."""

from __future__ import annotations

import gzip
import io
import re
import shutil
import tarfile
import zipfile
from pathlib import Path

import httpx

MAX_ARCHIVE_BYTES = 60 * 1024 * 1024
MAX_EXTRACTED_BYTES = 250 * 1024 * 1024
MAX_FILES = 3000
ARXIV_ID_RE = re.compile(r"^(?:\d{4}\.\d{4,5}|[a-z][a-z\-]*(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?$")
TEXT_SUFFIXES = {".tex", ".sty", ".cls", ".bbl", ".bib", ".ltx", ".def", ".cfg", ".clo", ".bst"}


class SourceError(ValueError):
    pass


def normalize_arxiv_id(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^(?:https?://)?(?:www\.)?arxiv\.org/(?:abs|pdf|e-print)/", "", s)
    s = re.sub(r"\.pdf$", "", s)
    s = re.sub(r"^arxiv:", "", s, flags=re.I)
    if not ARXIV_ID_RE.match(s):
        raise SourceError(f"不是合法的 arXiv 编号：{s!r}")
    return s


def _safe_target(dest: Path, name: str) -> Path | None:
    name = name.replace("\\", "/")
    if name.startswith("/") or ".." in Path(name).parts:
        return None
    target = (dest / name).resolve()
    if dest.resolve() not in target.parents:
        return None
    return target


def extract_bytes(data: bytes, filename: str, dest: Path) -> None:
    """Write an uploaded/downloaded payload into dest (an empty directory)."""
    dest.mkdir(parents=True, exist_ok=True)
    if len(data) > MAX_ARCHIVE_BYTES:
        raise SourceError("文件过大")
    lower = filename.lower()
    # gzip (arXiv e-prints are gzipped tar or gzipped single .tex)
    if data[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(data)
        except OSError as e:
            raise SourceError(f"无法解压 gzip：{e}") from e
        if len(raw) > MAX_EXTRACTED_BYTES:
            raise SourceError("解压后文件过大")
        if _is_tar(raw):
            _extract_tar(raw, dest)
        else:
            stem = re.sub(r"\.(gz|tgz)$", "", Path(filename).name) or "main"
            if not stem.endswith(".tex"):
                stem = "main.tex"
            (dest / stem).write_bytes(raw)
        return
    if data[:4] == b"PK\x03\x04" or lower.endswith(".zip"):
        _extract_zip(data, dest)
        return
    if _is_tar(data):
        _extract_tar(data, dest)
        return
    suffix = Path(filename).suffix.lower()
    if suffix in TEXT_SUFFIXES:
        t = _safe_target(dest, Path(filename).name)
        if t is None:
            raise SourceError("非法文件名")
        t.write_bytes(data)
        return
    raise SourceError("不支持的文件类型：请上传 .tex、.zip、.tar.gz 或 arXiv 源码包")


def _is_tar(data: bytes) -> bool:
    try:
        with tarfile.open(fileobj=io.BytesIO(data)) as tf:
            tf.next()
        return True
    except (tarfile.TarError, OSError):
        return False


def _extract_tar(data: bytes, dest: Path):
    total = 0
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        members = []
        for m in tf.getmembers():
            if not m.isfile():
                continue
            if _safe_target(dest, m.name) is None:
                continue
            total += m.size
            members.append(m)
            if len(members) > MAX_FILES or total > MAX_EXTRACTED_BYTES:
                raise SourceError("压缩包内文件过多或过大")
        # filter="data" rejects absolute paths, links outside dest and special files.
        tf.extractall(dest, members=members, filter="data")


def _extract_zip(data: bytes, dest: Path):
    total = 0
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > MAX_FILES:
            raise SourceError("压缩包内文件过多")
        for info in infos:
            target = _safe_target(dest, info.filename)
            if target is None:
                continue
            total += info.file_size
            if total > MAX_EXTRACTED_BYTES:
                raise SourceError("解压后文件过大")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)


def flatten_single_subdir(dest: Path) -> Path:
    """If an archive contains a single top-level directory, analyze inside it."""
    entries = [p for p in dest.iterdir() if not p.name.startswith((".", "__MACOSX"))]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return dest


def fetch_arxiv(arxiv_id: str, dest: Path, timeout: float = 90.0) -> Path:
    aid = normalize_arxiv_id(arxiv_id)
    url = f"https://arxiv.org/e-print/{aid}"
    headers = {"User-Agent": "paper-checkup/0.1 (LaTeX structure analysis)"}
    with httpx.Client(follow_redirects=True, timeout=timeout, headers=headers) as client:
        with client.stream("GET", url) as r:
            if r.status_code != 200:
                raise SourceError(f"arXiv 返回 {r.status_code}：{url}")
            buf = io.BytesIO()
            for chunk in r.iter_bytes():
                buf.write(chunk)
                if buf.tell() > MAX_ARCHIVE_BYTES:
                    raise SourceError("arXiv 源码包过大")
    data = buf.getvalue()
    if data[:5] == b"%PDF-":
        raise SourceError("该 arXiv 条目没有提供 LaTeX 源码（只有 PDF）")
    extract_bytes(data, f"{aid.replace('/', '_')}.gz", dest)
    return flatten_single_subdir(dest)


def fetch_arxiv_meta(arxiv_id: str, timeout: float = 30.0) -> dict:
    """Title / authors / abstract from the arXiv API (best effort)."""
    aid = normalize_arxiv_id(arxiv_id)
    try:
        r = httpx.get("https://export.arxiv.org/api/query", params={"id_list": aid}, timeout=timeout,
                      headers={"User-Agent": "paper-checkup/0.1"})
        r.raise_for_status()
    except httpx.HTTPError:
        return {}
    txt = r.text
    entry = txt.split("<entry>", 1)[-1] if "<entry>" in txt else ""

    def tag(name):
        m = re.search(rf"<{name}>(.*?)</{name}>", entry, re.S)
        return re.sub(r"\s+", " ", m.group(1)).strip() if m else None

    authors = re.findall(r"<author>\s*<name>(.*?)</name>", entry, re.S)
    return {"title": tag("title"), "abstract": tag("summary"), "authors": [a.strip() for a in authors],
            "published": tag("published")}
