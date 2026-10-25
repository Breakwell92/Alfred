"""What a routine may contain. Checked when it's saved and again before every step runs.

- Every step is a tool its connector declares (so it has a risk and a tier).
- No `irreversible` tool, by declared risk or by Dan's override: those always need an approval
  in the app (ADR 0013, 0016).
- A gated (`write` tier) tool only if Dan approved it for this routine when it was saved.
- Trigger phrases are unique across routines and never a plain "yes" or "no", which answers a
  pending confirmation instead.
"""

from __future__ import annotations

from collections.abc import Iterable

from alfred_server.confirmations.policy import ToolPolicy
from alfred_server.confirmations.service import parse_yes_no
from alfred_server.routines.matcher import normalise
from alfred_server.routines.models import Routine, RoutineSpec, RoutineStep

# The routines connector's own tools (saving, deleting) can't be steps.
ROUTINES_CONNECTOR_ID = "routines"


class RoutineInvalidError(ValueError):
    """The routine can't be saved as it is; the message says why, in a sentence to say."""


def step_problem(step: RoutineStep, policy: ToolPolicy, approved: Iterable[str]) -> str | None:
    """Why `step` can't run (a sentence), or None if it may."""
    if step.connector == ROUTINES_CONNECTOR_ID:
        return "A routine can't change routines."
    connector = policy.registry.get(step.connector)
    if connector is None:
        return f"There's no {step.connector} connector."
    meta = next((t for t in connector.tools if t.name == step.tool), None)
    if meta is None:
        return f"{connector.name} has no tool called {step.tool}."
    tier = policy.tier(step.tool)
    if meta.risk == "irreversible" or tier == "irreversible":
        return f"{step.tool} can't be in a routine: it needs approval in the Alfred app every time."
    if tier != "read" and step.tool not in set(approved):
        return f"{step.tool} normally asks first, and wasn't approved for this routine."
    return None


def check_routine(
    spec: RoutineSpec, policy: ToolPolicy, others: Iterable[Routine], *, routine_id: str | None
) -> RoutineSpec:
    """`spec` if it may be saved (with `approved_tools` trimmed to its own gated steps).

    `others` is every saved routine; `routine_id` is the one being updated, if any.
    Raises `RoutineInvalidError`.
    """
    approved = {t for t in spec.approved_tools if any(s.tool == t for s in spec.steps)}
    for index, step in enumerate(spec.steps, start=1):
        problem = step_problem(step, policy, approved)
        if problem:
            raise RoutineInvalidError(f"Step {index}: {problem}")
    taken: dict[str, str] = {}
    names: set[str] = set()
    for other in others:
        if other.id == routine_id:
            continue
        names.add(other.name.casefold())
        for phrase in other.phrases:
            taken[normalise(phrase)] = other.name
    if spec.name.casefold() in names:
        raise RoutineInvalidError(f"There's already a routine called {spec.name}.")
    for phrase in spec.phrases:
        said = normalise(phrase)
        if not said:
            raise RoutineInvalidError(f"'{phrase}' has nothing to say.")
        if parse_yes_no(phrase) is not None:
            raise RoutineInvalidError(f"'{phrase}' would answer a question, so it can't be one.")
        if said in taken:
            raise RoutineInvalidError(f"'{phrase}' already runs the {taken[said]} routine.")
    gated = sorted(t for t in approved if policy.tier(t) != "read")
    return spec.model_copy(update={"approved_tools": gated})
