"""Prompts and JSON schemas for the LLM stages.

Schemas follow the strict structured-output subset (every object has additionalProperties=false and all
properties required), so they work with OpenAI json_schema mode and Claude output_config.format alike.
"""

from __future__ import annotations

S = {"type": "string"}
N = {"type": "number"}
I = {"type": "integer"}
B = {"type": "boolean"}


def obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def arr(item: dict) -> dict:
    return {"type": "array", "items": item}


def enum(*vals: str) -> dict:
    return {"type": "string", "enum": list(vals)}


def nullable(t: dict) -> dict:
    return {"anyOf": [t, {"type": "null"}]}


LANG_NAME = {"zh": "中文", "en": "English"}


def lang_line(lang: str) -> str:
    return f"所有说明性文字请使用{LANG_NAME.get(lang, '中文')}；引用原文时逐字复制，不要翻译。"


# ---------------------------------------------------------------------------
# Stage 1: implicit dependencies + external knowledge
# ---------------------------------------------------------------------------

IMPLICIT_SCHEMA = obj({
    "items": arr(obj({
        "node_id": S,
        "dependencies": arr(obj({
            "target_id": S,
            "quote": S,
            "where": enum("statement", "proof", "lead-in"),
            "confidence": N,
            "reason": S,
        })),
        "external": arr(obj({
            "name": S,
            "quote": S,
            "where": enum("statement", "proof", "lead-in"),
            "description": S,
        })),
    }))
})


def implicit_system(lang: str) -> str:
    return f"""你是一名严谨的数学 / 理论物理论文审读助手，负责补全论文中“隐式引用”的依赖关系。

背景：程序已经通过 \\ref、\\eqref、\\cite 确定性地找出了显式依赖（列在每个条目下）；正文中的 \\ref 已被替换成编号，形如“Lemma 4〔lem-4〕”，〔〕中是条目 id。
程序还通过符号表确定性地处理了“记号依赖”：某条目用到了某个定义中引入的记号或术语。因此你**不需要**、也**不应该**因为某处使用了一个已定义的概念或记号就报告依赖。

你的任务只有两件：
A. dependencies：原文用**指代性的话语**指向论文中某个具体条目、却没有用 \\ref 标注的地方。只有以下几类才算：
   - 指代前文：“the previous / preceding / above lemma”“the last proposition”“this theorem”“the lemma”“the claim”；
   - 按名称引用：“by the Step Lemma”“the Column Lemma”“the main theorem”；
   - 明确指向某个定义：“by definition of weight”“by the definition of reducibility”“by definition”（且上下文能唯一确定是哪个定义）；
   - 指向前文的论证：“as shown above”“by the same argument as in the proof of …”“as before”（且能唯一确定所指条目）；
   - 【引出段落】中“Combining … we obtain the following”“The following is a consequence of …”之类说明该条目由哪些条目推出的句子。
   quote 必须包含这个指代性的词句本身。
B. external：论证中用到、但论文既没有证明也没有引用文献的**有名称的标准结果或非初等事实**，例如“中国剩余定理”“Cayley–Hamilton 定理”“Z[ω] 是欧几里得整环”“Minkowski 定理”。
   不要列出初等的代数或算术运算性质（矩阵乘法封闭、行列式可乘、复数取模等）。name 用该结果的通行名称（可中英文），同一结果只列一次。

硬性规则：
1. quote 必须是给定文本中连续的一段原文（逐字复制，15～150 个字符，可含 LaTeX），不得改写、拼接；找不到原文依据的不要输出。
2. 不要重复“程序已确定的依赖”；target_id 必须是条目目录中存在的 id，且不能是条目自身。
3. confidence ∈ [0,1]：≥0.9 表示指代对象明确无歧义；0.7～0.9 表示很可能；低于 0.7 不要输出。
4. reason 用一句话说明“哪个词句指向了哪个条目”。
5. 对每个给定条目都输出一项（没有发现时返回空数组；大多数条目通常没有隐式引用，这是正常的）。
{lang_line(lang)}"""


OWNER_SCHEMA = obj({
    "proofs": arr(obj({
        "proof_index": I,
        "owner_id": nullable(S),
        "confidence": N,
        "reason": S,
    }))
})


def owner_system(lang: str) -> str:
    return f"""你是论文结构分析助手。下面列出若干程序无法确定归属的证明（proof 环境或证明段落），以及它们前面的一段正文。
请根据证明内容、前文以及条目目录判断每个证明在证明哪一个条目。只有在有明确依据时才给出 owner_id，否则填 null。confidence ∈ [0,1]。
{lang_line(lang)}"""


