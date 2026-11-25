import { ArrowRight, Cpu, Network, Route, ShieldCheck, Sparkles, Stethoscope, Upload } from "lucide-react";
import { useMemo } from "react";
import { renderInline, type RenderCtx } from "../lib/latex";
import type { Health, PresetEntry, Report } from "../types";
import { UploadPanel } from "./Upload";

const EMPTY_CTX: RenderCtx = { macros: {}, textMacros: {}, labels: {}, citations: {}, theoremEnvs: {} };

function TitleTex({ s }: { s: string }) {
  const html = useMemo(() => renderInline(s, EMPTY_CTX), [s]);
  return <span dangerouslySetInnerHTML={{ __html: html }} />;
}

/** Decorative mini dependency graph for the hero. */
function HeroArt() {
  const node = (x: number, y: number, w: number, label: string, color: string, opts: { bold?: boolean; step?: number; risk?: boolean } = {}) => (
    <g>
      <rect x={x} y={y} width={w} height={30} rx={8} fill={color} fillOpacity={0.1} stroke={opts.risk ? "#d23f3a" : color} strokeWidth={opts.risk ? 2.4 : 1.5} />
      <text x={x + w / 2} y={y + 19.5} textAnchor="middle" fontSize="12.5" fontWeight={opts.bold ? 700 : 600} fill="#1c2333" fontFamily="Inter, PingFang SC, sans-serif">
        {label}
      </text>
      {opts.step ? (
        <g>
          <circle cx={x} cy={y} r={9} fill="#2f8f4e" />
          <text x={x} y={y + 4} textAnchor="middle" fontSize="10.5" fontWeight={700} fill="#fff" fontFamily="Inter, sans-serif">
            {opts.step}
          </text>
        </g>
      ) : null}
      {opts.risk ? (
        <g>
          <circle cx={x + w} cy={y} r={9} fill="#d23f3a" />
          <text x={x + w} y={y + 4.2} textAnchor="middle" fontSize="12" fontWeight={800} fill="#fff" fontFamily="Inter, sans-serif">
            !
          </text>
        </g>
      ) : null}
    </g>
  );
  const edge = (d: string, kind: "solid" | "dashed" | "dotted" = "solid", hl = false) => (
    <path
      d={d}
      fill="none"
      stroke={kind === "dashed" ? "#2f56c8" : kind === "dotted" ? "#b6bfcd" : hl ? "#2f8f4e" : "#64748b"}
      strokeWidth={hl ? 2.2 : 1.5}
      strokeDasharray={kind === "dashed" ? "5 4" : kind === "dotted" ? "2 4" : undefined}
      markerEnd={`url(#arrow-${kind === "dashed" ? "b" : hl ? "g" : "s"})`}
    />
  );
  return (
    <svg viewBox="0 0 440 300" role="img" aria-label="依赖图示意">
      <defs>
        {[
          ["s", "#64748b"],
          ["b", "#2f56c8"],
          ["g", "#2f8f4e"],
        ].map(([id, c]) => (
          <marker key={id} id={`arrow-${id}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill={c} />
          </marker>
        ))}
        <pattern id="dots" width="16" height="16" patternUnits="userSpaceOnUse">
          <circle cx="1.5" cy="1.5" r="1.1" fill="#e6e2d8" />
        </pattern>
      </defs>
      <rect width="440" height="300" rx="12" fill="#f8f7f3" />
      <rect width="440" height="300" rx="12" fill="url(#dots)" />
      {edge("M 92 66 C 92 95, 120 100, 128 118", "solid", true)}
      {edge("M 222 66 C 222 95, 190 100, 182 118", "solid", true)}
      {edge("M 335 66 C 335 100, 320 105, 316 118", "dotted")}
      {edge("M 155 148 C 155 175, 190 185, 205 202", "solid", true)}
      {edge("M 300 148 C 300 175, 260 185, 247 202", "dashed")}
      {edge("M 226 232 L 226 252", "solid")}
      {node(40, 36, 104, "Definition 2.1", "#0f766e", { step: 1 })}
      {node(170, 36, 104, "Definition 2.4", "#0f766e", { step: 2 })}
      {node(290, 36, 92, "Remark 2.5", "#64748b")}
      {node(100, 118, 110, "Lemma 3.2", "#2563eb", { step: 3 })}
      {node(250, 118, 116, "Proposition 3.4", "#7c3aed", { risk: true })}
      {node(168, 202, 116, "Theorem 1.1", "#c2410c", { bold: true, step: 4 })}
      {node(176, 254, 100, "Corollary 4.1", "#b45309")}
      <g fontFamily="Inter, PingFang SC, sans-serif" fontSize="10.5" fill="#475066">
        <rect x="300" y="196" width="128" height="62" rx="8" fill="#fff" stroke="#e5e1d7" />
        <line x1="310" y1="211" x2="330" y2="211" stroke="#64748b" strokeWidth="2" />
        <text x="336" y="214.5">确定依赖</text>
        <line x1="310" y1="227" x2="330" y2="227" stroke="#2f56c8" strokeWidth="2" strokeDasharray="5 4" />
        <text x="336" y="230.5">推断依赖</text>
        <circle cx="320" cy="244" r="5" fill="#2f8f4e" />
        <text x="336" y="247.5">阅读路径</text>
      </g>
    </svg>
  );
}

const FEATURES = [
  {
    icon: Network,
    color: "#2563eb",
    title: "结构提取",
    text: "识别定义、引理、定理与公式，用 \\label / \\ref 确定性地建立依赖；没有标注的隐式引用交给大模型补全并用虚线区分，同时生成全文符号表。",
  },
  {
    icon: Route,
    color: "#2f8f4e",
    title: "目标驱动的阅读路径",
    text: "点选一个你关心的结论，计算最小前置子图，按拓扑顺序给出阅读清单，并标出论文默认你已经掌握的外部知识。",
  },
  {
    icon: ShieldCheck,
    color: "#c2410c",
    title: "审稿人体检",
    text: "作者智能体为每个关键步骤给出依据，审稿人智能体逐条质疑；只输出双方无法达成一致的风险点，附原文位置与完整对抗记录。",
  },
];

export function Home({
  presets,
  health,
  onPreset,
  onOpen,
}: {
  presets: PresetEntry[];
  health: Health | null | undefined;
  onPreset: (id: string) => void;
  onOpen: (r: Report, jobId?: string) => void;
}) {
  // Comparison runs ("<id>--<tag>") are shown on their base paper's card.
  const groups = useMemo(() => {
    const base = presets.filter((p) => !p.id.includes("--"));
    return base.map((b) => ({ base: b, variants: presets.filter((p) => p.id.startsWith(`${b.id}--`)) }));
  }, [presets]);
  const scrollTo = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });

  return (
    <div className="home">
      <section className="hero">
        <div>
          <span className="eyebrow">
            <Stethoscope size={13} /> LaTeX 论文审稿体检
          </span>
          <h1>
            像审稿人一样，
            <br />
            把一篇论文<em>读透</em>
          </h1>
          <p className="lead">
            上传数学或理论物理论文的 LaTeX 源码：画出定理之间的依赖关系，告诉你看懂某个结论最少要读哪些部分，并标出论证中可能有问题的地方。
          </p>
          <div className="hero-actions">
            <button className="btn-primary btn-lg" onClick={() => scrollTo("presets")}>
              查看示例报告 <ArrowRight size={16} />
            </button>
            <button className="btn btn-lg" onClick={() => scrollTo("upload")}>
              <Upload size={16} /> 体检我的论文
            </button>
          </div>
        </div>
        <div className="hero-art">
          <HeroArt />
        </div>
      </section>

      <section className="features">
        {FEATURES.map((f) => (
          <div key={f.title} className="feature">
            <div className="f-icon" style={{ background: f.color + "16", color: f.color }}>
              <f.icon size={20} />
            </div>
            <strong>{f.title}</strong>
            <p>{f.text}</p>
          </div>
        ))}
      </section>

      <section id="presets">
        <div className="section-head">
          <div>
            <h2 className="section-title">预置体检报告</h2>
            <div className="section-sub">量子计算理论方向的三篇 Clifford+T 合成论文，打开即可浏览，不依赖实时调用。</div>
          </div>
        </div>
        {groups.length ? (
          <div className="preset-grid">
            {groups.map(({ base: p, variants }) => (
              <div key={p.id} className="preset card" role="button" tabIndex={0} onClick={() => onPreset(p.id)} onKeyDown={(e) => e.key === "Enter" && onPreset(p.id)}>
                <div className="preset-top">
                  {p.arxiv_id ? <span className="tag tag-grey">arXiv:{p.arxiv_id}</span> : null}
                  {p.llm ? (
                    <span className="tag tag-purple" title="生成该报告所用的模型">
                      <Sparkles size={11} /> {[...new Set(Object.values(p.models).map((m) => m.split(":").pop()))].join(" + ")}
                    </span>
                  ) : (
                    <span className="tag tag-grey">仅确定性分析</span>
                  )}
                </div>
                <div className="preset-title">
                  <TitleTex s={p.title} />
                </div>
                <div className="preset-authors">{p.authors.join(", ")}</div>
                {p.blurb ? <div className="preset-blurb">{p.blurb}</div> : null}
                {variants.length ? (
                  <div className="preset-foot">
                    对照版本：
                    {variants.map((v) => (
                      <button
                        key={v.id}
                        className="variant-chip"
                        onClick={(e) => {
                          e.stopPropagation();
                          onPreset(v.id);
                        }}
                        title={v.blurb ?? ""}
                      >
                        <Cpu size={11} /> {v.id.split("--")[1]} · {v.stats.risks} 风险点
                      </button>
                    ))}
                  </div>
                ) : null}
                <div className="preset-stats">
                  <div className="pstat">
                    <b>{p.stats.nodes}</b>
                    <span>条目</span>
                  </div>
                  <div className="pstat">
                    <b>{p.stats.edges_explicit}</b>
                    <span>确定依赖</span>
                  </div>
                  <div className="pstat">
                    <b>{p.stats.edges_inferred}</b>
                    <span>推断依赖</span>
                  </div>
                  <div className={`pstat ${p.stats.risks ? "risk" : ""}`}>
                    <b>{p.stats.risks}</b>
                    <span>风险点</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="note">
            还没有预置报告。运行 <code>cd backend && uv run paper-checkup demo</code> 生成。
          </div>
        )}
      </section>

      <section id="upload">
        <div className="section-head">
          <div>
            <h2 className="section-title">体检你自己的论文</h2>
            <div className="section-sub">支持单个 .tex、多文件项目包（.zip / .tar.gz），或直接填写 arXiv 编号。</div>
          </div>
        </div>
        <UploadPanel health={health} onOpen={onOpen} />
      </section>

      <section className="principle">
        <div className="p-icon">
          <ShieldCheck size={22} />
        </div>
        <div>
          <h2>能确定的，不交给大模型</h2>
          <p>
            显式依赖、环境编号、证明归属、符号首次出现位置、未定义引用和循环依赖都由程序解析得到，结果稳定、可复现。
            大模型只做两件事：理解自然语言中的隐式引用，以及审稿对抗中的数学判断；它给出的每条推断都必须附带能在原文中逐字定位的引文，定位不到的直接丢弃。
            本工具不判定论文“对”还是“错”，也不替代同行评审——最终判断权在读者。
          </p>
        </div>
      </section>
      <div className="footer">论文审稿体检 · Paper Checkup</div>
    </div>
  );
}
