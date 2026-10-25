"""Running a routine: its steps' tools are called directly, in order, with no model.

Tools come from the same loader the router uses (`ConnectorService.load_tools`), so a disabled
or disconnected connector's steps fail rather than run. Each step is checked against the
current tool policy right before it runs. A failed step is reported and the rest still run,
unless it's marked `stop_on_error`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any

from agno.tools.function import Function, FunctionCall
from agno.tools.mcp import MCPTools

from alfred_server.confirmations.policy import ToolPolicy, summarise_call
from alfred_server.connectors.models import ToolError
from alfred_server.routines.models import Routine, RoutineResult, RoutineStep, StepResult
from alfred_server.routines.validation import step_problem

if TYPE_CHECKING:
    from alfred_server.agents.router import ToolLoader

logger = logging.getLogger(__name__)

Sleep = Callable[[float], Awaitable[None]]


def describe_step(policy: ToolPolicy, step: RoutineStep) -> str:
    """A short phrase for a step, e.g. "turn on light lounge lights"."""
    text = summarise_call(policy, step.tool, step.args)
    return text[:1].lower() + text[1:]


def _sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "!", "?")) else text + "."


async def load_functions(
    loader: ToolLoader | None, policy: ToolPolicy, connector_ids: set[str]
) -> dict[str, Function]:
    """The loaded tools of these connectors, by tool name, ready to call.

    A specialist whose tools fail to load contributes nothing (its steps then fail).
    """
    if loader is None:
        return {}
    specialists = set()
    for connector_id in connector_ids:
        connector = policy.registry.get(connector_id)
        if connector is not None:
            specialists.add(connector.specialist)
    functions: dict[str, Function] = {}
    for specialist in sorted(specialists):
        try:
            toolkits = await loader(specialist)
        except Exception:
            logger.exception("couldn't load tools for %s", specialist)
            continue
        for toolkit in toolkits:
            fresh = not isinstance(toolkit, MCPTools)
            for registry in (toolkit.functions, toolkit.async_functions):
                for name, function in registry.items():
                    found = policy.registry.tool_meta(name)
                    if found is None or found[0].id not in connector_ids:
                        continue
                    if fresh:
                        # Agno toolkits are built per load: their functions still need their
                        # schema and argument validation set up (MCP ones come ready).
                        function.process_entrypoint()
                    functions[name] = function
    return functions


class RoutineRunner:
    def __init__(
        self,
        loader: ToolLoader | None,
        *,
        sleep: Sleep = asyncio.sleep,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.loader = loader
        self.sleep = sleep
        self.clock = clock

    async def run(self, routine: Routine, policy: ToolPolicy, trigger: str) -> RoutineResult:
        started = self.clock()
        functions = await load_functions(self.loader, policy, {s.connector for s in routine.steps})
        results: list[StepResult] = []
        stopped = False
        for index, step in enumerate(routine.steps, start=1):
            if stopped:
                results.append(
                    StepResult(
                        index=index,
                        tool=step.tool,
                        ok=False,
                        message="Skipped.",
                        ms=0.0,
                        skipped=True,
                    )
                )
                continue
            if step.delay_s:
                await self.sleep(step.delay_s)
            result = await self._step(index, step, functions, policy, routine.approved_tools)
            results.append(result)
            if not result.ok and step.stop_on_error:
                stopped = True
        ms = round((self.clock() - started) * 1000, 1)
        reply = self._reply(routine, results, policy, stopped)
        ran = [r for r in results if not r.skipped]
        logger.info(
            "routine %r ran %d of %d steps in %.0f ms (no LLM, trigger %s): %s",
            routine.name,
            len(ran),
            len(results),
            ms,
            trigger,
            "; ".join(f"{r.index} {r.tool} {'ok' if r.ok else 'failed'}" for r in ran),
        )
        return RoutineResult(
            routine_id=routine.id,
            name=routine.name,
            reply=reply,
            steps=results,
            ms=ms,
            trigger=trigger,
        )

    async def _step(
        self,
        index: int,
        step: RoutineStep,
        functions: Mapping[str, Function],
        policy: ToolPolicy,
        approved: list[str],
    ) -> StepResult:
        started = self.clock()

        def done(ok: bool, message: str) -> StepResult:
            ms = round((self.clock() - started) * 1000, 1)
            return StepResult(index=index, tool=step.tool, ok=ok, message=message, ms=ms)

        problem = step_problem(step, policy, approved)
        if problem:
            return done(False, problem)
        function = functions.get(step.tool)
        if function is None:
            connector = policy.registry.get(step.connector)
            name = connector.name if connector else step.connector
            return done(False, f"{name} isn't connected.")
        try:
            outcome = await FunctionCall(function=function, arguments=dict(step.args)).aexecute()
        except Exception as exc:
            logger.exception("routine step %d (%s) raised", index, step.tool)
            return done(False, f"It went wrong ({type(exc).__name__}).")
        if outcome.status != "success":
            logger.warning("routine step %d (%s) failed: %s", index, step.tool, outcome.error)
            return done(False, "It couldn't run with the saved settings.")
        result: Any = outcome.result
        message = str(result) if result is not None else "Done."
        return done(not isinstance(result, ToolError), message)

    @staticmethod
    def _reply(
        routine: Routine, results: list[StepResult], policy: ToolPolicy, stopped: bool
    ) -> str:
        parts = [_sentence(routine.spoken_reply)]
        for result in results:
            if result.ok or result.skipped:
                continue
            step = routine.steps[result.index - 1]
            parts.append(
                f"Step {result.index} ({describe_step(policy, step)}) failed: "
                + _sentence(result.message)
            )
        if stopped and any(r.skipped for r in results):
            parts.append("I stopped there.")
        return " ".join(parts)