# ---------------------------------------------------------------------------
# Stage 2: symbols
# ---------------------------------------------------------------------------

SYMBOL_MEANING_SCHEMA = obj({"symbols": arr(obj({"id": S, "meaning": S}))})


def symbol_meaning_system(lang: str) -> str:
    return f"""你是论文记号表的整理者。对给定的每个符号 / 术语，依据它在论文中的定义语境，给出简明含义（不超过 40 个字，可含 LaTeX，用 $…$ 包裹数学）。
若是宏（macro），给出它代表的数学对象而不是宏的写法。若上下文不足以判断，写“未在文中明确定义”。不要编造论文中没有的内容。
{lang_line(lang)}"""


SEV = enum("high", "medium", "low", "info")


def shared_system(lang: str, mode: str = "theorem") -> str:
    """One rule book for every author / reviewer / editor call, so they share one cached prefix.

    mode="structure" (papers without theorem-like environments): claims are checked against the paper's own
    evidence instead of proofs step by step."""
    if mode == "structure":
        return structure_system(lang)
    return f"""你在参与一次论文“审稿体检”：作者智能体为论证给出依据，审稿人智能体逐条质疑，主审复核整理。具体扮演哪个角色、做什么，写在用户消息中论文全文之后。

用户消息的第一部分是论文全文：行首 L<n>| 是源文件行号（“### 文件 …”标出换文件），【id · 名称 开始/结束】标出定理类条目及其证明的边界，正文中的 \\ref 已替换为编号并附〔id〕。

所有角色共同遵守：
1. 善意阅读：按该领域称职读者的理解来读陈述和证明。显然的隐含约定（指数是整数、零或空集等退化情形另行处理、忽略全局相位、记号在上下文中的通常含义）不构成问题。
2. 先检索全文，再下结论：在提出或承认一个问题之前，先在全文中查找它是否已被处理——定义正文、约定、算法与伪代码、附录、脚注、证明草图之后的段落、计算机验证的说明。已在别处处理的，不是问题。
3. 引用原文时逐字复制（可含 LaTeX），并给出行号；条目用 id 指代（如 lem-4）。不得编造论文中没有的内容。
4. 严重程度标准（统一使用）：
   - high：结论本身按其陈述是错的——给出了针对陈述的具体反例，或证明在主要情形下失效且有理由怀疑结论不成立；
   - medium：证明有实质性缺口或错误、需要真正的补证，但结论很可能成立（例如反例只针对证明中的某一步或某个改写，结论本身仍对）；
   - low：笔误、记号、常数或计数的局部错误、可直接修正的表述问题；
   - info：只涉及退化情形或通行约定、或只影响表述的严谨性。
   定为 high 之前先自问：“受影响的是结论本身，还是只是证明？我给出了针对结论的反例吗？”
5. 错误类型（error_type）决定严重程度的上限：
   - typo_or_notation：笔误、记号不一致或局部记号未定义、不等号方向、计数差一、公式中的符号错误——最高 low；
   - pseudocode_or_typesetting：伪代码、算法、表格或排版中的错误，且作者意图从上下文明显可见（如循环嵌套写反、区间漏写省略号）——最高 medium；若意图本身不清楚、或按意图执行仍然出错，应归为 logic；
   - degenerate_or_convention：只涉及退化情形（零、空集、平凡参数）或通行约定——最高 info；
   - logic：论证或结论本身的问题——按标准定级。
6. 不要提出文风、动机、可推广性之类的意见。
{lang_line(lang)}"""


AUTHOR_ROLE = """## 你的角色：论文作者
你熟悉全文。为论证给出依据：每个关键步骤用到了哪个定义 / 引理 / 公式 / 外部结果，被引用结论的前提条件在此处如何满足。
辩护时可以引用全文任何位置的内容。只有当问题在称职读者的理解下、在主要情形中确实存在时才承认（concede）；
若问题真实但只是笔误、退化情形或不影响结论的局部疏漏，回答 minor 并说明为何不影响结论；若论文已有依据，回答 defend 并给出原文。"""

