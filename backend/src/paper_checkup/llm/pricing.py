"""Token prices and the spend guard for LLM calls.

Prices are USD per 1M tokens, checked 2026-11-25 on the providers' official pages:
- OpenAI model pages for GPT-6.2 Astra and GPT-6.2 Sol: input, cached input, cache write (30-minute
  retention), output. Requests over 272K input tokens pay 2x input/cache rates and 1.5x output.
  usage.prompt_tokens includes cached and cache-write tokens; cache-write pricing replaces the input rate.
- DeepSeek pricing page: peak prices below; off-peak is half. Peak hours are 01:00-04:00 and 06:00-10:00 UTC,
  Monday-Friday (Chinese public holidays are off-peak; they are billed here as peak, i.e. conservatively).
  Cache writes cost the normal (cache-miss) input price.

Models missing from PRICES are billed at the most expensive known rates so the budget errs on the safe side.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("paper_checkup.llm")


@dataclass(frozen=True)
class Price:
    input: float
    cached: float
    output: float
    cache_write: float | None = None     # None: cache writes are billed as normal input
    long_context: int | None = None      # input-token threshold of the long-context surcharge
    offpeak_factor: float | None = None  # multiplier outside the provider's peak hours


PRICES: dict[str, Price] = {
    "openai:gpt-6.2-astra": Price(8.0, 0.40, 40.0, cache_write=10.0, long_context=272_000),
    "openai:gpt-6.2-sol": Price(2.0, 0.10, 10.0, cache_write=2.5, long_context=272_000),
    "deepseek:deepseek-flash": Price(0.30, 0.006, 1.20, offpeak_factor=0.5),
    "deepseek:deepseek-v4-pro": Price(1.32, 0.044, 3.96, offpeak_factor=0.5),
}
FALLBACK_PRICE = PRICES["openai:gpt-6.2-astra"]


def deepseek_peak(t: datetime) -> bool:
    t = t.astimezone(timezone.utc)
    return t.weekday() < 5 and (1 <= t.hour < 4 or 6 <= t.hour < 10)


def price_for(spec: str) -> tuple[Price, bool]:
    """(price, known) for a 'provider:model' spec."""
    p = PRICES.get(spec)
    return (p, True) if p else (FALLBACK_PRICE, False)


def cost_usd(spec: str, usage: dict, when: datetime | None = None) -> float:
    p, _ = price_for(spec)
    i = int(usage.get("input_tokens") or 0)
    r = int(usage.get("cache_read_tokens") or 0)
    w = int(usage.get("cache_write_tokens") or 0)
    o = int(usage.get("output_tokens") or 0)
    fresh = max(0, i - r - w)
    in_mult, out_mult = (2.0, 1.5) if p.long_context and i > p.long_context else (1.0, 1.0)
    write_rate = p.cache_write if p.cache_write is not None else p.input
    c = (fresh * p.input + r * p.cached + w * write_rate) * in_mult + o * p.output * out_mult
    if p.offpeak_factor and not deepseek_peak(when or datetime.now(timezone.utc)):
        c *= p.offpeak_factor
    return c / 1e6


class SpendLedger:
    """Daily LLM spend (server local date), persisted to a small JSON file so restarts keep the count."""

    def __init__(self, path: Path | None):
        self.path = path
        self.days: dict[str, float] = {}
        if path and path.exists():
            try:
                self.days = {k: float(v) for k, v in json.loads(path.read_text()).items()}
            except (OSError, ValueError, AttributeError):
                log.warning("无法读取花费记录 %s，从 0 开始", path)

    @staticmethod
    def today() -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def spent_today(self) -> float:
        return self.days.get(self.today(), 0.0)

    def add(self, usd: float) -> None:
        if usd <= 0:
            return
        k = self.today()
        self.days[k] = self.days.get(k, 0.0) + usd
        for old in sorted(self.days)[:-90]:
            del self.days[old]
        if self.path:
            tmp = self.path.with_suffix(".tmp")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps({k: round(v, 6) for k, v in self.days.items()}, indent=1))
            tmp.replace(self.path)


@dataclass
class Budget:
    """Per-analysis and per-day spend limits (USD). Calls are refused once a limit is reached.

    `reserve` keeps a fraction of each limit for the final meta-review so that risks still get re-graded when
    the debates use up the budget.
    """
    per_job: float | None = None
    per_day: float | None = None
    ledger: SpendLedger | None = None
    spent: float = 0.0
    exhausted: str | None = None

    def day_exhausted(self) -> bool:
        return self.per_day is not None and self.ledger is not None and self.ledger.spent_today() >= self.per_day

    def refusal(self, reserve: float = 0.0) -> str | None:
        if self.per_job is not None and self.spent >= self.per_job * (1 - reserve):
            return f"本次分析的大模型预算 ${self.per_job:g} 已用完"
        if self.per_day is not None and self.ledger is not None and self.ledger.spent_today() >= self.per_day * (1 - reserve):
            return f"今天的大模型预算 ${self.per_day:g} 已用完"
        return None

    def add(self, usd: float) -> None:
        self.spent += usd
        if self.ledger is not None:
            self.ledger.add(usd)


def _env_float(name: str, default: str = "") -> float | None:
    v = (os.environ.get(name) or default).strip()
    if not v or v.lower() in ("none", "off", "unlimited"):
        return None
    return float(v)


def server_budget(ledger: SpendLedger) -> Budget:
    """Budget for one analysis started from the web API.

    LLM_BUDGET_PER_JOB_USD (default 3) and LLM_BUDGET_PER_DAY_USD (default 10); "off" disables a limit.
    """
    return Budget(per_job=_env_float("LLM_BUDGET_PER_JOB_USD", "3"),
                  per_day=_env_float("LLM_BUDGET_PER_DAY_USD", "10"), ledger=ledger)
