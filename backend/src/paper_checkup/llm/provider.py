"""Switchable LLM providers with structured JSON output, disk cache and concurrency control.

Configuration (environment variables or a .env file):

    LLM_PROVIDER        openai (default, any OpenAI-compatible API) | anthropic | none
    LLM_MODEL           model name for all roles (default depends on provider)
    LLM_MODEL_EXTRACT   optional per-role override, "model" or "provider:model"
    LLM_MODEL_AUTHOR    optional per-role override
    LLM_MODEL_REVIEWER  optional per-role override
    OPENAI_API_KEY / OPENAI_BASE_URL      for the OpenAI-compatible provider
    LLM_JSON_MODE       json_object (default) | json_schema | none   (OpenAI-compatible only)
    LLM_REASONING_EFFORT  optional reasoning_effort for OpenAI reasoning models
    ANTHROPIC_API_KEY   (or an `ant auth login` profile) for the anthropic provider
    LLM_EFFORT          Claude effort: low | medium | high | xhigh | max (default: per role)
    LLM_CONCURRENCY     parallel requests (default 4)
    LLM_MAX_TOKENS      max output tokens per request (default 16000)
    LLM_CACHE_DIR       response cache directory (default backend/.cache/llm)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .pricing import Budget, cost_usd

log = logging.getLogger("paper_checkup.llm")

DEFAULT_MODELS = {"openai": "gpt-6.2-astra", "anthropic": "claude-opus-5-5", "deepseek": "deepseek-v4-pro",
                  "qwen": "qwen-max", "moonshot": "kimi-latest"}

# OpenAI-compatible presets. Each has its own key variable so several keys can live in .env at once;
# <PRESET>_BASE_URL / <PRESET>_JSON_MODE / <PRESET>_MAX_TOKENS_PARAM override the defaults.
OPENAI_COMPAT_PRESETS: dict[str, dict] = {
    "openai": {"key_env": "OPENAI_API_KEY", "base_url": "https://api.openai.com/v1",
               "json_mode": "json_object", "max_tokens_param": "max_tokens"},
    # api-docs.deepseek.com: json_object only (prompt must contain "json"), max_tokens, reasoning_effort low|high|max
    "deepseek": {"key_env": "DEEPSEEK_API_KEY", "base_url": "https://api.deepseek.com",
                 "json_mode": "json_object", "max_tokens_param": "max_tokens"},
    "qwen": {"key_env": "DASHSCOPE_API_KEY", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
             "json_mode": "json_object", "max_tokens_param": "max_tokens"},
    "moonshot": {"key_env": "MOONSHOT_API_KEY", "base_url": "https://api.moonshot.cn/v1",
                 "json_mode": "json_object", "max_tokens_param": "max_tokens"},
}
PROVIDERS = set(OPENAI_COMPAT_PRESETS) | {"anthropic"}
ROLE_EFFORT = {"extract": "medium", "author": "high", "reviewer": "high"}


class LLMError(RuntimeError):
    # Token usage of a call that was billed but failed (truncated / unparsable output), for cost accounting.
    usage: dict | None = None


class BudgetExceeded(LLMError):
    """Raised instead of making a call once the analysis or daily spend limit is reached."""


class LLMRefusal(LLMError):
    pass


@dataclass
class LLMStats:
    calls: int = 0
    cached: int = 0
    failures: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read_tokens: int = 0
    reasoning_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0          # spend of the real calls made by this analysis (cached responses are free)
    unpriced: set[str] = field(default_factory=set)

    def add(self, usage: dict):
        self.reasoning_tokens += int(usage.get("reasoning_tokens") or 0)
        self.cache_write_tokens += int(usage.get("cache_write_tokens") or 0)
        self.tokens_in += int(usage.get("input_tokens") or 0)
        self.tokens_out += int(usage.get("output_tokens") or 0)
        self.cache_read_tokens += int(usage.get("cache_read_tokens") or 0)


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def extract_json(text: str) -> dict:
    """Parse a JSON object from model text (tolerates code fences and surrounding prose)."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        v = json.loads(t)
        if isinstance(v, dict):
            return v
    except json.JSONDecodeError:
        pass
    start = t.find("{")
    end = t.rfind("}")
    if start >= 0 and end > start:
        v = json.loads(t[start:end + 1])
        if isinstance(v, dict):
            return v
    raise LLMError("模型输出不是合法的 JSON 对象")