REVIEWER_ROLE = """## 你的角色：审稿人
严格但公正。只关注：precondition（引用结论时前提是否在引用处得到验证）、symbol（同一符号含义不一致、未定义即使用）、unsupported（没有证明也没有来源的结论）、gap（被一笔带过但确实非平凡、读者难以自行补全的步骤）、circular（循环论证）。
每个质疑必须指向原文中具体的一句（逐字引用并给出行号），具体说明缺了什么，并按统一标准定级。两类都要报告：
(1) 实质问题（error_type=logic，或意图明显的 pseudocode_or_typesetting）；
(2) 投稿前应改正的局部错误（error_type=typo_or_notation）：按原文确实写错的地方，例如笔误、记号前后不一致或未定义、不等号方向、计数差一、公式中的符号或下标错误。
不要报告退化情形和通行约定类问题，除非论证确实依赖该退化情形（此时至多一条，error_type=degenerate_or_convention）。没有问题时返回空列表。"""

EDITOR_ROLE = """## 你的角色：主审（领域主编）
审稿对抗已经结束，下面是所有未被化解的质疑。你的任务是复核与整理，而不是重新审稿：
1. 合并：几条质疑说的是同一个根本问题时，保留表述最完整的一条（keep），其余 merge 到它（merge_into 填其 risk_id）。
2. 定级：按统一标准重新定级，且不超过该条错误类型的上限。可以对照全文核实：若问题其实已在论文别处处理，定为 info 并在理由中指出位置；不要因为作者承认就提高级别，也不要把真实的错误降为 info。
3. rationale：一句话说明理由（读者会看到）。对每一条都要给出决定。"""


SYMBOL_AUTHOR_SCHEMA = obj({"responses": arr(obj({
    "id": S,
    "position": enum("no_issue", "minor", "issue"),
    "argument": S,
    "quote": S,
}))})

SYMBOL_REVIEWER_SCHEMA = obj({"verdicts": arr(obj({
    "id": S,
    "verdict": enum("resolved", "minor", "unresolved"),
    "severity": SEV,
    "title": S,
    "comment": S,
}))})

SYMBOL_AUTHOR_TASK = """程序检测到下面这些符号 / 术语可能存在问题（在定义之前被使用，或在多处被定义）。请逐条说明：
- no_issue：不是问题（如引言中的非正式使用、后文是同一含义的重述或推广、局部变量与全局记号同名但上下文不会混淆、全文别处已有定义）；
- minor：确有不严谨之处，但不会让称职读者误解；
- issue：两处定义的含义确实不同，或读者在定义出现前确实无法理解该符号。
argument 中说明理由，quote 逐字引用支持你观点的原文（没有就填空字符串）。"""

SYMBOL_REVIEWER_TASK = """以上是作者对每个符号疑点的辩护。判断辩护是否成立：成立判 resolved；问题真实但不影响理解判 minor；同一符号确实被赋予了不同含义、或在定义前的使用会让读者无法理解判 unresolved。
对每一条给出简短标题（不超过 30 字）、按统一标准的 severity，以及意见（说明读者需要注意什么）。"""


# ---------------------------------------------------------------------------
# Stage 3: author / reviewer debate
# ---------------------------------------------------------------------------

STEPS_SCHEMA = obj({"steps": arr(obj({
    "step_id": S,
    "line": I,
    "quote": S,
    "claim": S,
    "justification": S,
    "uses": arr(S),
    "hypotheses_check": S,
}))})

CHALLENGES_SCHEMA = obj({"challenges": arr(obj({
    "challenge_id": S,
    "step_id": nullable(S),
    "category": enum("precondition", "symbol", "unsupported", "gap", "circular", "other"),
    "line": I,
    "quote": S,
    "concern": S,
    "affects": enum("statement", "proof"),
    "error_type": enum("logic", "pseudocode_or_typesetting", "typo_or_notation", "degenerate_or_convention"),
    "severity": SEV,
}))})

REBUTTAL_SCHEMA = obj({"responses": arr(obj({
    "challenge_id": S,
    "stance": enum("defend", "minor", "partial", "concede"),
    "response": S,
    "evidence_quote": S,
}))})

VERDICT_SCHEMA = obj({"verdicts": arr(obj({
    "challenge_id": S,
    "verdict": enum("resolved", "minor", "unresolved"),
    "severity": SEV,
    "title": S,
    "final_comment": S,
}))})

SUPPORT_AUTHOR_SCHEMA = obj({
    "support_found": B,
    "kind": enum("proof_in_text", "citation", "standard_fact", "restatement", "none"),
    "line": I,
    "quote": S,
    "argument": S,
})

