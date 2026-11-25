/**
 * A small LaTeX-fragment -> HTML renderer for theorem statements and proofs.
 * Math is rendered with KaTeX (using the paper's own \newcommand macros); text-mode commands are
 * converted to HTML; \ref / \eqref / \cite become clickable links. All text is HTML-escaped.
 */
import katex from "katex";
import type { Citation, LabelInfo } from "../types";

export interface RenderCtx {
  macros: Record<string, string>;
  textMacros: Record<string, string>;
  labels: Record<string, LabelInfo>;
  citations: Record<string, Citation>;
  theoremEnvs: Record<string, string>;
  /** node id -> display name; when given, bare ids such as "lem-4" in prose become links */
  nodeNames?: Record<string, string>;
}

const NODE_ID_RE = /(?<![\\{,\w/.-])((?:thm|lem|prop|cor|def|claim|fact|conj|asm|notn|rem|ex|prob|alg|env|eq)-[A-Za-z0-9]+(?:\.[A-Za-z0-9]+)*(?:-\d+)?)(?![\w-])/g;

const MATH_ENVS = new Set([
  "equation", "equation*", "align", "align*", "alignat", "alignat*", "flalign", "flalign*", "gather", "gather*",
  "multline", "multline*", "eqnarray", "eqnarray*", "displaymath", "math", "dmath", "dmath*",
]);
const SKIP_ENVS = new Set([
  "figure", "figure*", "table", "table*", "tikzpicture", "pgfpicture", "picture", "tikzcd", "algorithm",
  "algorithm*", "algorithmic", "lstlisting", "verbatim", "wrapfigure", "tabular", "tabular*", "thebibliography",
  "comment", "quantikz", "qcircuit",
]);
const LIST_ENVS: Record<string, string> = { itemize: "ul", enumerate: "ol", description: "ul", compactenum: "ol", "enumerate*": "ol" };
const PROOF_ENVS = new Set(["proof", "proof*", "pf", "Proof", "IEEEproof", "proofsketch"]);
const REF_CMDS = new Set(["ref", "eqref", "cref", "Cref", "autoref", "Autoref", "vref", "Vref", "nameref", "labelcref", "subref", "thmref"]);
const CITE_CMDS = new Set(["cite", "citep", "citet", "citealp", "citealt", "Cite", "Citep", "Citet", "parencite", "textcite", "autocite", "footcite", "citenum", "supercite"]);
const DROP_WITH_ARG = new Set(["label", "index", "vspace", "hspace", "vspace*", "hspace*", "phantom", "hphantom", "vphantom", "setlength", "addtolength", "setcounter", "addtocounter", "pagestyle", "thispagestyle", "bibliographystyle", "bibliography", "includegraphics", "input", "include", "newline", "linebreak", "nopagebreak", "pagebreak", "enlargethispage"]);
const DROP_NOARG = new Set(["noindent", "medskip", "bigskip", "smallskip", "par", "newpage", "clearpage", "centering", "qed", "qedhere", "hfill", "vfill", "null", "maketitle", "small", "footnotesize", "normalsize", "large", "Large", "LARGE", "scriptsize", "tiny", "relax", "protect", "indent", "displaystyle", "allowbreak", "nobreak", "sloppy", "raggedright", "item", "leavevmode", "xspace", "unskip", "ignorespaces"]);
const SYMBOLS: Record<string, string> = {
  S: "§", P: "¶", ldots: "…", dots: "…", textellipsis: "…", LaTeX: "LaTeX", TeX: "TeX", copyright: "©",
  dag: "†", ddag: "‡", textbullet: "•", textendash: "–", textemdash: "—", ss: "ß", ae: "æ", oe: "œ", o: "ø", l: "ł",
  i: "ı", quad: " ", qquad: "  ", enspace: " ", textasciitilde: "~", textbackslash: "\\",
  slash: "/", ensp: " ", lq: "‘", rq: "’", textquoteleft: "‘", textquoteright: "’", today: "",
};
const ACCENTS: Record<string, string> = { "'": "́", "`": "̀", '"': "̈", "^": "̂", "~": "̃", c: "̧", v: "̌", u: "̆", "=": "̄", ".": "̇", H: "̋" };

const mathCache = new Map<string, string>();

export function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function prepMath(tex: string, env: string | null, ctx: RenderCtx): string {
  let t = tex
    .replace(/\\label\s*\{[^{}]*\}/g, "")
    .replace(/\\(nonumber|notag)\b/g, "")
    .replace(/\\tag\*?\s*\{[^{}]*\}/g, "")
    .replace(/\\mbox\s*\{/g, "\\text{")
    .replace(/\\bm\s*\{/g, "\\boldsymbol{")
    .replace(/\\mathds\s*\{/g, "\\mathbb{")
    .replace(/\\intertext\s*\{([^{}]*)\}/g, "\\text{$1}\\\\")
    .replace(/\\begin\s*\{([bpvBV])smallmatrix\}/g, (_m, k: string) => `\\left${OPEN[k]}\\begin{smallmatrix}`)
    .replace(/\\end\s*\{([bpvBV])smallmatrix\}/g, (_m, k: string) => `\\end{smallmatrix}\\right${CLOSE[k]}`)
    .replace(/\\begin\s*\{(array|tabular)\}\s*(\[[a-z]\])?\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}/g, (_m, env: string, _pos, spec: string) =>
      `\\begin{array}{${spec.replace(/[@!<>]\{[^{}]*\}/g, "").replace(/[pmb]\{[^{}]*\}/g, "l").replace(/\*\{(\d+)\}\{([^{}]*)\}/g, (_x, n: string, c: string) => c.repeat(Number(n)))}}`.replace(env === "tabular" ? "" : "", ""),
    )
    .replace(/\\(eq)?ref\s*\{([^{}]*)\}/g, (_m, eq, keys: string) => {
      const out = keys.split(",").map((k) => {
        const li = ctx.labels[k.trim()];
        const d = li ? li.display : "??";
        const num = d.includes(" ") ? d.split(" ").slice(1).join(" ") : d;
        const bare = num.replace(/^\((.*)\)$/, "$1");
        return eq ? `(${bare})` : bare;
      });
      return `\\text{${out.join(", ")}}`;
    });
  const base = env ? env.replace(/\*$/, "") : null;
  if (base === "align" || base === "flalign" || base === "eqnarray") {
    if (base === "eqnarray") t = t.replace(/&\s*([=<>]|\\[a-z]+)\s*&/g, "&$1 ");
    t = `\\begin{aligned}${t}\\end{aligned}`;
  } else if (base === "alignat") {
    const m = t.match(/^\s*\{(\d+)\}/);
    t = m ? `\\begin{alignedat}{${m[1]}}${t.slice(m[0].length)}\\end{alignedat}` : `\\begin{aligned}${t}\\end{aligned}`;
  } else if (base === "gather" || base === "multline") {
    t = `\\begin{gathered}${t}\\end{gathered}`;
  }
  return t.trim();
}

const OPEN: Record<string, string> = { b: "[", p: "(", v: "|", B: "\\{", V: "\\|" };
const CLOSE: Record<string, string> = { b: "]", p: ")", v: "|", B: "\\}", V: "\\|" };
const PICTURE_IN_MATH = /\\begin\s*\{(qcircuit|tikzpicture|tikzcd|xy|picture|quantikz|circuitikz)\}|\\Qcircuit|\\xymatrix|\\includegraphics|\\pgfmathparse|\\draw\b|\\foreach\b|\\tikz\b/;
const KATEX_EXTRA_MACROS: Record<string, string> = {
  "\\lefteqn": "\\mathrlap{#1}",
  "\\eqqcolon": "=\\mathrel{\\mathop:}",
  "\\mathds": "\\mathbb{#1}",
  "\\bm": "\\boldsymbol{#1}",
  "\\textsc": "\\text{#1}",
  "\\dfrac": "\\frac{#1}{#2}",
};

export function renderMath(tex: string, display: boolean, ctx: RenderCtx, env: string | null = null): string {
  if (PICTURE_IN_MATH.test(tex)) return '<span class="env-skip">［电路图 / 图示，见原文］</span>';
  const key = (display ? "D" : "I") + (env ?? "") + "\u0001" + tex;
  const hit = mathCache.get(key);
  if (hit !== undefined) return hit;
  let html: string;
  try {
    html = katex.renderToString(prepMath(tex, env, ctx), {
      displayMode: display,
      throwOnError: false,
      strict: "ignore",
      trust: false,
      macros: { ...KATEX_EXTRA_MACROS, ...ctx.macros },
      maxExpand: 2000,
      output: "html",
    });
  } catch {
    html = `<code class="math-fallback">${escapeHtml(tex)}</code>`;
  }
  if (mathCache.size > 5000) mathCache.clear();
  mathCache.set(key, html);
  return html;
}

// --------------------------------------------------------------------------------------------

function matchBrace(s: string, i: number): number {
  let depth = 0;
  for (let j = i; j < s.length; j++) {
    const c = s[j];
    if (c === "\\") {
      j++;
      continue;
    }
    if (c === "{") depth++;
    else if (c === "}") {
      depth--;
      if (depth === 0) return j;
    }
  }
  return -1;
}

function skipWs(s: string, i: number): number {
  while (i < s.length && (s[i] === " " || s[i] === "\t" || s[i] === "\n")) i++;
  return i;
}

function readArg(s: string, i: number): [string, number] | null {
  const j = skipWs(s, i);
  if (s[j] !== "{") return null;
  const k = matchBrace(s, j);
  if (k < 0) return null;
  return [s.slice(j + 1, k), k + 1];
}

function readOpt(s: string, i: number): [string, number] | null {
  const j = skipWs(s, i);
  if (s[j] !== "[") return null;
  let depth = 0;
  for (let k = j; k < s.length; k++) {
    if (s[k] === "{") depth++;
    else if (s[k] === "}") depth--;
    else if (s[k] === "]" && depth === 0) return [s.slice(j + 1, k), k + 1];
  }
  return null;
}

function findEnvEnd(s: string, from: number, name: string): [number, number] {
  const re = /\\(begin|end)\s*\{([^{}]+)\}/g;
  re.lastIndex = from;
  let depth = 1;
  let m: RegExpExecArray | null;
  while ((m = re.exec(s))) {
    if (m[2].trim() !== name) continue;
    if (m[1] === "begin") depth++;
    else {
      depth--;
      if (depth === 0) return [m.index, m.index + m[0].length];
    }
  }
  return [s.length, s.length];
}

/** Replace math with placeholders; returns the masked text and the rendered math. */
function extractMath(s: string, ctx: RenderCtx): [string, string[]] {
  const out: string[] = [];
  const rendered: string[] = [];
  let i = 0;
  let last = 0;
  const push = (html: string, end: number, start: number) => {
    out.push(s.slice(last, start));
    out.push(`\uE000${rendered.length}\uE001`);
    rendered.push(html);
    last = end;
    i = end;
  };
  while (i < s.length) {
    const c = s[i];
    if (c === "\\") {
      const nx = s[i + 1];
      if (nx === "(" || nx === "[") {
        const close = nx === "(" ? "\\)" : "\\]";
        const j = s.indexOf(close, i + 2);
        if (j > 0) {
          push(renderMath(s.slice(i + 2, j), nx === "[", ctx), j + 2, i);
          continue;
        }
      }
      if (s.startsWith("\\begin", i)) {
        const m = /^\\begin\s*\{([^{}]+)\}/.exec(s.slice(i, i + 60));
        if (m && MATH_ENVS.has(m[1].trim())) {
          const env = m[1].trim();
          const bodyStart = i + m[0].length;
          const [es, ee] = findEnvEnd(s, bodyStart, env);
          push(renderMath(s.slice(bodyStart, es), env !== "math", ctx, env), ee, i);
          continue;
        }
      }
      i += 2;
      continue;
    }
    if (c === "$") {
      if (s[i + 1] === "$") {
        const j = s.indexOf("$$", i + 2);
        if (j > 0) {
          push(renderMath(s.slice(i + 2, j), true, ctx), j + 2, i);
          continue;
        }
        i += 2;
        continue;
      }
      let j = i + 1;
      while (j < s.length) {
        if (s[j] === "\\") {
          j += 2;
          continue;
        }
        if (s[j] === "$") break;
        j++;
      }
      if (j < s.length) {
        push(renderMath(s.slice(i + 1, j), false, ctx), j + 1, i);
        continue;
      }
    }
    i++;
  }
  out.push(s.slice(last));
  return [out.join(""), rendered];
}

class TextRenderer {
  private ctx: RenderCtx;

  constructor(ctx: RenderCtx) {
    this.ctx = ctx;
  }

  render(s: string): string {
    return this.parse(s, 0, false)[0];
  }

  private refHtml(cmd: string, keys: string): string {
    return keys
      .split(",")
      .map((k) => k.trim())
      .filter(Boolean)
      .map((k) => {
        const li = this.ctx.labels[k];
        if (!li) return `<span class="ref ref-missing" title="未定义的标签 ${escapeHtml(k)}">??</span>`;
        let text = li.display;
        if (cmd === "ref" || cmd === "vref" || cmd === "subref") {
          text = li.display.includes(" ") ? li.display.split(" ").slice(1).join(" ") : li.display;
        }
        if (li.kind === "equation") {
          // \ref{eq} prints the bare number (authors write "(\ref{eq})"); only \eqref adds parentheses.
          const bare = text.replace(/^\((.*)\)$/, "$1");
          text = cmd === "eqref" ? `(${bare})` : bare;
        }
        const attr = li.node_id ? ` data-node="${escapeHtml(li.node_id)}"` : li.section_id ? ` data-section="${escapeHtml(li.section_id)}"` : "";
        return `<a class="ref"${attr} title="${escapeHtml(k)}">${escapeHtml(text)}</a>`;
      })
      .join(", ");
  }

  private citeHtml(keys: string, note: string | null): string {
    const parts = keys
      .split(",")
      .map((k) => k.trim())
      .filter(Boolean)
      .map((k) => {
        const c = this.ctx.citations[k];
        const lab = c?.label || (c?.number ? String(c.number) : k);
        const title = c ? [c.authors, c.title || c.text].filter(Boolean).join(" · ") : k;
        return `<span class="cite" data-cite="${escapeHtml(k)}" title="${escapeHtml(title)}">${escapeHtml(lab)}</span>`;
      });
    return `[${parts.join(", ")}${note ? ", " + this.render(note) : ""}]`;
  }

  private envHtml(name: string, body: string, opt: string | null): string {
    if (SKIP_ENVS.has(name)) {
      const what = name.startsWith("table") || name.startsWith("tabular") ? "表格" : name.startsWith("algorithm") ? "算法" : "图";
      return `<span class="env-skip">［${what}，见原文］</span>`;
    }
    if (LIST_ENVS[name]) {
      const tag = LIST_ENVS[name];
      const items = body.split(/\\item\b/).slice(1);
      const lis = items.map((it) => {
        let label = "";
        const o = readOpt(it, 0);
        if (o) {
          label = `<span class="item-label">${this.render(o[0])}</span> `;
          it = it.slice(o[1]);
        }
        return `<li>${label}${this.render(it)}</li>`;
      });
      return `<${tag}>${lis.join("")}</${tag}>`;
    }
    if (PROOF_ENVS.has(name)) {
      return `<div class="nested-proof"><em>证明${opt ? "（" + this.render(opt) + "）" : ""}.</em> ${this.render(body)} <span class="qed">∎</span></div>`;
    }
    const display = this.ctx.theoremEnvs[name];
    if (display) {
      const lab = /\\label\s*\{([^{}]*)\}/.exec(body);
      const li = lab ? this.ctx.labels[lab[1]] : undefined;
      const head = li ? `<a class="ref" data-node="${escapeHtml(li.node_id ?? "")}">${escapeHtml(li.display)}</a>` : escapeHtml(display);
      return `<div class="nested-block"><strong>${head}</strong>${opt ? " （" + this.render(opt) + "）" : ""}. ${this.render(body)}</div>`;
    }
    if (name === "quote" || name === "quotation") return `<blockquote>${this.render(body)}</blockquote>`;
    if (name === "center") return `<div class="center">${this.render(body)}</div>`;
    return this.render(body);
  }

  private parse(s: string, i: number, stopAtBrace: boolean): [string, number] {
    let out = "";
    while (i < s.length) {
      const c = s[i];
      if (c === "}") {
        if (stopAtBrace) return [out, i + 1];
        i++;
        continue;
      }
      if (c === "{") {
        // declarations at the start of a group: {\em ...} {\bf ...}
        const decl = /^\{\s*\\(em|it|bf|sl|sc|tt|rm|sf|bfseries|itshape|scshape)\b\s*/.exec(s.slice(i, i + 20));
        if (decl) {
          const [inner, j] = this.parse(s, i + decl[0].length, true);
          const tag = { em: "em", it: "em", sl: "em", itshape: "em", bf: "strong", bfseries: "strong", tt: "code", sc: "span", scshape: "span", rm: "span", sf: "span" }[decl[1]] ?? "span";
          const cls = decl[1] === "sc" || decl[1] === "scshape" ? ' class="sc"' : "";
          out += `<${tag}${cls}>${inner}</${tag}>`;
          i = j;
          continue;
        }
        const [inner, j] = this.parse(s, i + 1, true);
        out += inner;
        i = j;
        continue;
      }
      if (c === "\\") {
        const m = /^\\([A-Za-z@]+\*?|.)/.exec(s.slice(i, i + 40));
        if (!m) {
          i++;
          continue;
        }
        const name = m[1];
        let j = i + m[0].length;
        if (name === "\\") {
          out += "<br/>";
          const o = readOpt(s, j);
          i = o ? o[1] : j;
          continue;
        }
        if ("{}%&$_#".includes(name)) {
          out += escapeHtml(name);
          i = j;
          continue;
        }
        if (name === " " || name === "," || name === ";" || name === ":" || name === "!" || name === "/" || name === "@") {
          out += name === "!" || name === "/" || name === "@" ? "" : " ";
          i = j;
          continue;
        }
        if (name === "-") {
          i = j;
          continue;
        }
        if (ACCENTS[name] !== undefined) {
          const a = readArg(s, j);
          const ch = a ? a[0] : s[j] ?? "";
          out += escapeHtml(ch.replace(/\\/g, "")) + ACCENTS[name];
          i = a ? a[1] : j + 1;
          continue;
        }
        if (name === "begin") {
          const a = readArg(s, j);
          if (!a) {
            i = j;
            continue;
          }
          const env = a[0].trim();
          let k = a[1];
          let opt: string | null = null;
          const o = readOpt(s, k);
          if (o && !LIST_ENVS[env]) {
            opt = o[0];
            k = o[1];
          }
          if (env === "tabular" || env === "array") {
            const spec = readArg(s, k);
            if (spec) k = spec[1];
          }
          const [es, ee] = findEnvEnd(s, k, env);
          out += this.envHtml(env, s.slice(k, es), opt);
          i = ee;
          continue;
        }
        if (name === "end") {
          const a = readArg(s, j);
          i = a ? a[1] : j;
          continue;
        }
        const bare = name.replace(/\*$/, "");
        if (REF_CMDS.has(bare)) {
          const a = readArg(s, j);
          if (a) {
            out += this.refHtml(bare, a[0]);
            i = a[1];
            continue;
          }
        }
        if (bare === "hyperref") {
          const o = readOpt(s, j);
          const a = o ? readArg(s, o[1]) : null;
          if (o && a) {
            const li = this.ctx.labels[o[0].trim()];
            out += li?.node_id ? `<a class="ref" data-node="${escapeHtml(li.node_id)}">${this.render(a[0])}</a>` : this.render(a[0]);
            i = a[1];
            continue;
          }
        }
        if (CITE_CMDS.has(bare)) {
          let k = j;
          const notes: string[] = [];
          for (let t = 0; t < 2; t++) {
            const o = readOpt(s, k);
            if (!o) break;
            notes.push(o[0]);
            k = o[1];
          }
          const a = readArg(s, k);
          if (a) {
            out += this.citeHtml(a[0], notes.length ? notes[notes.length - 1] || null : null);
            i = a[1];
            continue;
          }
        }
        if (bare === "pcnode") {
          const a = readArg(s, j);
          if (a) {
            const id = a[0];
            out += `<a class="ref node-ref" data-node="${escapeHtml(id)}">${escapeHtml(this.ctx.nodeNames?.[id] ?? id)}</a>`;
            i = a[1];
            continue;
          }
        }
        if (bare === "nocite") {
          const a = readArg(s, j);
          i = a ? a[1] : j;
          continue;
        }
        const wrap: Record<string, [string, string]> = {
          emph: ["<em>", "</em>"], textit: ["<em>", "</em>"], textsl: ["<em>", "</em>"], textbf: ["<strong>", "</strong>"],
          texttt: ["<code>", "</code>"], textsc: ['<span class="sc">', "</span>"], underline: ["<u>", "</u>"],
          text: ["", ""], textrm: ["", ""], textup: ["", ""], textnormal: ["", ""], textsf: ["", ""], mbox: ["", ""],
          hbox: ["", ""], makebox: ["", ""], fbox: ["", ""], paragraph: ["<strong>", "</strong> "],
          subparagraph: ["<strong>", "</strong> "], textmd: ["", ""], enquote: ["“", "”"],
        };
        if (wrap[bare]) {
          const o = readOpt(s, j);
          const a = readArg(s, o ? o[1] : j);
          if (a) {
            const [inner] = this.parse(a[0], 0, false);
            out += wrap[bare][0] + inner + wrap[bare][1];
            i = a[1];
            continue;
          }
        }
        if (bare === "footnote" || bare === "thanks" || bare === "marginpar") {
          const a = readArg(s, j);
          if (a) {
            const plain = a[0].replace(/\uE000\d+\uE001/g, "…").replace(/\\[a-zA-Z]+/g, "").replace(/[{}$]/g, "");
            out += `<sup class="fn" title="${escapeHtml(plain)}">注</sup>`;
            i = a[1];
            continue;
          }
        }
        if (bare === "url") {
          const a = readArg(s, j);
          if (a) {
            const u = a[0].trim();
            out += /^https?:\/\//.test(u) ? `<a href="${escapeHtml(u)}" target="_blank" rel="noreferrer">${escapeHtml(u)}</a>` : escapeHtml(u);
            i = a[1];
            continue;
          }
        }
        if (bare === "href") {
          const a = readArg(s, j);
          const b = a ? readArg(s, a[1]) : null;
          if (a && b) {
            const u = a[0].trim();
            out += /^https?:\/\//.test(u) ? `<a href="${escapeHtml(u)}" target="_blank" rel="noreferrer">${this.render(b[0])}</a>` : this.render(b[0]);
            i = b[1];
            continue;
          }
        }
        if (DROP_WITH_ARG.has(bare)) {
          const o = readOpt(s, j);
          const a = readArg(s, o ? o[1] : j);
          i = a ? a[1] : j;
          continue;
        }
        if (DROP_NOARG.has(bare)) {
          i = j;
          continue;
        }
        if (SYMBOLS[bare] !== undefined) {
          out += SYMBOLS[bare];
          i = j;
          if (s[i] === " " && /[A-Za-z]/.test(bare)) i++;
          continue;
        }
        // Unknown command: keep the content of a following argument, drop the command itself.
        const a = readArg(s, j);
        if (a && !/^\s/.test(s.slice(j, j + 1))) {
          const [inner] = this.parse(a[0], 0, false);
          out += inner;
          i = a[1];
        } else {
          i = j;
        }
        continue;
      }
      if (c === "~") {
        out += "&nbsp;";
        i++;
        continue;
      }
      if (c === "`") {
        if (s[i + 1] === "`") {
          out += "“";
          i += 2;
        } else {
          out += "‘";
          i++;
        }
        continue;
      }
      if (c === "'") {
        if (s[i + 1] === "'") {
          out += "”";
          i += 2;
        } else {
          out += "’";
          i++;
        }
        continue;
      }
      if (c === "-" && s[i + 1] === "-") {
        if (s[i + 2] === "-") {
          out += "—";
          i += 3;
        } else {
          out += "–";
          i += 2;
        }
        continue;
      }
      if (c === "\n") {
        const m = /^\n[ \t]*\n\s*/.exec(s.slice(i));
        if (m) {
          out += '<span class="para-break"></span>';
          i += m[0].length;
        } else {
          out += " ";
          i++;
        }
        continue;
      }
      if (c === "&") {
        out += " ";
        i++;
        continue;
      }
      out += escapeHtml(c);
      i++;
    }
    return [out, i];
  }
}

function expandTextMacros(s: string, ctx: RenderCtx): string {
  for (const [name, body] of Object.entries(ctx.textMacros)) {
    const re = new RegExp(name.replace(/\\/g, "\\\\") + "(?![A-Za-z])\\s*\\{([^{}]*)\\}", "g");
    s = s.replace(re, (_m, arg: string) => body.replace(/#1/g, arg));
  }
  return s;
}

export function renderLatex(src: string, ctx: RenderCtx): string {
  if (!src) return "";
  const s = expandTextMacros(src.replace(/(^|[^\\])%[^\n]*/g, "$1"), ctx);
  let [masked, maths] = extractMath(s, ctx);
  if (ctx.nodeNames) {
    const names = ctx.nodeNames;
    masked = masked.replace(NODE_ID_RE, (m: string) => (names[m] ? `\\pcnode{${m}}` : m));
  }
  const html = new TextRenderer(ctx).render(masked);
  return html.replace(/\uE000(\d+)\uE001/g, (_m, k: string) => maths[Number(k)] ?? "");
}

/** Inline-only rendering for short labels/titles. */
export function renderInline(src: string, ctx: RenderCtx): string {
  return renderLatex(src, ctx).replace(/<span class="para-break"><\/span>/g, " ");
}