class BaseProvider:
    provider = "base"

    def __init__(self, model: str):
        self.model = model

    @property
    def spec(self) -> str:
        return f"{self.provider}:{self.model}"

    async def complete_json(self, system: str, blocks: list[str], schema: dict, schema_name: str,
                            max_tokens: int, role: str) -> tuple[dict, dict]:
        raise NotImplementedError


class OpenAICompatProvider(BaseProvider):
    provider = "openai"

    def __init__(self, model: str, preset: str = "openai"):
        super().__init__(model)
        from openai import AsyncOpenAI

        cfg = OPENAI_COMPAT_PRESETS[preset]
        self.provider = preset
        P = preset.upper()
        # An empty OPENAI_BASE_URL in the environment would be picked up by the SDK as an empty URL.
        base_url = _env(f"{P}_BASE_URL") or cfg["base_url"]
        api_key = _env(cfg["key_env"])
        if not api_key:
            raise LLMError(f"未设置 {cfg['key_env']}（{preset} 接口需要 API key）")
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=1800.0, max_retries=3)
        # prompt_cache_key is an OpenAI-platform parameter; other compatible servers may reject it.
        self.official = "api.openai.com" in base_url
        # GPT-5.6+ on the OpenAI platform: explicit prompt-cache breakpoints after the shared prefix blocks.
        bp = (_env("LLM_CACHE_BREAKPOINTS", "auto") or "auto").lower()
        self.breakpoints = (bp in ("1", "true", "yes", "on")) or (
            bp == "auto" and self.official and bool(re.match(r"^gpt-(?:5\.(?:[6-9]|\d{2,})|[6-9])", model)))
        # Provider-scoped settings first; the generic LLM_* variables only apply to the openai preset
        # (their values, e.g. json_schema / max_completion_tokens, are OpenAI-specific).
        generic = preset == "openai"
        self.json_mode = (_env(f"{P}_JSON_MODE") or (_env("LLM_JSON_MODE") if generic else None)
                          or cfg["json_mode"]).lower()
        self.reasoning_effort = _env(f"{P}_REASONING_EFFORT") or _env("LLM_REASONING_EFFORT")
        self._max_tokens_param = (_env(f"{P}_MAX_TOKENS_PARAM") or (_env("LLM_MAX_TOKENS_PARAM") if generic else None)
                                  or cfg["max_tokens_param"])

    async def complete_json(self, system, blocks, schema, schema_name, max_tokens, role):
        import openai

        parts = list(blocks)
        if self.json_mode != "json_schema":
            parts.append("请只输出一个 json 对象（不要输出其他文字），并严格符合下面的 JSON Schema：\n"
                         + json.dumps(schema, ensure_ascii=False))
        if self.breakpoints and len(parts) > 1:
            # Breakpoints after the paper catalog (shared by every call) and after the per-item context
            # (shared by the rounds of one debate). Unique tails are not written to the cache.
            content = []
            for i, b in enumerate(parts):
                item = {"type": "text", "text": b if i == len(parts) - 1 else b + "\n\n"}
                if i < min(2, len(parts) - 1):
                    item["prompt_cache_breakpoint"] = {"mode": "explicit"}
                content.append(item)
            user_content: str | list = content
        else:
            user_content = "\n\n".join(parts)
        kwargs: dict = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user_content}],
        }
        if self.breakpoints and len(parts) > 1:
            kwargs["prompt_cache_options"] = {"mode": "explicit"}
        if self.json_mode == "json_schema":
            kwargs["response_format"] = {"type": "json_schema",
                                         "json_schema": {"name": schema_name, "schema": schema, "strict": True}}
        elif self.json_mode == "json_object":
            kwargs["response_format"] = {"type": "json_object"}
        if self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort
        if self.official:
            # Route requests sharing the same system prompt + paper catalog to the same prompt cache.
            kwargs["prompt_cache_key"] = hashlib.sha256((system + "\x00" + blocks[0]).encode()).hexdigest()[:32]
        kwargs[self._max_tokens_param] = max_tokens
        try:
            resp = await self.client.chat.completions.create(**kwargs)
        except openai.BadRequestError as e:
            msg = str(e)
            if "prompt_cache" in msg and (self.official or self.breakpoints):
                log.warning("接口不支持 prompt cache 参数，已关闭：%s", msg[:200])
                self.official = False
                self.breakpoints = False
                return await self.complete_json(system, blocks, schema, schema_name, max_tokens, role)
            if "max_tokens" in msg and self._max_tokens_param == "max_tokens":
                # Newer OpenAI models only accept max_completion_tokens.
                self._max_tokens_param = "max_completion_tokens"
                kwargs.pop("max_tokens", None)
                kwargs["max_completion_tokens"] = max_tokens
                resp = await self.client.chat.completions.create(**kwargs)
            elif "response_format" in msg and self.json_mode != "none":
                log.warning("接口不支持 response_format=%s，改为纯文本 JSON 模式", self.json_mode)
                self.json_mode = "none"
                return await self.complete_json(system, blocks, schema, schema_name, max_tokens, role)
            else:
                raise
        choice = resp.choices[0]
        content = choice.message.content or ""
        usage = {}
        if resp.usage:
            usage = {"input_tokens": resp.usage.prompt_tokens, "output_tokens": resp.usage.completion_tokens}
            details = getattr(resp.usage, "prompt_tokens_details", None)
            if details is not None and getattr(details, "cached_tokens", None):
                usage["cache_read_tokens"] = details.cached_tokens
            if details is not None and getattr(details, "cache_write_tokens", None):
                usage["cache_write_tokens"] = details.cache_write_tokens
            cdetails = getattr(resp.usage, "completion_tokens_details", None)
            if cdetails is not None and getattr(cdetails, "reasoning_tokens", None):
                usage["reasoning_tokens"] = cdetails.reasoning_tokens
        try:
            if choice.finish_reason == "length":
                raise LLMError("输出被截断（达到输出上限；推理 token 也计入上限），请调大 LLM_MAX_TOKENS")
            if getattr(choice.message, "refusal", None):
                raise LLMRefusal(f"模型拒绝了该请求：{choice.message.refusal}")
            return extract_json(content), usage
        except LLMError as e:
            e.usage = usage
            raise
        except json.JSONDecodeError as e:
            err = LLMError(f"模型输出不是合法的 JSON：{e}")
            err.usage = usage
            raise err from e