SUPPORT_REVIEWER_SCHEMA = obj({
    "verdict": enum("resolved", "minor", "unresolved"),
    "severity": SEV,
    "title": S,
    "comment": S,
})


AUTHOR_STEPS_TASK = """请把上面这个证明分解为 3～10 个关键步骤。对每一步：
- step_id：S1、S2…；line：该步骤所在行号；quote：该步骤在原文中的一句（逐字）；
- claim：这一步得出了什么；justification：依据是什么（可引用全文任何位置）；uses：用到的条目 id 或外部结果名称；
- hypotheses_check：若这一步引用了某个引理 / 定理，逐条说明它的前提条件在此处如何满足（不涉及引用时写“无”）。"""

REVIEWER_CHALLENGE_TASK = """以上是作者对该证明关键步骤的说明。请逐条审查（也包括作者没有列出的步骤），只对确有实质疑问之处提出质疑。
对每个质疑：challenge_id（C1、C2…）、对应的 step_id（若有）、category、line、quote（逐字引用原文）、concern（具体缺了什么、哪个前提没验证、哪个符号含义冲突；若给出反例请写明）、
affects（statement：结论本身受影响；proof：只影响证明）、error_type、按统一标准且不超过该类型上限的 severity。提出之前先确认全文其他位置没有处理这个问题。"""

AUTHOR_REBUTTAL_TASK = """审稿人对你的证明提出了以上质疑。请逐条回应：
- stance：defend（论文中已有充分依据，给出原文）、minor（问题真实但只是笔误 / 退化情形 / 不影响结论）、partial（部分成立）、concede（问题在主要情形下确实存在）；
- response：你的理由；evidence_quote：论文中支持你的原文（逐字，可来自全文任何位置；没有就填空字符串）。"""

REVIEWER_VERDICT_TASK = """作者已经对你的质疑逐条回应。请逐条裁决：
- resolved：作者给出的依据确实存在于论文中、足以补全论证；
- minor：问题真实，但只是笔误、退化情形、约定或不影响结论的局部疏漏；
- unresolved：问题实质存在（作者承认，或依据不充分、与原文不符）。
对每一条给出 title（不超过 30 字，概括问题）、按统一标准重新确认的 severity（看过作者回应后可以调整），以及 final_comment（unresolved / minor 时说明读者需要自己核查什么；resolved 时一句话说明为何接受）。"""

SUPPORT_AUTHOR_TASK = """程序没有为上面这个结论找到证明环境、“Proof of …”章节或文献引用。请作为作者在全文中寻找它的依据：
- 若正文中有非正式的证明、推导或计算机验证的说明，kind=proof_in_text，给出起始行号与原文；
- 若它其实引自文献（正文提到了出处），kind=citation；
- 若它是无需证明的标准事实，kind=standard_fact，并说明是哪个标准结果；
- 若它是别处已证明结论的重述，kind=restatement；
- 若确实找不到依据，kind=none，support_found=false。"""

SUPPORT_REVIEWER_TASK = """以上是作者为该结论给出的依据。请判断：该结论在论文中是否得到了充分支撑（证明、可靠出处、明确说明的计算机验证或确属标准事实）？
充分 → resolved；有依据但写得不完整 → minor；不充分 → unresolved。按统一标准给出 severity，并给出 title（不超过 30 字）与 comment。"""


# ---------------------------------------------------------------------------
# Stage 4: post-processing (meta-review, external-knowledge consolidation)
# ---------------------------------------------------------------------------

META_SCHEMA = obj({"decisions": arr(obj({
    "risk_id": S,
    "action": enum("keep", "merge"),
    "merge_into": nullable(S),
    "severity": SEV,
    "rationale": S,
}))})


EXTERNAL_MERGE_SCHEMA = obj({"groups": arr(obj({
    "canonical_name": S,
    "member_ids": arr(S),
    "keep": B,
}))})


def external_merge_system(lang: str) -> str:
    return f"""下面是从一篇论文中识别出的“外部知识”（论文使用但未证明、也未引用文献的标准结果）。请把指同一个结果的条目归为一组（不同叫法、中英文、括号补充说明都算同一个），
给出通行的规范名称（canonical_name）。若某组只是初等的运算性质或常识（不需要专门知识），keep=false；否则 keep=true。每个 id 恰好出现在一个组里。
{lang_line(lang)}"""


# ---------------------------------------------------------------------------
# Structure mode: papers without theorem-like environments (empirical / ML / applied papers)
# ---------------------------------------------------------------------------

