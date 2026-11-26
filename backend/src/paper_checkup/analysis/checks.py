"""Deterministic structural checks -> risk points (source = "structural")."""

from __future__ import annotations

import re
from collections import defaultdict

from ..latex.preamble import PROVABLE_KINDS
from ..latex.structure import ParsedPaper
from ..models import Edge, Node, Risk, Symbol
from .graph import STRUCT_KINDS, GraphBuilder, loc_of, sentence_around, strongly_connected_components

GAP_PHRASES = [
    r"clearly", r"obviously", r"evidently", r"trivially", r"it is (?:easy|straightforward|not hard|not difficult|routine) to (?:see|check|verify|show)",
    r"one (?:can|may) (?:easily )?(?:show|check|verify|see)", r"it (?:easily )?follows(?: easily| immediately)?",
    r"(?:is|are) (?:left )?to the reader", r"we leave .{0,30} to the reader", r"by a similar argument",
    r"(?:similarly|analogously)", r"without loss of generality", r"(?:a |the )?standard argument",
    r"well[- ]known", r"it is (?:clear|obvious|evident)", r"straightforward", r"routine (?:calculation|computation|check)",
    r"(?:direct|simple|easy) (?:calculation|computation)", r"we omit the (?:proof|details)",
]
_GAP_RE = re.compile(r"\b(?:" + "|".join(GAP_PHRASES) + r")\b", re.I)

# Numeric cross-check (structure mode): a metric, statistic or unit next to a number, e.g. "BLEU score of $41.0$",
# "OR 2.1 (95% CI 1.4–3.0)", "p = 0.03", "5.1$\\sigma$", "125.1 GeV", "yield of 82%".
_METRIC_WORDS = (  # case-insensitive
    # machine learning
    r"BLEU|ROUGE(?:-[12L])?|METEOR|chrF|F1|F-?(?:score|measure)|accuracy|precision|recall|perplexit(?:y|ies)|PPL|"
    r"error\s+rates?|WER|CER|mAP|top-?[15](?:\s+accuracy)?|exact\s+match|FID|PSNR|SSIM|RMSE|MAE|MSE|"
    r"speed-?up|throughput|latency|FLOPs?|parameters|params|"
    # statistics / medicine / social science
    r"p[- ]?values?|odds\s+ratios?|hazard\s+ratios?|relative\s+risks?|risk\s+ratios?|confidence\s+intervals?|"
    r"credible\s+intervals?|sensitivity|specificity|AUROC|AUPRC|incidence|prevalence|mortality|survival|"
    r"effect\s+sizes?|standard\s+deviations?|standard\s+errors?|correlation|coefficients?|elasticit(?:y|ies)|"
    r"percentage\s+points?|basis\s+points?|R-squared|"
    # physics / chemistry / quantum
    r"significance|cross[- ]sections?|lifetimes?|masse?s?|uncertaint(?:y|ies)|yields?|enantiomeric\s+excess|"
    r"selectivit(?:y|ies)|conversion|turnover\s+(?:numbers?|frequenc(?:y|ies))|half[- ]li(?:fe|ves)|band\s*gaps?|"
    r"binding\s+energ(?:y|ies)|efficienc(?:y|ies)|fidelit(?:y|ies)")
_METRIC_ABBR = (  # case-sensitive abbreviations, symbols and units (so that "OR" is not the word "or")
    r"(?<![A-Za-z])(?:a?OR|a?HR|I?RR|CrI|CI|IQR|SD|SEM|SE|AUC|NNT|ICC|BMI|IC50|EC50|K_?[di]|TON|TOF|ee)(?![A-Za-z])|"
    r"(?<![A-Za-z\\])[pP]\s*(?:[<=>≤≥]|\\[lg]eq?\b|\\[lg]t\b)|\\sigma\b|σ|"
    r"(?<![A-Za-z])(?:[kMGT]?eV|Mpc|kpc|fb|pb|nb|[kMGT]?Hz|[nµμm]M|ppm|ppb|kcal/mol|kJ/mol|wt\s?%|mol\s?%)(?![A-Za-z])|"
    r"M_\{?\\odot\}?|M_⊙|\\[Mm]sun\b|°C")
