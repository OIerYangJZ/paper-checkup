import asyncio, time
from paper_checkup.settings import load_env
load_env()
from paper_checkup.llm.provider import LLMClient, LLMConfig

async def main():
    cfg = LLMConfig.from_env()
    print("enabled:", cfg.enabled, "| models:", cfg.models, "| reason:", cfg.reason_disabled)
    client = LLMClient(cfg, use_cache=False)
    prov = client.provider_for("reviewer")
    t = time.time()
    data, usage = await prov.complete_json(
        "You are a terse math checker.",
        ["Is 91 prime? Answer with the JSON object only."],
        {"type": "object", "properties": {"prime": {"type": "boolean"}, "reason": {"type": "string"}},
         "required": ["prime", "reason"], "additionalProperties": False},
        "ping", 64000, "reviewer")
    print(f"{time.time() - t:.1f}s", data, usage, "| param:", prov._max_tokens_param, "| json_mode:", prov.json_mode,
          "| effort:", prov.reasoning_effort)

asyncio.run(main())
