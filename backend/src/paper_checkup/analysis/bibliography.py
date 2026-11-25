"""Bibliography: \\bibitem entries (.bbl / thebibliography) and .bib files."""

from __future__ import annotations

import re

from ..latex.scan import match_brace, read_group, read_optional, strip_latex_to_text


def _clean(s: str) -> str:
    s = re.sub(r"\\newblock\s*", " ", s)
    s = re.sub(r"\\(?:em|it|bf|sc|rm|tt|sl)\b\s*", "", s)
    s = re.sub(r"\\(?:url|href)\s*\{([^{}]*)\}(?:\s*\{([^{}]*)\})?", lambda m: m.group(2) or m.group(1), s)
    s = re.sub(r"\\(?:penalty|BIBand|natexlab)\S*", "", s)
    s = strip_latex_to_text(s)
    s = re.sub(r"[{}]", "", s)
    s = re.sub(r"\\([&%$#_])", r"\1", s)
    s = re.sub(r"\\[A-Za-z]+\s*", "", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_bibitems(text: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    items = list(re.finditer(r"\\bibitem\s*", text))
    for idx, m in enumerate(items):
        i = m.end()
        label = None
        o = read_optional(text, i)
        if o is not None:
            label = o[0]
            i = o[2]
        g = read_group(text, i)
        if g is None:
            continue
        key = g[0].strip()
        end = items[idx + 1].start() if idx + 1 < len(items) else len(text)
        endm = re.search(r"\\end\s*\{thebibliography\}", text[g[2]:end])
        if endm:
            end = g[2] + endm.start()
        raw = text[g[2]:end]
        blocks = [b for b in re.split(r"\\newblock", raw)]
        entry_text = _clean(raw)
        title = _clean(blocks[1]) if len(blocks) > 1 else None
        authors = _clean(blocks[0]) if len(blocks) > 1 else None
        lab = None
        if label:
            # natbib: \bibitem[Author(Year)]{key} or alpha: \bibitem[GS13]{key}
            lab = _clean(re.sub(r"\\protect", "", label))
            nm = re.match(r"(.+?)\((\d{4}[a-z]?)\)(.*)", lab)
            if nm:
                lab = f"{nm.group(1).strip()} {nm.group(2)}"
        out[key] = {
            "key": key,
            "label": lab,
            "number": idx + 1,
            "text": entry_text[:400],
            "title": (title or "")[:250] or None,
            "authors": (authors or "")[:200] or None,
        }
    return out


def parse_bib(text: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", text):
        if m.group(1).lower() in ("string", "preamble", "comment"):
            continue
        key = m.group(2)
        start = m.end() - 1
        brace = text.rfind("{", m.start(), m.end())
        end = match_brace(text, brace)
        body = text[m.end():end if end > 0 else len(text)]
        fields = {}
        for fm in re.finditer(r"(\w+)\s*=\s*", body):
            name = fm.group(1).lower()
            j = fm.end()
            if j < len(body) and body[j] == "{":
                k = match_brace(body, j)
                val = body[j + 1:k] if k > 0 else ""
            elif j < len(body) and body[j] == '"':
                k = body.find('"', j + 1)
                val = body[j + 1:k] if k > 0 else ""
            else:
                vm = re.match(r"[^,\n]+", body[j:])
                val = vm.group(0) if vm else ""
            fields.setdefault(name, _clean(val))
        authors = fields.get("author", "")
        short_auth = ", ".join(a.split(",")[0].strip() for a in authors.split(" and ")[:3]) if authors else None
        out[key] = {
            "key": key,
            "label": None,
            "number": None,
            "text": ". ".join(x for x in (authors, fields.get("title"), fields.get("journal") or fields.get("booktitle"),
                                          fields.get("year")) if x)[:400],
            "title": fields.get("title"),
            "authors": short_auth,
            "year": fields.get("year"),
            "eprint": fields.get("eprint"),
        }
    return out


def build_bibliography(full_text: str, files: dict[str, str]) -> dict[str, dict]:
    bib: dict[str, dict] = {}
    # Inline thebibliography or \input of a .bbl is already in full_text.
    if "\\bibitem" in full_text:
        bib.update(parse_bibitems(full_text))
    for name, content in files.items():
        if name.endswith(".bbl") and "\\bibitem" in content:
            for k, v in parse_bibitems(content).items():
                bib.setdefault(k, v)
    for name, content in files.items():
        if name.endswith(".bib"):
            for k, v in parse_bib(content).items():
                if k in bib:
                    for f in ("title", "authors", "year", "eprint"):
                        if not bib[k].get(f) and v.get(f):
                            bib[k][f] = v[f]
                else:
                    bib[k] = v
    # Assign numbers if missing (order of appearance in .bib is not citation order; keep None).
    return bib