_METRIC_RE = re.compile(f"(?i:\\b(?:{_METRIC_WORDS})\\b)|{_METRIC_ABBR}")
# Not part of a name such as "top-1", "ResNet-50", "ImageNet-1K", nor a sub/superscript or a citation "[12]".
# A hyphen only counts after a letter: "1.4--3.0" and "1.4–3.0" are ranges, "top-1" is a name.
_VALUE_RE = re.compile(r"(?<![\w.\\^_{\[])(?<![A-Za-z]-)(\d+\.\d+|\d+)(?:\s*(\\?%))?(?![\w.\]])")
_CMP_RE = re.compile(r"(<|>|≤|≥|\\leq?|\\geq?|\\lt|\\gt)\s*\$?\s*$")


def _metric_key(m: str) -> str:
    """Normalized metric name: "P <" and "p=" -> "p-value", units / abbreviations kept as written."""
    t = m.strip()
    if re.match(r"[pP]\s*(?:[<=>≤≥]|\\)", t) or re.match(r"(?i)p[- ]?values?", t):
        return "p-value"
    if t in ("σ", "\\sigma") or re.match(r"(?i)standard\s+deviations?|significance", t):
        return "sigma"
    if re.match(r"M_|\\[Mm]sun", t):
        return "solar-mass"
    return re.sub(r"\s+", " ", t.lower()).rstrip("s") if len(t) > 4 else t


def _same_value(a: dict, b: dict) -> bool:
    """Equal up to the coarser of the two precisions ("2" ~ "2.0" ~ "1.98"; "41.8" !~ "41.0"), or compatible
    when one side is a bound ("p < 0.05" and "p = 0.03")."""
    va, vb = a["value"], b["value"]
    ca, cb = a.get("cmp"), b.get("cmp")
    if ca or cb:
        lt = lambda c: c in ("<", "≤") or (c or "").startswith(("\\le", "\\lt"))
        if ca and cb:
            return lt(ca) == lt(cb)
        bound, val, c = (va, vb, ca) if ca else (vb, va, cb)
        return val <= bound if lt(c) else val >= bound
    dec = lambda r: len(r.split(".")[1]) if "." in r else 0
    d = min(dec(a["raw"].rstrip("%")), dec(b["raw"].rstrip("%")))
    return round(va, d) == round(vb, d)
_STOP = set("""the and for with that this from our are was were has have been than which these those such into over
under after before about also more less most best new using used use based model models task tasks score scores
results result achieves achieve achieved reach reaches reported report previous previously state art single""".split())