class AnthropicProvider(BaseProvider):
    provider = "anthropic"

    def __init__(self, model: str):
        super().__init__(model)
        import anthropic

        # Credentials resolve from ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN or an `ant auth login` profile.
        self.client = anthropic.AsyncAnthropic(timeout=900.0, max_retries=3)
        self.effort_override = _env("LLM_EFFORT")

    async def complete_json(self, system, blocks, schema, schema_name, max_tokens, role):
        content = []
        for i, b in enumerate(blocks):
            item = {"type": "text", "text": b}
            if i == 0 and len(blocks) > 1:
                # The first block is the shared paper context: cache it across requests.
                item["cache_control"] = {"type": "ephemeral"}
            content.append(item)
        effort = self.effort_override or ROLE_EFFORT.get(role, "high")
        kwargs = dict(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        )
        if self.model in ("claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"):
            # Server-side fallback on policy declines (routed by refusal category).
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"
            api = self.client.beta.messages
        else:
            api = self.client.messages
        async with api.stream(**kwargs) as stream:
            msg = await stream.get_final_message()
        if msg.stop_reason == "refusal":
            raise LLMRefusal("模型拒绝了该请求")
        if msg.stop_reason == "max_tokens":
            raise LLMError("输出被截断（达到 max_tokens），请调大 LLM_MAX_TOKENS")
        text = next((b.text for b in msg.content if b.type == "text"), "")
        u = msg.usage
        usage = {"input_tokens": (u.input_tokens or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0)
                 + (getattr(u, "cache_creation_input_tokens", 0) or 0),
                 "output_tokens": u.output_tokens or 0,
                 "cache_read_tokens": getattr(u, "cache_read_input_tokens", 0) or 0}
        return extract_json(text), usage