def structure_system(lang: str) -> str:
    return f"""你在参与一次论文“审稿体检”：这篇论文没有定理类环境，体检对象是论文提出的核心论断。作者智能体为论断给出论文中的证据，审稿人智能体逐条质疑，主审复核整理。具体扮演哪个角色、做什么，写在用户消息中论文全文之后。

用户消息的第一部分是论文全文：行首 L<n>| 是源文件行号（“### 文件 …”标出换文件）；【sec-… · Section …】标出章节开头，【tab-… / fig-… / alg-… 开始/结束】标出图表，【eq-… · 公式 (n)】标出编号公式；正文中的 \\ref 已替换为编号并附〔id〕。表格保留原始 LaTeX，数字可以直接读取。

所有角色共同遵守：
1. 善意阅读：按该领域称职读者的理解来读。领域通行的做法（使用标准基准与指标、报告开发集上选出的模型、引用前人报告的基线数字）本身不是问题，除非论文的说法超出了它。
2. 先检索全文，再下结论：表格、图注、附录、脚注、实验设置与训练细节段落都是论文的证据。已在别处说明的，不是问题。
3. 引用原文时逐字复制（可含 LaTeX），并给出行号；条目用 id 指代（如 tab-2、sec-23、eq-1）。不得编造论文中没有的内容或数字。
4. 严重程度标准（统一使用）：
   - high：核心论断与论文自己的证据矛盾（如摘要 / 结论中的数字与表格或正文不一致、所称的提升在表中不存在），或核心论断完全没有证据；
   - medium：证据弱于论断——比较不对等（数据、评测、规模或开销不可比）、把提升归因于某个设计却没有对照或消融、只在单一设置下验证却作一般性表述、判断论断所需的关键实验细节缺失；
   - low：局部的数字、记号或表述错误，不影响任何核心论断；
   - info：表述可以更严谨，但称职读者不会被误导。
   定为 high 之前先自问：“受影响的是核心论断本身吗？论文自己的哪条证据与之矛盾？”
5. 错误类型（error_type）决定严重程度的上限：
   - typo_or_notation：笔误、记号不一致、上下文能明确看出正确数字且不影响论断的数字笔误——最高 low；
   - pseudocode_or_typesetting：伪代码、算法、表格排版中的错误，且意图从上下文明显可见——最高 medium；
   - degenerate_or_convention：只涉及领域通行约定——最高 info；
   - logic：论断与证据的关系本身有问题（包括无法判断哪个才是真实结果的数字矛盾）——按标准定级。
6. 只评价论文写出的论断是否被论文自己的证据支撑。不要提出文风、写作动机、未来工作之类的意见；不要因为“还可以多做一个实验”就提质疑——只有当某个论断确实依赖那项证据时才指出它缺失。
7. 审查公式推导时（推导审查），严重程度按以下标准：
   - high：推导出的主要结果按其写法是错的——给出了具体的反例，或极限 / 特例检验、量纲检验明确失败；
   - medium：某一步需要实质性的补充论证（未说明的近似及其适用范围、非平凡的跳步、依赖了未说明的条件），但结果很可能成立；
   - low：笔误、符号、因子或下标错误，且可直接修正、不影响后续结果；
   - info：只涉及约定（单位制、度规符号、归一化约定）。
   善意阅读同样适用：领域内的标准近似和常用恒等式不需要论文逐一证明；先在全文中查找该步骤是否已在别处说明。
{lang_line(lang)}"""


CLAIMS_SCHEMA = obj({"claims": arr(obj({
    "line": I,
    "quote": S,
    "kind": enum("result", "comparison", "efficiency", "generalization", "mechanism", "other"),
    "importance": enum("core", "supporting"),
    "summary": S,
    "evidence_ids": arr(S),
}))})

CLAIMS_TASK = """## 任务：列出论文的核心论断
找出论文提出的、需要证据支撑的论断（最多 12 条），优先摘要、引言、结论中的总括性论断，以及结果章节中带数字的结论。不要列出：对前人工作的介绍、方法的定义与描述（除非它断言了某种效果）、致谢与未来工作。
对每条论断：
- line：所在行号；quote：论断在原文中的一句（逐字复制，20～300 个字符，可含 LaTeX；必须能在原文中原样找到）；
- kind：result（结果）、comparison（与他人比较）、efficiency（开销 / 速度）、generalization（推广到其他任务或设置）、mechanism（某个设计带来了某种效果）、other；
- importance：core（论文的主要贡献所依赖的论断）或 supporting；
- summary：不超过 30 字的概括；
- evidence_ids：论文为它提供证据的表、图、章节或公式的 id（来自全文中的【…】标记，如 tab-2、sec-23）；论文没有给出证据时返回空数组。
同一论断在摘要和正文中重复出现时只列一次（取摘要或引言中的那一句）。"""

