"""Router overhead benchmark: time the `local-router` classification call (STU-55).

The router adds one classifier call before the specialist runs; this times it end to end
(Agno agent → LiteLLM proxy → Ollama) over a set of typical requests, after a warm-up so the
model is loaded. Model selection itself is a couple of budget-store reads (sub-millisecond when
cloud is off) and isn't included.

Needs the compose stack up and `ALFRED_LITELLM_API_KEY` set (e.g. in server/.env).

    uv run python scripts/bench_router.py [--rounds 3]
"""

import argparse
import asyncio
import statistics
import time

from agno.db.in_memory import InMemoryDb

from alfred_server.agents.context import RequestContext
from alfred_server.agents.registry import get_registry
from alfred_server.agents.router import ModelFactory, Router
from alfred_server.budget.models import BudgetDecision, EscalationReason
from alfred_server.config import get_settings
from alfred_server.models.gateway import ModelAlias, gateway_model

REQUESTS = [
    "What time is it?",
    "Turn off the kitchen lights.",
    "What's the capital of Peru?",
    "Ask Claude to write a sonnet about autumn.",
    "Plan my week around my calendar and book a restaurant for Friday.",
    "Summarise the last forty emails from work.",
    "Use the smart model to explain quantum tunnelling.",
    "What's on my calendar tomorrow? Email Sam the times.",
    "Play something relaxing in the living room.",
    "Remind me to call Mum at six.",
]


class _NoBudget:
    """Classification never asks the budget; the router just needs one."""

    def preferred_aliases(self, task_type: str) -> list[ModelAlias]:
        return []

    def decide(
        self,
        alias: ModelAlias,
        prompt_tokens: int,
        expected_output_tokens: int,
        reason: EscalationReason = "auto",
    ) -> BudgetDecision:
        raise NotImplementedError


async def main(rounds: int) -> None:
    settings = get_settings()
    factory: ModelFactory = lambda alias, metadata, **kwargs: gateway_model(  # noqa: E731
        alias, metadata=metadata, settings=settings, **kwargs
    )
    router = Router(get_registry(), _NoBudget(), factory, InMemoryDb())
    ctx = RequestContext.build(timezone=settings.timezone, device_type="text", session_id="bench")

    await router.classify("warm up", ctx)
    timings: list[float] = []
    for i in range(rounds):
        for text in REQUESTS:
            start = time.perf_counter()
            c = await router.classify(text, ctx)
            timings.append((time.perf_counter() - start) * 1000)
            if i == 0:
                print(
                    f"{timings[-1]:6.0f} ms  {c.complexity:8} tools={c.expected_tool_calls} "
                    f"long={c.needs_long_context!s:5} cloud={c.explicit_cloud_request!s:5} "
                    f"{c.preferred_provider or '-':6}  {text}"
                )
    timings.sort()
    p90 = timings[int(len(timings) * 0.9) - 1]
    print(
        f"\n{len(timings)} calls: median {statistics.median(timings):.0f} ms, "
        f"p90 {p90:.0f} ms, max {timings[-1]:.0f} ms"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rounds", type=int, default=3)
    asyncio.run(main(parser.parse_args().rounds))
