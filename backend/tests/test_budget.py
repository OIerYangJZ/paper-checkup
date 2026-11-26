"""Pricing and spend limits (no network)."""

import asyncio
from datetime import datetime, timezone

from paper_checkup.llm.pricing import Budget, SpendLedger, cost_usd
from paper_checkup.llm.provider import LLMClient, LLMConfig
from paper_checkup.pipeline import run_pipeline

from test_pipeline_fake_llm import FIXTURE, FakeProvider

PEAK = datetime(2026, 11, 25, 6, 30, tzinfo=timezone.utc)      # Wednesday 06:30 UTC: DeepSeek peak
OFFPEAK = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)


def test_cost_formulas():
    u = {"input_tokens": 200_000, "cache_read_tokens": 120_000, "cache_write_tokens": 20_000, "output_tokens": 20_000}
    # Sol: 60K fresh x $2 + 120K x $0.10 + 20K x $2.50 + 20K x $10
    assert abs(cost_usd("openai:gpt-6.2-sol", u) - (0.12 + 0.012 + 0.05 + 0.2)) < 1e-9
    # Long context (>272K input in one request): input/cache x2, output x1.5
    big = {"input_tokens": 300_000, "output_tokens": 10_000}
    assert abs(cost_usd("openai:gpt-6.2-sol", big) - (300_000 * 2 * 2 + 10_000 * 10 * 1.5) / 1e6) < 1e-9
    # DeepSeek flash: no cache-write premium; off-peak is half price.
    d = {"input_tokens": 1_000_000, "cache_read_tokens": 900_000, "output_tokens": 100_000}
    peak = (900_000 * 0.006 + 100_000 * 0.3 + 100_000 * 1.2) / 1e6
    assert abs(cost_usd("deepseek:deepseek-flash", d, PEAK) - peak) < 1e-9
    assert abs(cost_usd("deepseek:deepseek-flash", d, OFFPEAK) - peak / 2) < 1e-9
    # Unknown models are billed at the most expensive known rates.
    assert abs(cost_usd("openai:some-new-model", {"input_tokens": 100_000}) - 0.8) < 1e-9


def test_ledger_persists(tmp_path):
    p = tmp_path / "spend.json"
    led = SpendLedger(p)
    led.add(1.25)
    led.add(0.5)
    assert abs(SpendLedger(p).spent_today() - 1.75) < 1e-9


class PricedFake(FakeProvider):
    """Every call costs $1 (1M fresh input tokens on an unknown model would be $8, so use a priced spec)."""

    def __init__(self):
        super().__init__()
        self.model = "gpt-6.2-sol"
        self.provider = "openai"

    async def complete_json(self, *a, **kw):
        data, _ = await super().complete_json(*a, **kw)
        return data, {"input_tokens": 500_000, "output_tokens": 0}   # 500K x $2 = $1.00


def _cfg(tmp_path):
    return LLMConfig(enabled=True, provider="openai",
                     models={"extract": "openai:gpt-6.2-sol", "author": "openai:gpt-6.2-sol",
                             "reviewer": "openai:gpt-6.2-sol"}, cache_dir=tmp_path, concurrency=1)


def test_job_budget_stops_calls_and_keeps_meta_reserve(monkeypatch, tmp_path):
    fake = PricedFake()
    monkeypatch.setattr(LLMClient, "provider_for", lambda self, role: fake)
    budget = Budget(per_job=6.0)
    rep = asyncio.run(run_pipeline(FIXTURE, config=_cfg(tmp_path), use_cache=False, budget=budget))
    p = rep.pipeline
    # Calls are refused once 90% of the budget is spent (the rest is kept for the meta-review); the reading guide
    # may still run up to 110% (it costs about a cent in reality, $1 in this fake). So: limit + one in-flight call
    # + the guide.
    assert p.cost_usd <= 6.0 + 2.0 and p.budget_note and "已用完" in p.budget_note
    assert p.budget_usd == 6.0
    skipped = [n for n in rep.nodes if (n.review or {}).get("reason") == "budget"]
    assert skipped, "some review targets should be skipped for budget"
    assert any(s.name == "review" and "因预算未审" in (s.detail or "") for s in p.stages)


def test_daily_budget_exhausted_runs_deterministic_only(monkeypatch, tmp_path):
    fake = PricedFake()
    monkeypatch.setattr(LLMClient, "provider_for", lambda self, role: fake)
    led = SpendLedger(tmp_path / "spend.json")
    led.add(10.0)
    rep = asyncio.run(run_pipeline(FIXTURE, config=_cfg(tmp_path), use_cache=False,
                                   budget=Budget(per_job=3.0, per_day=10.0, ledger=led)))
    assert not fake.calls
    assert rep.pipeline.llm_enabled is False and "今天的大模型预算" in (rep.pipeline.budget_note or "")
    assert rep.nodes  # deterministic results are still there