CLAIM_AUTHOR_ROLE = """## 你的角色：论文作者
你熟悉全文。为论断给出论文中的证据：表格中的哪一行哪一列、哪张图、哪段实验设置或分析支撑了它，比较的双方是否可比。
辩护时可以引用全文任何位置的内容。只有当问题在称职读者的理解下确实存在时才承认（concede）；
若问题真实但只是笔误或不影响论断的局部疏漏，回答 minor 并说明为何不影响论断；若论文已有依据，回答 defend 并给出原文。"""

CLAIM_REVIEWER_ROLE = """## 你的角色：审稿人
严格但公正，只评价这个论断是否被论文自己的证据支撑。重点核对：
- numeric：论断中的每个数字是否与表格 / 正文其他位置一致（逐一对照，不一致时写出两处原文与行号）；
- overclaim：论断的措辞（如 state of the art、generalizes well、significantly faster）是否超出了证据覆盖的设置；
- comparison：比较是否对等（相同数据与评测、可比的规模或开销），对比对象是否就是论断所指的对象；
- attribution：把效果归因于某个设计时，是否有对照或消融支持；
- reproducibility：判断该论断所需的关键设置（数据划分、训练开销的计算方式、选择模型的依据等）是否写明。
每个质疑必须指向原文中具体的一句（逐字引用并给出行号），具体说明缺了什么，并按统一标准定级。没有问题时返回空列表。"""

CLAIM_AUTHOR_TASK = """请说明论文用哪些证据支撑上面这个论断，把证据链分解为 1～6 步。对每一步：
- step_id：S1、S2…；line：证据所在行号；quote：证据原文（逐字，如表格中的一行、结果段落中的一句）；
- claim：这一步说明了什么；justification：为何足以支撑论断；uses：用到的 tab- / fig- / sec- / eq- id；
- hypotheses_check：若论断涉及比较，说明比较是否对等（数据、评测方式、模型规模或训练开销）；不涉及比较时写“无”。"""

CLAIM_CHALLENGES_SCHEMA = obj({"challenges": arr(obj({
    "challenge_id": S,
    "step_id": nullable(S),
    "category": enum("numeric", "overclaim", "comparison", "attribution", "reproducibility", "other"),
    "line": I,
    "quote": S,
    "concern": S,
    "affects": enum("statement", "proof"),
    "error_type": enum("logic", "pseudocode_or_typesetting", "typo_or_notation", "degenerate_or_convention"),
    "severity": SEV,
}))})

CLAIM_REVIEWER_TASK = """以上是作者给出的证据链。请逐条审查（也包括作者没有列出的证据），只对确有疑问之处提出质疑。
对每个质疑：challenge_id（C1、C2…）、对应的 step_id（若有）、category、line、quote（逐字引用原文）、concern（具体问题；数字不一致时写出两处的数字与行号）、
affects（statement：论断本身不成立或需要收窄；proof：只是证据的呈现有缺陷，论断本身可以成立）、error_type、按统一标准且不超过该类型上限的 severity。提出之前先确认全文其他位置没有处理这个问题。"""

CLAIM_REBUTTAL_TASK = """审稿人对这个论断提出了以上质疑。请逐条回应：
- stance：defend（论文中已有充分依据，给出原文）、minor（问题真实但只是笔误 / 不影响论断）、partial（部分成立）、concede（问题确实存在）；
- response：你的理由；evidence_quote：论文中支持你的原文（逐字，可来自全文任何位置；没有就填空字符串）。"""

CLAIM_VERDICT_TASK = """作者已经对你的质疑逐条回应。请逐条裁决：
- resolved：作者给出的依据确实存在于论文中、足以支撑论断；
- minor：问题真实，但只是笔误或不影响论断的局部疏漏；
- unresolved：问题实质存在（作者承认，或依据不充分、与原文不符）。
对每一条给出 title（不超过 30 字，概括问题）、按统一标准重新确认的 severity（看过作者回应后可以调整），以及 final_comment（unresolved / minor 时说明读者需要自己核查什么；resolved 时一句话说明为何接受）。"""

