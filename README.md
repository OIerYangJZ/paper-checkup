# 论文审稿体检 · Paper Checkup

上传一篇数学或理论物理论文的 LaTeX 源码，系统像一位审稿人一样把它“体检”一遍：画出定理之间的依赖关系，告诉你看懂某个结论最少要读哪些部分，并标出论证中可能有问题的地方。

没有定理类环境的论文（实验性的 ML / 应用论文、很多物理论文）自动切换到**结构模式**：以章节、图表、编号公式为节点建立依赖图，体检对象换成论文的核心论断——论断是否被论文自己的表格和实验支撑、数字前后是否一致、措辞是否超出证据、比较是否对等。

- **结构提取**：识别定义 / 引理 / 命题 / 定理 / 推论等环境与公式，用 `\label`、`\ref`、`\eqref`、`\cite` 确定性地建立依赖；没有用 `\ref` 标注的隐式引用（“由上一引理可得”）交给大模型补全，图上用虚线区分。生成全文符号表。
- **目标驱动的阅读路径**：点选一个结论，计算最小前置子图并按拓扑顺序给出阅读清单；“看懂证明”模式只要求读前置结论的陈述，“完全验证”模式递归读所有证明；列出论文默认读者已掌握的外部知识。
- **审稿人体检**：作者智能体为每个关键步骤给出依据，审稿人智能体逐条质疑（前提条件是否验证、符号是否一致、结论是否有来源、是否循环），只输出双方无法达成一致或作者承认的问题，附原文位置与完整对抗记录。

设计原则是**能确定的不交给大模型**：显式依赖、环境编号、证明归属、符号首次出现位置、未定义引用、循环依赖由程序解析得到，可复现；大模型只负责理解自然语言中的隐式引用和审稿对抗中的数学判断，并且它给出的每条推断都必须附带能在原文中逐字定位的引文，定位不到的直接丢弃。

本工具不判定论文“对”还是“错”，也不替代同行评审；最终判断权在读者。

---

## 快速开始

