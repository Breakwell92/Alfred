"""Eval cases: one request, what should handle it and which tools it should (not) call.

Cases live in `evals/cases/*.yaml`, each file a list of cases. Cases tagged `pending` need a
specialist or connector that isn't built yet and are skipped; their names for specialists and
tools are placeholders until those tickets land.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from alfred_server.agents.context import RequestContext

CASES_DIR = Path(__file__).parent / "cases"

PENDING = "pending"
# `must_not_call: ["*"]` means the request must not call any tool.
ANY_TOOL = "*"

# Every case runs at this moment unless its `context` says otherwise, so time answers and the
# `get_time` fixture agree and runs are repeatable.
DEFAULT_NOW = "2026-10-24T14:05:00+01:00"
DEFAULT_CONTEXT: dict[str, Any] = {
    "now": DEFAULT_NOW,
    "timezone": "Europe/London",
    "device_type": "phone",
    "location": "home",
}

Tier = Literal["local", "cloud"]


class ExpectedTool(BaseModel):
    """A tool the request must call, with arguments that must include `args_subset`."""

    model_config = ConfigDict(extra="forbid")

    name: str
    args_subset: dict[str, Any] = Field(default_factory=dict)


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    input: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    # `RequestContext` fields, over `DEFAULT_CONTEXT` (e.g. `active_room: kitchen`).
    context: dict[str, Any] = Field(default_factory=dict)
    expected_specialist: str
    expected_tools: list[ExpectedTool] = Field(default_factory=list)
    must_not_call: list[str] = Field(default_factory=list)
    max_tier: Tier = "cloud"
    # Text the reply must contain (case-insensitive), e.g. the time from the `get_time` fixture.
    reply_contains: list[str] = Field(default_factory=list)
    notes: str = ""

    @field_validator("context")
    @classmethod
    def _valid_context(cls, value: dict[str, Any]) -> dict[str, Any]:
        if "session_id" in value:
            raise ValueError("session_id is set by the runner")
        RequestContext.model_validate({**DEFAULT_CONTEXT, **value, "session_id": "check"})
        return value

    @model_validator(mode="after")
    def _consistent(self) -> EvalCase:
        clash = {t.name for t in self.expected_tools} & set(self.must_not_call)
        if clash or (ANY_TOOL in self.must_not_call and self.expected_tools):
            raise ValueError(f"tools both expected and forbidden: {sorted(clash) or ANY_TOOL}")
        return self

    @property
    def pending(self) -> bool:
        return PENDING in self.tags

    def request_context(self, session_id: str) -> RequestContext:
        fields = {**DEFAULT_CONTEXT, **self.context, "session_id": session_id}
        if isinstance(fields["now"], str):
            fields["now"] = datetime.fromisoformat(fields["now"])
        return RequestContext.model_validate(fields)


def load_cases(paths: Iterable[Path] | None = None) -> list[EvalCase]:
    """Every case in `paths` (default: `evals/cases/*.yaml`). Ids must be unique."""
    files = sorted(CASES_DIR.glob("*.yaml")) if paths is None else list(paths)
    cases: list[EvalCase] = []
    seen: dict[str, Path] = {}
    for path in files:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        if not isinstance(raw, list):
            raise ValueError(f"{path}: expected a list of cases")
        for item in raw:
            try:
                case = EvalCase.model_validate(item)
            except ValueError as e:
                raise ValueError(f"{path}: {item.get('id', '?')}: {e}") from e
            if case.id in seen:
                raise ValueError(f"{path}: duplicate case id {case.id!r} (also in {seen[case.id]})")
            seen[case.id] = path
            cases.append(case)
    return cases


def select(
    cases: Sequence[EvalCase], tags: Sequence[str] = (), ids: Sequence[str] = ()
) -> tuple[list[EvalCase], list[EvalCase]]:
    """(to run, skipped as pending) among the cases with any of `tags` and any of `ids`."""
    chosen = [
        c for c in cases if (not tags or set(tags) & set(c.tags)) and (not ids or c.id in ids)
    ]
    return [c for c in chosen if not c.pending], [c for c in chosen if c.pending]