NUMERIC_SCHEMA = obj({"items": arr(obj({
    "id": S,
    "verdict": enum("inconsistent", "different", "unclear"),
    "severity": SEV,
    "title": S,
    "comment": S,
}))})

NUMERIC_TASK = """## 你的角色：审稿人（数字核对）
程序在论文中找到了下面这些“同一指标、语境相近、数值不同”的候选（配对是启发式的）。请对照全文逐条核对：
- inconsistent：两处说的是同一个量（同一模型、数据集、指标与设置），数值却不同；
- different：其实是不同的量（不同模型规模、数据集、设置或统计口径），不是问题；
- unclear：无法从论文判断。
对每一条给出 verdict、按统一标准的 severity（同一个量在摘要 / 结论与表格 / 正文中不一致、读者无法确定哪个是真实结果：至少 medium；涉及论文的核心论断：high；different 时填 info）、
title（不超过 30 字）以及 comment（说明两处分别指什么、表格中是哪个数字、读者应以哪个为准）。"""


# ---------------------------------------------------------------------------
# Reading guide (for students): summary, mind map, per-section notes, concepts, prerequisites, questions
# ---------------------------------------------------------------------------

GUIDE_SCHEMA = obj({
    "summary": S,
    "audience": S,
    "organization": S,
    "mindmap": arr(obj({"key": S, "parent": nullable(S), "label": S, "note": S, "ids": arr(S)})),
    "sections": arr(obj({"id": S, "summary": S, "key_points": arr(S)})),
    "concepts": arr(obj({"term": S, "explanation": S, "ids": arr(S)})),
    "prerequisites": arr(obj({"name": S, "why": S, "level": enum("basic", "intermediate", "advanced")})),
    "questions": arr(obj({"question": S, "hint": S, "ids": arr(S)})),
})

GUIDE_TASK = """## 你的角色：导读老师
读者是第一次读这篇论文的高校学生（高年级本科生或研究生），学过本领域的基础课程。请为他们写一份导读，帮助他们抓住论文主线、读懂关键部分。论文全文见上文，章节目录、核心论断和体检发现的风险点附在本任务之后。

要求：
1. 忠于原文：只概括论文写了什么，不编造论文中没有的结论、数字或实验。论文中有争议的地方，引用体检风险点的 id，不要自行下结论。
2. 每一项尽量给出 ids，指向论文中讨论它的位置：章节 id（sec-…）、条目 id（thm-… / lem-… / def-… / as-… 等，来自全文中的【…】标记或下面的目录）、图表 id（fig-… / tab-… / alg-…）、公式 id（eq-…）或风险点 id。只能使用上文或下面出现过的 id。
3. 用通俗、准确的中文；数学记号用 $…$ 包裹。id 只写在各项的 ids 字段里，不要写进 summary、label、explanation 等文字中（文字里可以写“定理 2”“表 2”“第 3.2 节”这样读者看得懂的名称）。

各字段：
- summary：3～5 句话说清论文要解决什么问题、做了什么、得到了什么。
- audience：读懂这篇论文需要什么基础（一两句）。
- organization：思维导图采用的组织方式，以及为什么适合这篇论文（一句话）。
- mindmap：整篇论文的思维导图（根节点就是论文本身，不要输出根节点）。请按论文类型和该领域的惯例选择组织方式，例如数学 / 理论论文常按“背景与定义 → 关键引理 → 主要定理 → 证明思路 → 推论与应用”，实验 / 工程论文常按“研究问题 → 方法 → 实验设置 → 主要结果 → 局限”，也可以沿用论文自己的结构——以帮助学生看清主线为准。
  每个节点：key（n1、n2…）、parent（上级节点的 key；一级分支填 null）、label（不超过 20 字）、note（一句话说明，可为空字符串）、ids。共 15～40 个节点，最多 4 层，一级分支 4～7 个。
- sections：按章节顺序，为每个主要章节（id 用章节目录中的 sec-…）写通俗解读 summary（2～4 句）和 2～4 个要点 key_points。
- concepts：6～15 个关键概念或记号：term、通俗解释 explanation、ids（定义或首次出现的位置）。
- prerequisites：3～8 项需要预先了解的背景知识：name、why（论文哪里用到它）、level（basic / intermediate / advanced）。
- questions：4～8 个帮助自测是否读懂的问题：question、hint（提示去看哪里、从什么角度想，不直接给出答案）、ids。"""


