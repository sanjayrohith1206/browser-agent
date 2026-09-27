"""Check that a local Ollama model can drive the browser agent.

Runs through the backend's own model layer (LangChain + the agent's tool
definitions), so a pass here means the agent can use the model:

  1. a plain reply (and how fast the model generates);
  2. a tool call: the model must ask to read the page, then answer from the
     page text it is given.

Run from backend/:  uv run python ../local-llm/check_model.py --model qwen3.5:4b
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from app.agent.prompts import SYSTEM_PROMPT
from app.config import get_settings
from app.llm.factory import build_provider
from app.tools.registry import ToolRegistry

PAGE = {
    "url": "https://example.com/solar",
    "title": "Solar Power Basics",
    "description": "How solar panels turn sunlight into electricity.",
    "lang": "en",
    "headings": [{"level": 1, "text": "Solar Power Basics"}],
    "text": (
        "Solar panels are made of photovoltaic cells that convert sunlight directly into "
        "electricity. A typical 3 kW rooftop system in India costs around ₹1,50,000 before "
        "subsidies and pays for itself in five to seven years."
    ),
    "total_chars": 205,
    "truncated": False,
}


def rate(generation: object, seconds: float) -> str:
    usage = getattr(getattr(generation, "message", None), "usage_metadata", None) or {}
    out = usage.get("output_tokens")
    return f"{seconds:.1f}s" + (f", {out / seconds:.1f} tokens/s" if out and seconds else "")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--context", type=int, default=32768)
    args = parser.parse_args()

    settings = get_settings().model_copy(
        update={
            "llm_provider": "ollama",
            "llm_model": args.model,
            "llm_context_window": args.context,
        }
    )
    provider = build_provider(settings)
    tools = ToolRegistry.from_file(settings.shared_tools_path).as_langchain_tools()

    print(f"   model {args.model}, context {args.context} tokens")

    # 1. Plain reply (the first call also loads the model into memory).
    start = time.perf_counter()
    reply = await provider.generate([HumanMessage("Reply with just the word: ready")])
    elapsed = time.perf_counter() - start
    print(f"   plain reply: {reply.text.strip()[:40]!r} ({rate(reply, elapsed)})")

    # 2. Tool use, exactly as the agent does it.
    messages = [
        SystemMessage(SYSTEM_PROMPT),
        HumanMessage(
            "The user is currently looking at: Solar Power Basics — https://example.com/solar\n\n"
            "User request:\nRead this page and tell me how much a rooftop system costs."
        ),
    ]
    start = time.perf_counter()
    step = await provider.generate_with_tools(messages, tools)
    calls = [(c["name"], c["args"]) for c in step.message.tool_calls]
    print(f"   tool call: {calls or 'none'} ({rate(step, time.perf_counter() - start)})")
    if not step.message.tool_calls:
        print("FAIL: the model answered without using a tool. Try a larger model.", file=sys.stderr)
        return 1

    messages.append(step.message)
    for call in step.message.tool_calls:
        payload = {"success": True, "tool": call["name"], "result": PAGE}
        messages.append(
            ToolMessage(json.dumps(payload), tool_call_id=call["id"] or "", name=call["name"])
        )
    start = time.perf_counter()
    answer = await provider.generate_with_tools(messages, tools)
    text = answer.text.strip()
    print(f"   answer ({rate(answer, time.perf_counter() - start)}): {text[:200]!r}")
    if "1,50,000" not in text and "150,000" not in text and "1.5 lakh" not in text.lower():
        print("FAIL: the answer did not use the page content.", file=sys.stderr)
        return 1
    print("PASS: the model can drive the browser agent.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