需要 Python ≥ 3.13（推荐用 [uv](https://docs.astral.sh/uv/)）和 Node.js ≥ 20。

```bash
# 1. 后端依赖
cd backend
uv sync

# 2. 配置大模型（默认 OpenAI 兼容接口；也支持 Claude）
cp ../.env.example ../.env      # 然后编辑 ../.env，填入 OPENAI_API_KEY / OPENAI_BASE_URL / LLM_MODEL

# 3. 生成首页预置的三篇演示报告（会从 arXiv 下载源码；带缓存，可重复运行）
uv run paper-checkup demo                # 不想调用大模型：加 --no-llm

# 4. 构建前端
cd ../frontend
npm install
npm run build

# 5. 启动（同时提供 API、前端页面与预置报告）
cd ../backend
uv run paper-checkup serve               # 打开 http://127.0.0.1:8000
```

开发前端时可用 `npm run dev`（端口 5173，`/api` 自动代理到 8000）。仓库里只有去掉论文全文的公开报告（`site/data/`）；
本地开发如果没有自己生成的完整报告，可以先 `cp -R site/data frontend/public/data`。

### 部署：前端在 GitHub Pages，分析服务在自己的机器上

```
浏览器 ──► GitHub Pages（静态前端 + site/data 公开报告）
   └──────► https://<机器>.<tailnet>.ts.net/api   （Tailscale Funnel → 本机 127.0.0.1:8000，仅 API）
```

- **前端**：`.github/workflows/pages.yml` 在推送到 `main` 时构建 `frontend/`，用仓库变量 `API_BASE` 作为后端地址，并打包 `site/data/`。
- **公开报告**：`uv run paper-checkup publish` 从完整报告生成 `site/data/`，去掉论文完整 LaTeX 源码（演示论文均为 arXiv 默认许可证，只授权 arXiv 分发全文），“查看原文”改为跳转 arXiv。完整报告 `frontend/public/data/` 不进仓库。
- **后端**（服务器的 `.env`）：
  - `ANALYZE_LLM_TOKEN`：运行大模型阶段需要的访问口令（不填口令仍可做确定性分析）；
  - `SERVE_STATIC=0`：只提供 API，不对外提供带全文的预置报告；
  - `CORS_ORIGINS`：允许的前端域名，如 `https://<用户名>.github.io`。
- **常驻运行**（macOS）：`~/Library/LaunchAgents/com.paper-checkup.api.plist` 运行 `uv run paper-checkup serve --host 127.0.0.1 --port 8000`，
  公网入口用 `tailscale funnel --bg 8000`。日志在 `~/Library/Logs/paper-checkup.log`。

---

## 命令行

```bash
uv run paper-checkup analyze PATH [--out report.json] [--no-llm] [--review-max N]   # 目录、.tex 文件均可
uv run paper-checkup fetch 1212.0506                     # 下载 arXiv 源码到 papers/raw/
uv run paper-checkup demo [--only ID ...] [--no-llm]     # 按 demo/papers.json 生成预置报告
uv run paper-checkup path report.json thm-1 [--verify]   # 在终端打印阅读路径
uv run paper-checkup publish                             # 生成公开部署用的 site/data（去掉论文全文）
uv run paper-checkup serve [--port 8000]
```

上传入口支持 `.tex`、`.zip` / `.tar.gz` 项目包、arXiv 源码包，或直接填 arXiv 编号 / 链接。

## 大模型配置

全部通过环境变量或仓库根目录的 `.env` 配置，见 [`.env.example`](.env.example)。

| 变量 | 说明 |
|---|---|
| `LLM_PROVIDER` | `openai`（默认）\| `deepseek` \| `qwen` \| `moonshot` \| `anthropic` \| `none` |
| `LLM_MODEL` | 所有角色默认使用的模型；可写成 `提供方:模型` 一行切换，如 `deepseek:deepseek-v4-pro` |
| `DEEPSEEK_API_KEY` / `DASHSCOPE_API_KEY` / `MOONSHOT_API_KEY` | 各家的 key，可与 `OPENAI_API_KEY` 同时保留；地址、JSON 模式、输出上限参数按提供方自动设置 |
| `LLM_MODEL_EXTRACT` / `_AUTHOR` / `_REVIEWER` | 按角色覆盖，格式 `model` 或 `provider:model`，例如让审稿人用另一家的模型 |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | OpenAI 兼容接口（DeepSeek、通义千问、Moonshot、本地 vLLM…） |
| `LLM_JSON_MODE` | `json_object`（默认）\| `json_schema`（严格结构化输出）\| `none`（推理模型） |
| `ANTHROPIC_API_KEY` | Claude（也可用 `ant auth login` 的登录凭据）；`LLM_EFFORT` 控制推理强度 |
| `LLM_BUDGET_PER_JOB_USD` / `LLM_BUDGET_PER_DAY_USD` | 网页发起的分析的花费上限（默认每篇 $3、每天 $10；`off` 不限）。命令行用 `--budget` 设单篇上限 |
| `LLM_CONCURRENCY` | 并发请求数（默认 4） |
| `REVIEW_MAX_TARGETS` | 审稿对抗最多审查多少个证明（默认 30，按重要性挑选） |

临时用另一个模型跑一篇对照，不改 `.env`、也不覆盖原报告：

```bash
LLM_MODEL=deepseek:deepseek-v4-pro uv run paper-checkup demo --only kliuchnikov-maslov-mosca-2013 --tag deepseek
```

所有大模型响应都缓存在 `backend/.cache/llm/`（按提示词内容哈希），重复运行不会重复计费；`--no-cache` 可关闭。

**成本参考**：花费按 token 数 × 官方单价估算（`llm/pricing.py`，复用缓存不计费），写在报告的“生成信息”里。KMM 一篇：默认分工（flash + Sol 审稿）约 $1.2，全部 deepseek-flash 约 $0.6（但漏掉了 high 级问题），全部 GPT-6.2 Astra 约 $7。

---

## 架构

```
backend/src/paper_checkup/
  latex/            确定性 LaTeX 解析
    source.py         多文件展开（\input/\include/\import）、注释与 \iffalse 剥离、逐行源码映射
    preamble.py       \newtheorem / \declaretheorem / \newaliascnt / \newenvironment 别名、宏、\numberwithin
    structure.py      章节、定理类环境、证明归属、公式编号（align 逐行、\tag、\nonumber）、label/ref/cite 及上下文
  analysis/         在结构之上的确定性分析
    graph.py          依赖图：\ref/\eqref/\cite、“Proof of …”章节包含、嵌套断言、正文直接写出的编号；SCC、阅读路径
    symbols.py        符号表：定义句式、:=、定义环境中的强调术语、\newcommand 宏；首次出现、定义前使用、多处定义
    checks.py         结构检查：未定义引用、重复标签、循环依赖、无证明结论、记号疑点、省略论证的措辞；
                      结构模式下交叉核对摘要 / 引言 / 结论中的数字与正文、表格
  llm/
    provider.py       可切换 provider（OpenAI 兼容 / Anthropic）、结构化 JSON、磁盘缓存、并发
    context.py        把 LaTeX 渲染成引用已解析的提示词文本；把模型引文映射回源码位置
    prompts.py        提示词与严格 JSON Schema
    stages.py         证明归属、隐式引用、符号含义、符号争议、无来源结论、审稿对抗；结构模式的论断提取、论断审查、数字核对
    pricing.py        各模型单价、单篇 / 每日花费上限
  pipeline.py       编排：确定性阶段 → 大模型阶段（任何阶段失败都保留确定性结果）
  server.py         FastAPI：上传 / arXiv 分析任务、进度轮询、静态托管
frontend/src/       React + Cytoscape（dagre 布局）+ KaTeX（使用论文自己的宏渲染公式）
demo/papers.json    预置演示论文
```

### 证明归属是怎么确定的

按优先级：证明标题中的 `\ref`（`\begin{proof}[Proof of Theorem~\ref{x}]`）→ 标题中的编号或名称（“Proof of the Step Lemma”）→ 证明前一句“…to prove Lemma~\ref{x}.” → 证明首句“We now prove Theorem~\ref{x}.” → 紧随某个陈述之后且中间没有其他证明 →（仍不确定时）交给大模型判断并标注。标题为 “Proof of Theorem~\ref{x}” 的整节视为 x 的证明，节内陈述的引理视为 x 证明的组成部分。

### 审稿对抗

每个被审查的证明经过四轮：作者列出关键步骤与依据（含被引结论前提条件的逐条核对）→ 审稿人只就前提条件、符号、无来源、论证跳跃、循环五类问题质疑，每条必须逐字引用原文 → 作者辩护或承认 → 审稿人裁决。被化解的质疑不输出；未化解的作为风险点，附原文位置与完整对抗记录。没有证明的结论另走一轮“作者在正文中找依据 → 审稿人判断是否充分”，记号疑点也走一轮辩护与裁决。

### 结构模式（没有定理类环境的论文）

定理类环境少于 3 个时启用。节点是摘要、各级章节（`\paragraph` 并入所在小节）、图 / 表 / 算法（按 `\caption` 重建编号）和编号公式；依赖来自 `\ref` / `\eqref`、正文直接写出的 “Table 2” “Fig. 3”，以及“某节用到前面某节引入的记号”。指向后文章节的引用（“见第 6 节”）只算导航；引用某个公式、并在同一段推出新公式的句子算作新公式的推导依据。

程序先交叉核对数字：摘要、引言、结论中“指标 + 数值”与正文中语境相近、同一指标的数值不同的，作为候选（例如 Attention Is All You Need 摘要写英法 BLEU 41.8、第 6.1 节写 41.0、表 2 是 41.8）。大模型阶段：抽取至多 12 条核心论断（原文必须逐字定位，否则丢弃），每条经过“作者给证据链 → 审稿人核对数字、措辞范围、比较是否对等、归因是否有对照 → 作者回应 → 审稿人裁决”，数字候选由审稿人逐条确认或排除，最后统一经主审复核。

## 测试

```bash
cd backend && uv run pytest
```

`tests/fixtures/sample/` 是一篇覆盖各种边界情况的合成论文（共享计数器、附录编号、整节证明、嵌套断言、硬编码编号、循环依赖、未定义引用、`\iffalse`、`\input`）；`test_pipeline_fake_llm.py` 用脚本化的假模型端到端地跑完整条流水线（包括丢弃无法定位的“幻觉”引用）。`tests/fixtures/empirical/` 是一篇没有定理的合成“实验论文”（摘要数字与表格不一致），`test_structure_mode.py` 覆盖结构模式；`test_budget.py` 覆盖计价与花费上限。

## 已知局限

- 编号由程序按 LaTeX 计数器规则重建，覆盖常见写法（共享计数器、`[section]`、`\numberwithin`、`\appendix`、revtex 的罗马数字章节）；非常规的计数器操作可能与编译出的 PDF 不一致。
- 符号表基于句式与 `\newcommand` 检测，会漏掉没有明确定义句的记号；单字母局部变量不列入。
- 审稿对抗的结论是大模型的数学判断，可能有误报和漏报；它提供的是带依据的风险提示，不是形式化验证。