def split_spec(spec: str, default_provider: str) -> tuple[str, str]:
    """'deepseek:deepseek-v4-pro' -> ('deepseek', 'deepseek-v4-pro'); bare model names use the default provider."""
    if ":" in spec and spec.split(":", 1)[0].lower() in PROVIDERS:
        prov, model = spec.split(":", 1)
        return prov.lower(), model
    return default_provider, spec


def make_provider(spec: str) -> BaseProvider:
    prov, model = split_spec(spec, (_env("LLM_PROVIDER", "openai") or "openai").lower())
    if prov == "anthropic":
        return AnthropicProvider(model or DEFAULT_MODELS["anthropic"])
    if prov not in OPENAI_COMPAT_PRESETS:
        raise LLMError(f"未知的 LLM_PROVIDER：{prov}（可选：{', '.join(sorted(PROVIDERS))}）")
    return OpenAICompatProvider(model or DEFAULT_MODELS[prov], preset=prov)


@dataclass
class LLMConfig:
    enabled: bool
    provider: str
    models: dict[str, str] = field(default_factory=dict)   # role -> spec
    concurrency: int = 4
    max_tokens: int = 16000
    cache_dir: Path | None = None
    language: str = "zh"
    reason_disabled: str | None = None

    @classmethod
    def from_env(cls) -> "LLMConfig":
        provider = (_env("LLM_PROVIDER", "openai") or "openai").lower()
        lang = _env("OUTPUT_LANG", "zh") or "zh"
        cache = Path(_env("LLM_CACHE_DIR", str(Path(__file__).resolve().parents[3] / ".cache" / "llm")))
        conc = int(_env("LLM_CONCURRENCY", "4") or 4)
        mt = int(_env("LLM_MAX_TOKENS", "16000") or 16000)
        if provider == "none":
            return cls(enabled=False, provider="none", language=lang, reason_disabled="LLM_PROVIDER=none")
        base = _env("LLM_MODEL", DEFAULT_MODELS.get(provider, "gpt-4o"))
        # "LLM_MODEL=deepseek:deepseek-v4-pro" switches provider and model in one line.
        base_provider, base_model = split_spec(base, provider)
        models = {}
        for role in ("extract", "author", "reviewer"):
            spec = _env(f"LLM_MODEL_{role.upper()}")
            prov, model = split_spec(spec, base_provider) if spec else (base_provider, base_model)
            models[role] = f"{prov}:{model}"
        reason = None
        for spec in set(models.values()):
            prov = spec.split(":", 1)[0]
            if prov == "anthropic":
                if not (_env("ANTHROPIC_API_KEY") or _env("ANTHROPIC_AUTH_TOKEN") or _anthropic_profile_exists()):
                    reason = "未设置 ANTHROPIC_API_KEY（也没有检测到 ant auth 登录）"
            elif prov not in OPENAI_COMPAT_PRESETS:
                reason = f"未知的模型提供方：{prov}"
            elif not _env(OPENAI_COMPAT_PRESETS[prov]["key_env"]):
                reason = f"未设置 {OPENAI_COMPAT_PRESETS[prov]['key_env']}"
        return cls(enabled=reason is None, provider=provider, models=models, concurrency=conc, max_tokens=mt,
                   cache_dir=cache, language=lang, reason_disabled=reason)


def _anthropic_profile_exists() -> bool:
    p = Path(os.path.expanduser("~/.config/anthropic"))
    return p.exists() and any(p.iterdir())


