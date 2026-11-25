import asyncio
from pathlib import Path
from paper_checkup.settings import load_env
load_env()
from paper_checkup.llm.provider import LLMClient, LLMConfig
from paper_checkup.deterministic import analyze_structure
from paper_checkup.llm.context import digest

async def main():
    client = LLMClient(LLMConfig.from_env(), use_cache=False)
    prov = client.provider_for("extract")
    print("breakpoints:", prov.breakpoints, "official:", prov.official)
    dig = digest(analyze_structure(Path("../papers/raw/1212.0506/src")), max_statement=160)
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"], "additionalProperties": False}
    for q in ("How many items in the catalog are lemmas? Reply with n.", "How many items are definitions? Reply with n."):
        data, usage = await prov.complete_json("You count things.", [dig, q], schema, "probe", 4000, "extract")
        print(data, usage)

asyncio.run(main())