class Checker:
    def __init__(self, paper: ParsedPaper, gb: GraphBuilder, nodes: dict[str, Node], edges: list[Edge],
                 symbols: list[Symbol]):
        self.p = paper
        self.gb = gb
        self.nodes = nodes
        self.edges = edges
        self.symbols = symbols
        self.risks: list[Risk] = []
        self.gap_hints: dict[str, list[dict]] = defaultdict(list)   # node id -> [{phrase, loc, sentence}]

    def add(self, **kw) -> Risk:
        r = Risk(id=f"S{len(self.risks) + 1}", source="structural", **kw)
        self.risks.append(r)
        return r

    def run(self) -> list[Risk]:
        self.undefined_refs()
        self.duplicate_labels()
        self.cycles()
        self.unsupported()
        self.forward_dependencies()
        self.unowned_proofs()
        self.symbol_flags()
        self.gap_phrases()
        self.numeric_candidates: list[dict] = []
        if getattr(self.gb, "mode", "theorem") == "structure":
            self.numeric_consistency()
        return self.risks

    # ------------------------------------------------------------------
    def undefined_refs(self):
        grouped: dict[str, list[tuple[int, str | None]]] = defaultdict(list)
        for lab, off, nid in self.gb.undefined_refs:
            grouped[lab].append((off, nid))
        for lab, occ in grouped.items():
            off, nid = occ[0]
            self.add(category="reference", severity="medium",
                     title=f"引用了不存在的标签 \\ref{{{lab}}}",
                     description=f"源码中有 {len(occ)} 处引用 `{lab}`，但全文没有对应的 \\label。编译后会显示为“??”，"
                                 f"读者无法确定此处依赖的是哪一个结论（也可能是缺失了某个源文件）。",
                     node_ids=[nid] if nid else [], loc=loc_of(self.p, off, off + 1),
                     quote=sentence_around(self.p.text, off, off + 1), status="flagged")

    def duplicate_labels(self):
        for lab, off in self.p.duplicate_labels:
            self.add(category="reference", severity="low", title=f"标签重复定义：{lab}",
                     description="同一个 \\label 被定义了多次，\\ref 只会指向其中一个，可能导致引用指向错误的结论。",
                     loc=loc_of(self.p, off, off + 1), status="flagged")

    def cycles(self):
        # Sections and floats point at each other freely ("see Table 2" / a caption pointing back); a cycle among
        # them is not circular reasoning, so only theorem-like items are checked.
        ids = [n for n in self.nodes if self.nodes[n].kind not in STRUCT_KINDS]
        idset = set(ids)
        for kinds, sev, label in ((("explicit",), "high", "显式"), (("explicit", "inferred"), "medium", "含推断")):
            pairs = [(e.source, e.target) for e in self.edges if e.kind in kinds and e.via not in ("contained",)
                     and e.source in idset and e.target in idset]
            for comp in strongly_connected_components(ids, pairs):
                if len(comp) < 2:
                    continue
                if label == "含推断":
                    # Skip cycles already reported with explicit edges only.
                    if any(set(comp) <= set(r.node_ids) for r in self.risks if r.category == "cycle"):
                        continue
                comp_sorted = sorted(comp, key=lambda x: self.nodes[x].order)
                names = "、".join(self._name(x) for x in comp_sorted)
                inner = [e for e in self.edges if e.kind in kinds and e.source in comp and e.target in comp]
                detail = "；".join(f"{self._name(e.target)} 用到 {self._name(e.source)}" for e in inner[:8])
                first = self.nodes[comp_sorted[0]]
                self.add(category="cycle", severity=sev, title=f"循环依赖（{label}依赖）：{names}",
                         description=f"这些结论在依赖图中构成环：{detail}。若环上每一步都确实依赖对方，"
                                     f"则论证是循环的；也可能其中一条只是“参见”式引用。",
                         node_ids=comp_sorted, loc=first.loc, status="flagged")

    def unsupported(self):
        for nid, n in self.nodes.items():
            if n.kind not in PROVABLE_KINDS or n.proofs:
                continue
            if "from_literature" in n.flags or n.cites:
                continue
            # Proved later in the text?  e.g. a theorem in the intro restated & proved later.
            has_restatement = any(
                m for m in self.nodes.values()
                if m.id != nid and m.kind == n.kind and m.proofs and _similar(m.statement, n.statement))
            if has_restatement:
                n.flags.append("restated_later")
                continue
            lead = (n.lead_in or "")
            if re.search(r"\\cite", lead[-300:]):
                n.flags.append("cited_in_lead_in")
                continue
            sev = "medium" if n.kind in ("theorem", "proposition", "lemma") else "low"
            self.add(category="unsupported", severity=sev,
                     title=f"{self._name(nid)} 没有找到证明或文献来源",
                     description="没有找到与该结论对应的证明环境、“Proof of …”章节或证明段落，陈述和其前文也没有引用文献。"
                                 "它可能在正文中被非正式地论证，也可能被默认为已知结论；审稿对抗阶段会进一步检查。",
                     node_ids=[nid], loc=n.loc, status="flagged")

    def forward_dependencies(self):
        """Proof of X uses Y where Y appears after X *and* Y's proof uses X (transitively) -> already a cycle.
        Here we only flag forward references in proofs of results whose own dependency is unproven."""
        for e in self.edges:
            if e.kind != "explicit" or e.via in ("cite", "contained", "nested"):
                continue
            s, t = self.nodes.get(e.source), self.nodes.get(e.target)
            if not s or not t or s.order < 0 or t.order < 0:
                continue
            if s.order > t.order and s.kind in PROVABLE_KINDS and not s.proofs and "from_literature" not in s.flags \
                    and e.context == "proof":
                t.flags.append("uses_later_unproved")

    def unowned_proofs(self):
        for pp in self.p.proofs:
            if pp.owner is not None:
                continue
            if pp.source == "section":
                # A section called "Proof of ..." we could not match.
                pass
            self.add(category="structure", severity="info",
                     title="无法确定归属的证明",
                     description="这个证明环境前后没有明确说明它证明的是哪个结论（标题中没有引用，前一个结论已有证明）。"
                                 "大模型阶段会尝试判断其归属。",
                     loc=loc_of(self.p, pp.outer_start, pp.outer_end),
                     quote=re.sub(r"\s+", " ", self.p.text[pp.start:pp.start + 200]).strip(), status="flagged")

    def symbol_flags(self):
        for s in self.symbols:
            if "used_before_definition" in s.flags and s.definitions and s.first_use:
                d = next((d for d in s.definitions if d.scope in ("definition", "prose")), s.definitions[0])
                self.add(category="symbol", severity="low",
                         title=f"符号 ${s.latex}$ 在定义之前被使用" if s.kind != "term" else f"术语“{s.latex}”在定义之前被使用",
                         description=f"首次出现于 {s.first_use.file}:{s.first_use.line_start}，"
                                     f"而定义位于 {d.loc.file}:{d.loc.line_start}。",
                         node_ids=[x for x in (s.first_use_node, s.defined_in_node) if x],
                         loc=s.first_use, quote=s.first_use_context, status="flagged", symbol_id=s.id)
            if "multiple_definitions" in s.flags:
                locs = "；".join(f"{d.loc.file}:{d.loc.line_start}" for d in s.definitions if d.scope != "macro")
                single_letter = s.kind == "symbol" and len(s.latex) == 1
                self.add(category="symbol", severity="info" if single_letter else "low",
                         title=(f"符号 ${s.latex}$ 被多次定义" if s.kind != "term" else f"术语“{s.latex}”被多次定义"),
                         description=f"在多处出现了定义性表述（{locs}）。需要确认这些定义是否一致；"
                                     f"大模型阶段会判断它们是同一含义的重述还是不同含义。",
                         node_ids=[d.node_id for d in s.definitions if d.node_id][:4],
                         loc=s.definitions[0].loc, status="flagged", symbol_id=s.id)

    def gap_phrases(self):
        for b in self.p.blocks:
            nid = b.id
            for pi in b.proofs:
                pp = self.p.proofs[pi]
                seg = self.p.text[pp.start:pp.end]
                for m in _GAP_RE.finditer(seg):
                    off = pp.start + m.start()
                    if len(self.gap_hints[nid]) >= 8:
                        break
                    self.gap_hints[nid].append({
                        "phrase": m.group(0), "loc": loc_of(self.p, off, off + len(m.group(0))).model_dump(),
                        "sentence": sentence_around(self.p.text, off, off + len(m.group(0)))})

    def numeric_consistency(self):
        """The same metric reported with different values in comparable contexts (abstract / introduction /
        conclusion vs. the rest of the paper). Pairing is heuristic, so these are flagged as candidates (low) for
        the LLM stage to confirm or dismiss."""
        p, text, gb = self.p, self.p.text, self.gb
        skip = [(f.env.begin, f.env.end) for f in p.floats] + [(a, z) for a, z, _, _ in p.eq_lines]
        tables = " ".join(text[f.env.begin:f.env.end] for f in p.floats if f.kind == "table")
        top = [n for n in self.nodes.values() if n.kind == "section" and (n.env in ("section", "chapter"))]
        intro = top[0].id if top else None
        concl = next((n.id for n in reversed(top) if re.match(r"(?i)\s*(conclusions?|discussion|summary)", n.title or "")), None)
        headline_units = {"abstract", intro, concl} - {None}

        mentions = []
        for a, z, nid in gb.unit_spans:
            node = self.nodes.get(nid)
            if node is None or node.kind != "section":
                continue
            own_end = min([x for x, _, m in gb.unit_spans if x > a and self.nodes.get(m) and self.nodes[m].kind == "section"] + [z])
            for m in _VALUE_RE.finditer(text, a, own_end):
                off = m.start()
                if any(x <= off < y for x, y in skip):
                    continue
                raw = m.group(1)
                if "." not in raw and not m.group(2) and (len(raw) == 4 and raw[:2] in ("19", "20")):
                    continue   # a year
                before = text[max(a, off - 12):off]
                if re.search(r"(?i)(table|fig\.?|figure|section|sec\.|eq\.?|equation|appendix|layer|step|\\cite\w*\{[^}]*)\s*~?\(?$", before):
                    continue
                sent = sentence_around(text, off, m.end(), limit=320)
                win_lo, win_hi = max(a, off - 60), min(own_end, m.end() + 40)
                metric = None
                best = 10 ** 9
                for mm in _METRIC_RE.finditer(text, win_lo, win_hi):
                    d = abs(mm.start() - off)
                    if d < best:
                        best, metric = d, _metric_key(mm.group(0))
                if metric is None:
                    continue
                cmp_m = _CMP_RE.search(text[max(a, off - 8):off])
                words = re.findall(r"[A-Za-z][A-Za-z-]{2,}", re.sub(r"\\[A-Za-z]+", " ", sent))
                toks = {w.lower() for w in words} - _STOP - {metric.lower()}
                ents = {w.lower() for w in words if ("-" in w and any(c.isupper() for c in w)) or (w.isupper() and len(w) >= 3)}
                ents -= {metric.lower()}
                mentions.append({"value": float(raw), "raw": raw + ("%" if m.group(2) else ""), "metric": metric,
                                 "cmp": cmp_m.group(1) if cmp_m else None,
                                 "off": off, "end": m.end(), "unit": nid, "sent": sent, "toks": toks, "ents": ents,
                                 "headline": nid in headline_units})
        seen = set()
        for b in mentions:
            same = [a for a in mentions if a["headline"] and a["unit"] != b["unit"] and a["metric"] == b["metric"]]
            scored = []
            for a in same:
                sim = len(a["toks"] & b["toks"]) / max(1, min(len(a["toks"]), len(b["toks"])))
                # A shared named entity (dataset, experiment, drug) with some overlap, or a very similar sentence.
                if (sim >= 0.3 and a["ents"] & b["ents"]) or sim >= 0.5:
                    scored.append((sim, a))
            if not scored or any(_same_value(a, b) for _, a in scored):
                continue
            sim, a = max(scored, key=lambda t: t[0])
            # Another number in A's sentence with B's value -> A talks about several quantities; skip.
            if any(_same_value({"value": float(x), "raw": x}, b) for x in re.findall(r"\d+(?:\.\d+)?", a["sent"])):
                continue
            key = tuple(sorted([(a["raw"], a["unit"]), (b["raw"], b["unit"])])) + (b["metric"],)
            if key in seen:
                continue
            seen.add(key)
            in_tab = {v: bool(re.search(r"(?<![\d.])" + re.escape(v.rstrip("%")) + r"(?![\d])", tables)) for v in (a["raw"], b["raw"])}
            tab_note = ""
            if any(in_tab.values()):
                tab_note = "表格中出现了 " + "、".join(v for v, ok in in_tab.items() if ok) + \
                           ("，没有出现 " + "、".join(v for v, ok in in_tab.items() if not ok) if not all(in_tab.values()) else "") + "。"
            la, lb = loc_of(p, a["off"], a["end"]), loc_of(p, b["off"], b["end"])
            metric = {"p-value": "p 值", "sigma": "显著性（σ）", "solar-mass": "质量（M⊙）"}.get(
                b["metric"], b["metric"].upper() if b["metric"] in ("bleu", "wer", "cer", "fid", "ppl", "f1", "mse", "mae", "rmse") else b["metric"])
            r = self.add(category="numeric", severity="low",
                         title=f"{metric} 数值前后不一致：{a['raw']} 与 {b['raw']}",
                         description=f"相近的语境下，同一指标给出了不同的数值：{self._name(a['unit'])}（{la.file}:{la.line_start}）写的是 "
                                     f"{a['raw']}，{self._name(b['unit'])}（{lb.file}:{lb.line_start}）写的是 {b['raw']}。{tab_note}"
                                     f"程序按上下文相似度配对，也可能是两个不同的量（如不同模型规模或设置）；大模型阶段会逐条核对。\n"
                                     f"· {a['sent']}\n· {b['sent']}",
                         node_ids=[a["unit"], b["unit"]], loc=lb, quote=b["sent"], status="flagged")
            self.numeric_candidates.append({"risk_id": r.id, "metric": metric, "a": a["raw"], "b": b["raw"],
                                            "a_loc": la, "b_loc": lb, "a_sent": a["sent"], "b_sent": b["sent"],
                                            "a_unit": a["unit"], "b_unit": b["unit"], "table_note": tab_note})

    def _name(self, nid: str) -> str:
        n = self.nodes[nid]
        return f"{n.display} {n.number}" if n.number else (n.title or n.display)


def _similar(a: str, b: str) -> bool:
    def norm(s):
        s = re.sub(r"\\label\s*\{[^{}]*\}", "", s)
        return re.sub(r"\s+", "", s)[:400]
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    # crude overlap ratio on character trigrams
    ta = {na[i:i + 3] for i in range(len(na) - 2)}
    tb = {nb[i:i + 3] for i in range(len(nb) - 2)}
    if not ta or not tb:
        return False
    return len(ta & tb) / min(len(ta), len(tb)) > 0.85
