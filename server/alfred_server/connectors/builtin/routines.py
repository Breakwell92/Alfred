"""`routines` connector: the tools the `routines` specialist creates and deletes routines with.

Creating one is a normal agent turn: the specialist looks devices up with read tools, then calls
`save_routine` with concrete steps. `save_routine` is gated, so the run pauses and the
confirmation reads every step back ("I need your OK first: save the routine 'movie time' …");
approving it is also Dan's approval for any step that normally asks first. Running a routine
later needs no model at all (`alfred_server.routines`). Design: docs/decisions/0021-routines.md.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any

from agno.tools import Toolkit
from pydantic import ValidationError

from alfred_server.confirmations.policy import ToolPolicy, join_summaries
from alfred_server.connectors.models import (
    AgnoToolkitSource,
    Connector,
    Credentials,
    ToolError,
    ToolMeta,
)
from alfred_server.routines.models import RoutineSpec, RoutineStep
from alfred_server.routines.runner import describe_step
from alfred_server.routines.service import RoutineService, get_routine_service
from alfred_server.routines.validation import ROUTINES_CONNECTOR_ID, RoutineInvalidError

UNAVAILABLE = "Routines aren't available right now."


def _phrases(args: Mapping[str, Any]) -> list[str]:
    phrases = args.get("phrases") or []
    return [str(p) for p in phrases] if isinstance(phrases, list) else [str(phrases)]


def summarise_save(policy: ToolPolicy, args: Mapping[str, Any]) -> str:
    """The read-back for `save_routine`: name, phrases and every step, flagging steps that
    normally ask first (approving the save approves them for this routine)."""
    name = str(args.get("name") or "routine")
    parts = []
    for raw in args.get("steps") or []:
        try:
            step = RoutineStep.model_validate(raw)
        except ValidationError:
            parts.append(f"an unreadable step ({raw!r})")
            continue
        text = describe_step(policy, step)
        if step.delay_s:
            text = f"after {step.delay_s:g} seconds, {text}"
        if policy.tier(step.tool) != "read":
            text += " (it will run without asking each time)"
        parts.append(text)
    said = " or ".join(f"'{p}'" for p in _phrases(args)) or f"'{name}'"
    return f"Save the routine '{name}', run by saying {said}: {join_summaries(parts)}"


class RoutinesTools(Toolkit):
    """`list_step_tools`, `save_routine`, `list_routines`, `delete_routine`.

    `service` is None when routines can't be reached (no database): every tool says so.
    """

    def __init__(self, service: RoutineService | None) -> None:
        self.service = service
        super().__init__(
            name="routines",
            tools=[
                self.list_step_tools,
                self.save_routine,
                self.list_routines,
                self.delete_routine,
            ],
        )

    async def list_step_tools(self) -> str:
        """The tools a routine's steps can use, with their arguments. Call this before
        save_routine to see which connector each tool belongs to and what arguments it takes.

        Returns:
            JSON: a list of {connector, tool, description, asks_first, parameters}.
        """
        if self.service is None:
            return ToolError(UNAVAILABLE)
        try:
            catalog = await self.service.step_catalog()
        except Exception:
            return ToolError(UNAVAILABLE)
        if not catalog:
            return ToolError("No services are connected, so a routine has nothing to do yet.")
        return json.dumps(catalog, separators=(",", ":"))

    async def save_routine(
        self,
        name: str,
        phrases: list[str],
        steps: list[dict[str, Any]],
        reply: str | None = None,
    ) -> str:
        """Save a routine: when Dan says one of the phrases, the steps run exactly as given,
        without you. Replaces any routine with the same name.

        Args:
            name: A short name, e.g. "movie time".
            phrases: What Dan will say to run it, e.g. ["movie time"]. Usually just the name.
            steps: The tool calls, in order. Each is {"connector": ..., "tool": ..., "args":
                {...}} using a connector, tool and arguments from list_step_tools, with
                concrete values (real room and device names, no placeholders). Optional:
                "delay_s" (seconds to wait first) and "stop_on_error" (true to skip the rest
                if this step fails).
            reply: What to say after it runs; leave out to say the name.

        Returns:
            Whether it was saved.
        """
        if self.service is None:
            return ToolError(UNAVAILABLE)
        try:
            spec = RoutineSpec.model_validate(
                {"name": name, "phrases": phrases, "steps": steps, "reply": reply}
            )
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(p) for p in first["loc"])
            return ToolError(f"The routine isn't valid ({where}: {first['msg']}).")
        service = self.service
        try:
            policy = await asyncio.to_thread(service.policy)
            # Saving was approved with every step read back, including the ones that normally
            # ask first: that approval is what lets them run without asking (ADR 0021).
            approved = sorted({s.tool for s in spec.steps if policy.tier(s.tool) == "write"})
            spec = spec.model_copy(update={"approved_tools": approved})
            await service.check_arguments(spec)
            routine = await asyncio.to_thread(service.save, spec)
        except RoutineInvalidError as exc:
            return ToolError(f"I couldn't save it. {exc}")
        except Exception:
            return ToolError(UNAVAILABLE)
        return f"Saved the {routine.name} routine. Say '{routine.phrases[0]}' to run it."

    async def list_routines(self) -> str:
        """Dan's saved routines: names, phrases and what each does.

        Returns:
            One line per routine, or that there are none.
        """
        if self.service is None:
            return ToolError(UNAVAILABLE)
        service = self.service
        try:
            routines = await asyncio.to_thread(service.all)
            policy = await asyncio.to_thread(service.policy)
        except Exception:
            return ToolError(UNAVAILABLE)
        if not routines:
            return "There are no routines yet."
        lines = []
        for r in routines:
            steps = join_summaries([describe_step(policy, s) for s in r.steps])
            off = "" if r.enabled else " (turned off)"
            lines.append(f"{r.name}{off}: say {' or '.join(r.phrases)}; it will {steps}.")
        return "\n".join(lines)

    async def delete_routine(self, name: str) -> str:
        """Delete one of Dan's routines by name.

        Args:
            name: The routine's name, e.g. "movie time".

        Returns:
            Whether it was deleted.
        """
        if self.service is None:
            return ToolError(UNAVAILABLE)
        service = self.service
        try:
            routine = await asyncio.to_thread(service.find, name)
            if routine is None:
                return ToolError(f"There's no routine called {name}.")
            await asyncio.to_thread(service.delete, routine.id)
        except Exception:
            return ToolError(UNAVAILABLE)
        return f"Deleted the {routine.name} routine."


def build_routines_toolkit(_creds: Credentials) -> Toolkit:
    try:
        service: RoutineService | None = get_routine_service()
    except Exception:  # e.g. no database configured
        service = None
    return RoutinesTools(service)


ROUTINES_CONNECTOR = Connector(
    id=ROUTINES_CONNECTOR_ID,
    name="Routines",
    description='Phrases that run a saved list of actions instantly ("movie time").',
    auth_type="none",
    tool_source=AgnoToolkitSource(build_routines_toolkit),
    tools=(
        ToolMeta(
            name="list_step_tools",
            description="The tools a routine can use.",
            risk="read",
        ),
        ToolMeta(
            name="save_routine",
            description="Save a routine.",
            risk="write",
            summarise=summarise_save,
        ),
        ToolMeta(name="list_routines", description="Dan's routines.", risk="read"),
        ToolMeta(
            name="delete_routine",
            description="Delete a routine.",
            risk="write",
            confirm_summary="Delete the routine '{name}'",
        ),
    ),
    specialist="routines",
)