# ---------------------------------------------------------------------------
# Structure mode: derivation review (sections dense in displayed equations, e.g. theoretical physics)
# ---------------------------------------------------------------------------

DERIVATION_AUTHOR_ROLE = """## 你的角色：论文作者
你熟悉全文。为这一节的公式推导给出依据：每一步从哪些公式、用什么代换、恒等式、近似或定义得到下一式，近似成立的条件是什么、在此处如何满足。
辩护时可以引用全文任何位置的内容。只有当问题在称职读者的理解下确实存在时才承认（concede）；若问题真实但只是笔误、因子或符号写错且不影响结果，回答 minor 并说明为何不影响；若论文已有依据，回答 defend 并给出原文。"""

DERIVATION_REVIEWER_ROLE = """## 你的角色：审稿人
严格但公正，逐步核对这一节的推导。重点检查：
- algebra：代数运算、符号、因子、下标与求和是否正确（能验算的就自己验算一遍）；
- approximation：用到的近似或展开是否说明了适用条件、在此处是否成立；
- gap：被一笔带过但确实非平凡、读者难以自行补全的步骤；
- symbol：同一符号前后含义不一致，或未定义即使用；
- dimension：量纲或单位是否一致；
- limit：已知的极限或特例（如退化参数、经典极限、已知结果）是否得到正确的回归。
每个质疑必须指向原文中具体的一句或一式（逐字引用并给出行号），具体说明哪一步有问题、错在哪里（能给出正确形式的请写出），并按统一标准定级。没有问题时返回空列表。"""

DERIVATION_AUTHOR_TASK = """请把上面这一节的推导分解为 3～12 个关键步骤。对每一步：
- step_id：S1、S2…；line：该步骤所在行号；quote：这一步在原文中的一句或一式（逐字）；
- claim：这一步得出了什么（如“由式 (2.3) 代入 (2.1) 得到 (2.5)”）；justification：依据（代换、恒等式、近似、定义，可引用全文任何位置）；
- uses：用到的公式 / 章节 id（eq-… / sec-…）或外部结果名称；
- hypotheses_check：用到近似或条件时，说明其适用条件以及在此处如何满足（不涉及时写“无”）。"""

DERIVATION_CHALLENGES_SCHEMA = obj({"challenges": arr(obj({
    "challenge_id": S,
    "step_id": nullable(S),
    "category": enum("algebra", "approximation", "gap", "symbol", "dimension", "limit", "other"),
    "line": I,
    "quote": S,
    "concern": S,
    "affects": enum("statement", "proof"),
    "error_type": enum("logic", "pseudocode_or_typesetting", "typo_or_notation", "degenerate_or_convention"),
    "severity": SEV,
}))})

DERIVATION_REVIEWER_TASK = """以上是作者对这一节推导步骤的说明。请逐步核对（也包括作者没有列出的步骤），只对确有疑问之处提出质疑。
对每个质疑：challenge_id（C1、C2…）、对应的 step_id（若有）、category、line、quote（逐字引用原文）、concern（哪一步、错在哪里、正确形式是什么；做了验算或极限检验的请写出过程要点）、
affects（statement：影响这一节推出的结果；proof：只影响推导过程的写法）、error_type、按统一标准且不超过该类型上限的 severity。提出之前先确认全文其他位置没有处理这个问题。"""

DERIVATION_REBUTTAL_TASK = """审稿人对这一节的推导提出了以上质疑。请逐条回应：
- stance：defend（论文中已有充分依据，给出原文）、minor（问题真实但只是笔误 / 因子或符号写错、不影响结果）、partial（部分成立）、concede（问题确实存在）；
- response：你的理由（必要时写出验算过程）；evidence_quote：论文中支持你的原文（逐字，可来自全文任何位置；没有就填空字符串）。"""

DERIVATION_VERDICT_TASK = """作者已经对你的质疑逐条回应。请逐条裁决：
- resolved：作者给出的依据确实存在于论文中、或验算表明该步正确；
- minor：问题真实，但只是笔误、因子或符号写错，且不影响结果；
- unresolved：问题实质存在（作者承认，或依据不充分、验算不成立）。
对每一条给出 title（不超过 30 字）、按统一标准重新确认的 severity，以及 final_comment（unresolved / minor 时说明读者需要自己核查什么、正确形式是什么；resolved 时一句话说明为何接受）。"""