class LLMClient:
    """Role-aware facade used by the pipeline stages."""

    def __init__(self, config: LLMConfig, use_cache: bool = True, budget: Budget | None = None):
        self.config = config
        self.budget = budget
        self.stats = LLMStats()
        self.sem = asyncio.Semaphore(max(1, config.concurrency))
        self.use_cache = use_cache and config.cache_dir is not None
        self._providers: dict[str, BaseProvider] = {}
        if self.use_cache:
            config.cache_dir.mkdir(parents=True, exist_ok=True)

    def provider_for(self, role: str) -> BaseProvider:
        spec = self.config.models.get(role) or self.config.models.get("extract")
        if spec not in self._providers:
            self._providers[spec] = make_provider(spec)
        return self._providers[spec]

    async def aclose(self):
        """Close SDK HTTP clients inside the running loop (avoids 'Event loop is closed' at exit)."""
        for prov in self._providers.values():
            client = getattr(prov, "client", None)
            if client is not None and hasattr(client, "close"):
                try:
                    await client.close()
                except Exception:  # pragma: no cover - best effort
                    pass

    def _charge(self, spec: str, usage: dict | None) -> float:
        if not usage:
            return 0.0
        from .pricing import price_for
        if not price_for(spec)[1] and spec not in self.stats.unpriced:
            self.stats.unpriced.add(spec)
            log.warning("没有 %s 的价格，按已知最贵的价格估算花费", spec)
        c = cost_usd(spec, usage)
        self.stats.cost_usd += c
        if self.budget is not None:
            self.budget.add(c)
        return c

    def _cache_path(self, key: str) -> Path:
        return self.config.cache_dir / key[:2] / f"{key}.json"

    async def json(self, role: str, system: str, blocks: list[str], schema: dict, schema_name: str,
                   max_tokens: int | None = None, attempts: int = 2, use_disk_cache: bool = True,
                   budget_reserve: float = 0.1) -> dict:
        """One structured call. `budget_reserve` is the fraction of the budget this call may not dip into
        (kept for the final meta-review, which passes 0)."""
        prov = self.provider_for(role)
        # Reasoning/thinking tokens count against the output cap, so never go below the configured cap.
        max_tokens = max(max_tokens or 0, self.config.max_tokens)
        key = hashlib.sha256(json.dumps([prov.spec, system, blocks, schema_name, schema], ensure_ascii=False,
                                        sort_keys=True).encode()).hexdigest()
        use_cache = self.use_cache and use_disk_cache
        if use_cache:
            cp = self._cache_path(key)
            if cp.exists():
                try:
                    data = json.loads(cp.read_text())
                    self.stats.cached += 1
                    self.stats.add(data.get("usage") or {})
                    return data["response"]
                except (OSError, ValueError, KeyError):
                    pass
        last_err: Exception | None = None
        for attempt in range(attempts):
            async with self.sem:
                if self.budget is not None:
                    why = self.budget.refusal(budget_reserve)
                    if why:
                        self.budget.exhausted = self.budget.exhausted or why
                        raise BudgetExceeded(why)
                try:
                    self.stats.calls += 1
                    data, usage = await prov.complete_json(system, blocks, schema, schema_name, max_tokens, role)
                    self.stats.add(usage)
                    cost = self._charge(prov.spec, usage)
                    if use_cache:
                        cp = self._cache_path(key)
                        cp.parent.mkdir(parents=True, exist_ok=True)
                        cp.write_text(json.dumps({"spec": prov.spec, "schema": schema_name, "response": data,
                                                  "usage": usage, "cost_usd": round(cost, 6)}, ensure_ascii=False))
                    return data
                except LLMRefusal as e:
                    self.stats.failures += 1
                    self._charge(prov.spec, e.usage)
                    raise
                except (LLMError, json.JSONDecodeError) as e:
                    last_err = e
                    self._charge(prov.spec, getattr(e, "usage", None))
                    log.warning("LLM 输出解析失败（第 %d 次）：%s", attempt + 1, e)
                except Exception as e:  # network / API errors already retried by the SDK
                    self.stats.failures += 1
                    raise LLMError(f"{prov.spec} 调用失败：{type(e).__name__}: {e}") from e
        self.stats.failures += 1
        raise LLMError(f"{prov.spec} 连续 {attempts} 次未返回合法 JSON：{last_err}")
