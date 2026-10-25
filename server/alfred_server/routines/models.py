"""What a routine is, and what running one reports.

A routine is a name, the phrases that trigger it and a list of concrete connector tool calls.
It runs without a model: the steps are executed as saved. See docs/decisions/0021-routines.md.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

# The longest wait a step may ask for before it runs.
MAX_DELAY_S = 300.0


class RoutineStep(BaseModel):
    """One tool call: `tool` (declared by `connector`) with `args`, after `delay_s` seconds."""

    connector: str = Field(min_length=1, max_length=40)
    tool: str = Field(min_length=1, max_length=128)
    args: dict[str, Any] = Field(default_factory=dict)
    delay_s: float = Field(default=0.0, ge=0.0, le=MAX_DELAY_S)
    # If this step fails, skip the rest. Otherwise later steps still run.
    stop_on_error: bool = False


class RoutineSpec(BaseModel):
    """A routine as created or updated (REST body, `save_routine`)."""

    name: str = Field(min_length=1, max_length=80)
    phrases: list[str] = Field(min_length=1, max_length=10)
    steps: list[RoutineStep] = Field(min_length=1, max_length=20)
    # What's said after a run. None says the routine's name ("Movie time.").
    reply: str | None = Field(default=None, max_length=200)
    enabled: bool = True
    # Gated (`write` tier) step tools Dan approved for this routine when it was saved: they
    # run without asking each time. `irreversible` tools can never be approved.
    approved_tools: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("name", "reply")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("phrases")
    @classmethod
    def _phrases(cls, value: list[str]) -> list[str]:
        phrases = [p.strip() for p in value]
        if any(not p or len(p) > 80 for p in phrases):
            raise ValueError("each phrase must be 1 to 80 characters")
        return phrases


class Routine(RoutineSpec):
    id: str
    created_at: datetime
    updated_at: datetime

    @property
    def spoken_reply(self) -> str:
        if self.reply:
            return self.reply
        return self.name[:1].upper() + self.name[1:].rstrip(".") + "."


class StepResult(BaseModel):
    index: int  # 1-based, as said to the user
    tool: str
    ok: bool
    message: str
    ms: float
    # Not run because an earlier `stop_on_error` step failed.
    skipped: bool = False


class RoutineResult(BaseModel):
    routine_id: str
    name: str
    reply: str
    steps: list[StepResult]
    ms: float
    trigger: str

    @property
    def ok(self) -> bool:
        return all(s.ok for s in self.steps)

    def turn_info(self) -> dict[str, Any]:
        """The `turn` summary for the web chat and `turn.metadata`, shaped like a routed turn's
        (`chat.turn_info`) so the same displays work: no model, no tokens, no cost."""
        return {
            "alias": "none",
            "tier": "local",
            "reason": f"routine: {self.name}",
            "budget_code": None,
            "budget_tier": None,
            "specialist": "routine",
            "tools": [s.tool for s in self.steps if not s.skipped],
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_gbp": None,
            "routine": {
                "id": self.routine_id,
                "name": self.name,
                "ms": self.ms,
                "steps": [s.model_dump() for s in self.steps],
            },
        }
