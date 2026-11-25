"""Command line interface.

    paper-checkup analyze PATH [--out report.json] [--no-llm] [--review-max N]
    paper-checkup fetch ARXIV_ID [--dest DIR]
    paper-checkup demo [--only ID] [--no-llm] [--review-max N]
    paper-checkup path REPORT.json NODE_ID [--verify]
    paper-checkup serve [--host 127.0.0.1] [--port 8000]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

from .settings import DEMO_CONFIG, PAPERS_DIR, PRESET_DATA_DIR, load_env


def _progress_printer():
    last = {"msg": None}

    def cb(ev):
        msg = ev.get("message")
        if msg and msg != last["msg"]:
            last["msg"] = msg
            print(f"  · {msg}", file=sys.stderr, flush=True)
    return cb


def _budget(args):
    from .llm.pricing import Budget
    return Budget(per_job=args.budget) if getattr(args, "budget", None) else None


def cmd_analyze(args):
    from .pipeline import run_pipeline

    t = time.time()
    rep = asyncio.run(run_pipeline(Path(args.path), paper_id=args.id, arxiv_id=args.arxiv_id,
                                   use_llm=not args.no_llm, review_max=args.review_max,
                                   progress=_progress_printer(), use_cache=not args.no_cache,
                                   budget=_budget(args)))
    out = Path(args.out or f"{rep.paper.id}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rep.model_dump_json())
    s = rep.stats
    print(f"完成（{time.time() - t:.1f}s）：{s['nodes']} 个条目，显式依赖 {s['edges_explicit']}，推断依赖 {s['edges_inferred']}，"
          f"符号依赖 {s['edges_symbol']}，风险点 {s['risks']}（审稿对抗 {s['risks_review']}）")
    print(f"LLM 调用 {rep.pipeline.llm_calls} 次（缓存命中 {rep.pipeline.llm_cached}），"
          f"输入 {rep.pipeline.tokens_in} / 输出 {rep.pipeline.tokens_out} tokens，花费约 ${rep.pipeline.cost_usd:.2f}"
          + (f"（{rep.pipeline.budget_note}）" if rep.pipeline.budget_note else ""))
    print(f"报告已写入 {out}")


def cmd_fetch(args):
    from .sources import fetch_arxiv, normalize_arxiv_id

    aid = normalize_arxiv_id(args.arxiv_id)
    dest = Path(args.dest or PAPERS_DIR / "raw" / aid.replace("/", "_") / "src")
    if dest.exists() and any(dest.iterdir()):
        print(f"{dest} 已存在，跳过下载")
        return
    root = fetch_arxiv(aid, dest)
    print(f"已下载到 {root}")


def cmd_demo(args):
    """Build the preset reports shown on the landing page."""
    from .pipeline import run_pipeline
    from .sources import fetch_arxiv, fetch_arxiv_meta

    cfg = json.loads(Path(args.config or DEMO_CONFIG).read_text())
    out_dir = PRESET_DATA_DIR / "papers"
    out_dir.mkdir(parents=True, exist_ok=True)
    index_path = PRESET_DATA_DIR / "index.json"
    index = {e["id"]: e for e in (json.loads(index_path.read_text()) if index_path.exists() else [])}
    for entry in cfg["papers"]:
        if args.only and entry["id"] not in args.only:
            continue
        aid = entry.get("arxiv_id")
        if entry.get("path"):
            root = (DEMO_CONFIG.parent / entry["path"]).resolve()
        else:
            dest = PAPERS_DIR / "raw" / aid.replace("/", "_") / "src"
            if not (dest.exists() and any(dest.iterdir())):
                print(f"下载 arXiv:{aid} …", file=sys.stderr)
                fetch_arxiv(aid, dest)
            root = dest
            subdirs = [p for p in dest.iterdir() if not p.name.startswith(".")]
            if len(subdirs) == 1 and subdirs[0].is_dir():
                root = subdirs[0]
        out_id = f"{entry['id']}--{args.tag}" if args.tag else entry["id"]
        print(f"== {out_id} ({aid or root})", file=sys.stderr)
        t = time.time()
        rep = asyncio.run(run_pipeline(root, paper_id=out_id, arxiv_id=aid, use_llm=not args.no_llm,
                                       review_max=entry.get("review_max", args.review_max),
                                       progress=_progress_printer(), use_cache=not args.no_cache,
                                       budget=_budget(args)))
        if aid and (not rep.paper.abstract or not rep.paper.authors):
            meta = fetch_arxiv_meta(aid)
            rep.paper.abstract = rep.paper.abstract or meta.get("abstract")
            rep.paper.authors = rep.paper.authors or meta.get("authors", [])
        if entry.get("title"):
            rep.paper.title = entry["title"]
        if args.tag:
            models = sorted(set(rep.pipeline.models.values()))
            rep.paper.title = f"{rep.paper.title} · {args.tag}"
        (out_dir / f"{out_id}.json").write_text(rep.model_dump_json())
        index[out_id] = {
            "id": out_id, "title": rep.paper.title, "authors": rep.paper.authors, "arxiv_id": aid,
            "file": f"papers/{out_id}.json",
            "blurb": (f"【对照版本：{', '.join(models)}】" if args.tag else "") + (entry.get("blurb") or ""),
            "stats": rep.stats,
            "llm": rep.pipeline.llm_enabled, "models": rep.pipeline.models,
            "generated_at": rep.pipeline.generated_at,
        }
        s = rep.stats
        units = f"{s['nodes']} 条目" if s['nodes'] or not s.get('sections') else f"{s['sections']} 章节、{s.get('claims', 0)} 论断"
        print(f"   {time.time() - t:.0f}s：{units} / {s['edges_explicit']}+{s['edges_inferred']}+{s['edges_symbol']} 依赖 / "
              f"{s['risks']} 风险点；LLM {rep.pipeline.llm_calls} 次调用（缓存 {rep.pipeline.llm_cached}），"
              f"花费约 ${rep.pipeline.cost_usd:.2f}" + (f"（{rep.pipeline.budget_note}）" if rep.pipeline.budget_note else ""),
              file=sys.stderr)
    order = [e["id"] for e in cfg["papers"]]

    def sort_key(e):
        base = e["id"].split("--")[0]
        return (order.index(base) if base in order else 99, e["id"])
    items = sorted(index.values(), key=sort_key)
    index_path.write_text(json.dumps(items, ensure_ascii=False, indent=2))
    print(f"预置报告已写入 {PRESET_DATA_DIR}")


def cmd_publish(args):
    """Public copy of the preset reports: full LaTeX sources removed (arXiv licences), index kept."""
    src = Path(args.src or PRESET_DATA_DIR)
    out = Path(args.out)
    (out / "papers").mkdir(parents=True, exist_ok=True)   # files are overwritten in place
    index = json.loads((src / "index.json").read_text())
    for entry in index:
        rep = json.loads((src / entry["file"]).read_text())
        before = len(json.dumps(rep, ensure_ascii=False))
        if not args.keep_sources:
            rep["sources"] = {}
            rep["paper"]["sources_redacted"] = True
            # Structure-mode section nodes carry the section's full text: keep only an excerpt.
            for n in rep["nodes"]:
                if n.get("kind") == "section" and len(n.get("statement") or "") > 400:
                    n["statement"] = n["statement"][:400].rstrip() + " …（公开版本只保留开头，全文见 arXiv）"
        (out / entry["file"]).write_text(json.dumps(rep, ensure_ascii=False, separators=(",", ":")))
        after = len(json.dumps(rep, ensure_ascii=False))
        print(f"  {entry['id']}: {before // 1024} KB -> {after // 1024} KB")
    (out / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2))
    print(f"公开版本已写入 {out}")


def cmd_path(args):
    from .analysis.graph import reading_path
    from .models import Report

    rep = Report.model_validate_json(Path(args.report).read_text())
    nodes = {n.id: n for n in rep.nodes}
    if args.node not in nodes:
        sys.exit(f"没有条目 {args.node}；可选：{', '.join(n.id for n in rep.nodes if n.order >= 0)}")
    path = reading_path(nodes, rep.edges, args.node, mode="verify" if args.verify else "understand")
    for i, p in enumerate(path, 1):
        n = nodes[p["id"]]
        name = f"{n.display} {n.number or ''}".strip() if n.kind != "external" else f"[外部] {n.title}"
        read = "陈述+证明" if p["read"] == "statement+proof" else "陈述"
        loc = f"{n.loc.file}:{n.loc.line_start}" if n.loc else ""
        print(f"{i:3d}. {name:28s} 读{read:6s} {loc}")


def cmd_serve(args):
    import uvicorn

    uvicorn.run("paper_checkup.server:app", host=args.host, port=args.port, reload=args.reload)


def main(argv=None):
    load_env()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(prog="paper-checkup", description="论文审稿体检：LaTeX 结构分析与审稿对抗")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("analyze", help="分析一个 LaTeX 项目目录 / .tex 文件")
    a.add_argument("path")
    a.add_argument("--out")
    a.add_argument("--id")
    a.add_argument("--arxiv-id")
    a.add_argument("--no-llm", action="store_true", help="只运行确定性分析")
    a.add_argument("--no-cache", action="store_true", help="不使用 LLM 响应缓存")
    a.add_argument("--review-max", type=int, default=None, help="审稿对抗最多审查多少个证明（默认 30）")
    a.add_argument("--budget", type=float, default=None, help="本次分析的大模型花费上限（美元），默认不限")
    a.set_defaults(func=cmd_analyze)

    f = sub.add_parser("fetch", help="下载 arXiv 论文的 LaTeX 源码")
    f.add_argument("arxiv_id")
    f.add_argument("--dest")
    f.set_defaults(func=cmd_fetch)

    d = sub.add_parser("demo", help="生成首页预置的演示报告（demo/papers.json）")
    d.add_argument("--config")
    d.add_argument("--only", nargs="*")
    d.add_argument("--no-llm", action="store_true")
    d.add_argument("--no-cache", action="store_true")
    d.add_argument("--review-max", type=int, default=None)
    d.add_argument("--budget", type=float, default=None, help="每篇的大模型花费上限（美元），默认不限")
    d.add_argument("--tag", help="保存为对照版本（如 deepseek），不覆盖原报告：papers/<id>--<tag>.json")
    d.set_defaults(func=cmd_demo)

    pb = sub.add_parser("publish", help="生成公开部署用的预置报告（去掉论文完整源码）")
    pb.add_argument("--src", help="默认 frontend/public/data")
    pb.add_argument("--out", default=str(PRESET_DATA_DIR.parents[2] / "site" / "data"))
    pb.add_argument("--keep-sources", action="store_true")
    pb.set_defaults(func=cmd_publish)

    p = sub.add_parser("path", help="打印某个条目的阅读路径")
    p.add_argument("report")
    p.add_argument("node")
    p.add_argument("--verify", action="store_true", help="完全验证模式（递归阅读所有证明）")
    p.set_defaults(func=cmd_path)

    s = sub.add_parser("serve", help="启动 Web 服务")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--reload", action="store_true")
    s.set_defaults(func=cmd_serve)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
