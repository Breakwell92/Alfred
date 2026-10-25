"""Tool tiers, gating agents, summaries, yes/no parsing and who may approve what."""

from datetime import timedelta

import pytest
from agno.agent import Agent
from agno.run.agent import RunOutput
from agno.tools import Toolkit
from agno.tools.function import Function

from alfred_server.agents.context import DeviceType, RequestContext
from alfred_server.confirmations.models import Channel, Tier, can_approve
from alfred_server.confirmations.policy import (
    ToolPolicy,
    gate_agent,
    highest,
    join_summaries,
    summarise_call,
)
from alfred_server.confirmations.service import (
    ConfirmationService,
    ConfirmationsUnavailableError,
    ask_prompt,
    denied_reply,
    expired_reply,
    parse_yes_no,
)
from alfred_server.confirmations.store import ConfirmationStore
from alfred_server.connectors.builtin.test_messages import send_test_message
from alfred_server.connectors.registry import default_registry
from tests.budget_helpers import memory_engine
from tests.connector_helpers import Clock, build_registry


def policy(**overrides: Tier) -> ToolPolicy:
    keys = {"send_test_message": "test_messages", "get_time": "time"}
    return ToolPolicy(default_registry(), {(keys[k], k): v for k, v in overrides.items()})


def undeclared_tool() -> str:
    """Nothing declares this tool."""
    return "?"


def test_declared_risk_is_the_tier() -> None:
    assert policy().tier("get_time") == "read"
    assert policy().tier("send_test_message") == "write"


def test_overrides_relax_and_tighten() -> None:
    assert policy(send_test_message="read").tier("send_test_message") == "read"
    assert policy(get_time="irreversible").tier("get_time") == "irreversible"


def test_undeclared_tools_are_irreversible() -> None:
    assert policy().tier("undeclared_tool") == "irreversible"


def test_gated_names() -> None:
    assert policy().gated_names() == ["send_test_message"]
    assert policy(send_test_message="read").gated_names() == []


def test_highest() -> None:
    assert highest(["read", "irreversible", "write"]) == "irreversible"
    assert highest([]) == "read"


def test_gate_agent_marks_callables() -> None:
    from alfred_server.agents.echo import make_get_time

    agent = Agent(tools=[make_get_time("Europe/London"), send_test_message, undeclared_tool])

    gate_agent(agent, policy())

    assert isinstance(agent.tools, list)
    flags = {t.name: t.requires_confirmation for t in agent.tools if isinstance(t, Function)}
    assert flags == {"get_time": False, "send_test_message": True, "undeclared_tool": True}


def test_gate_agent_rewrites_a_toolkit_every_time() -> None:
    toolkit = Toolkit(name="test_messages", tools=[send_test_message])
    agent = Agent(tools=[toolkit])

    gate_agent(agent, policy())
    assert toolkit.functions["send_test_message"].requires_confirmation is True
    assert toolkit.requires_confirmation_tools == ["send_test_message"]

    # A shared (cached MCP) toolkit loses the flag once the tool is relaxed.
    gate_agent(Agent(tools=[toolkit]), policy(send_test_message="read"))
    assert toolkit.functions["send_test_message"].requires_confirmation is False
    assert toolkit.requires_confirmation_tools == []


def test_gate_agent_drops_provider_builtin_tools() -> None:
    agent = Agent(tools=[{"type": "web_search"}, send_test_message])

    gate_agent(agent, policy())

    assert isinstance(agent.tools, list)
    assert [getattr(t, "name", None) for t in agent.tools] == ["send_test_message"]


def test_summary_uses_the_template() -> None:
    summary = summarise_call(
        policy(), "send_test_message", {"recipient": "Sam", "text": "Running 10 mins late"}
    )
    assert summary == "Send a test message to Sam: 'Running 10 mins late'"


def test_summary_falls_back_without_a_template_or_arguments() -> None:
    p = ToolPolicy(build_registry())
    assert summarise_call(p, "acme_delete", {"what": "all"}) == "Run acme_delete (what='all')"
    assert summarise_call(policy(), "send_test_message", {"recipient": "Sam"}) == (
        "Run send_test_message (recipient='Sam')"
    )
    assert summarise_call(p, "acme_delete", None) == "Run acme_delete"


def test_join_summaries() -> None:
    assert join_summaries(["A"]) == "A"
    assert join_summaries(["A", "B", "C"]) == "A, B and C"


@pytest.mark.parametrize(
    "text",
    ["yes", "Yes.", "yeah", "OK", "okay!", "go ahead", "Do it", "yes please", "Alfred, yes"],
)
def test_yes(text: str) -> None:
    assert parse_yes_no(text) == "yes"


@pytest.mark.parametrize("text", ["no", "No!", "nope", "don't", "Don’t send it", "cancel"])
def test_no(text: str) -> None:
    assert parse_yes_no(text) == "no"


@pytest.mark.parametrize(
    "text", ["yes, and also text Mum", "what's the weather?", "no idea", "ok so what time is it"]
)
def test_anything_else_is_not_an_answer(text: str) -> None:
    assert parse_yes_no(text) is None


@pytest.mark.parametrize(
    ("tier", "channel", "allowed"),
    [
        ("write", "text", True),
        ("write", "voice", True),
        ("write", "app_ui", True),
        ("write", "scheduled", False),
        ("irreversible", "text", False),
        ("irreversible", "voice", False),
        ("irreversible", "app_ui", True),
        ("irreversible", "scheduled", False),
    ],
)
def test_who_may_approve(tier: Tier, channel: Channel, allowed: bool) -> None:
    assert can_approve(tier, channel) is allowed


# --- Service -------------------------------------------------------------------------------


def service(clock: Clock) -> ConfirmationService:
    return ConfirmationService(ConfirmationStore(memory_engine()), default_registry(), clock=clock)


def paused_run() -> RunOutput:
    from agno.models.response import ToolExecution
    from agno.run.requirement import RunRequirement

    tool = ToolExecution(
        tool_name="send_test_message",
        tool_args={"recipient": "Sam", "text": "Hi"},
        requires_confirmation=True,
    )
    return RunOutput(run_id="run-1", requirements=[RunRequirement(tool_execution=tool)])


def ctx(device_type: DeviceType = "text") -> RequestContext:
    return RequestContext.build(timezone="Europe/London", device_type=device_type, session_id="s1")


def test_create_lists_and_expires() -> None:
    clock = Clock()
    s = service(clock)

    c = s.create(
        paused_run(), specialist="general", alias="local-main", ctx=ctx(), policy=s.policy()
    )

    assert (c.tier, c.channel, c.tools) == ("write", "text", ["send_test_message"])
    assert c.expires_at - c.created_at == timedelta(minutes=2)
    assert [p.id for p in s.pending("s1")] == [c.id]
    assert s.pending("other") == []
    assert s.notifications() == []

    clock.advance(seconds=121)
    assert s.pending() == []
    assert [d.id for d in s.store.due(clock())] == [c.id]


def test_a_confirmation_is_answered_once() -> None:
    s = service(Clock())
    c = s.create(
        paused_run(), specialist="general", alias="local-main", ctx=ctx(), policy=s.policy()
    )

    assert s.resolve(c, "approved") is True
    assert s.resolve(c, "denied") is False
    stored = s.get(c.id)
    assert stored is not None and stored.status == "approved"


def test_scheduled_runs_wait_longer_and_notify() -> None:
    s = service(Clock())

    c = s.create(
        paused_run(),
        specialist="general",
        alias="local-main",
        ctx=ctx("scheduled"),
        policy=s.policy(),
    )

    assert c.channel == "scheduled"
    assert c.expires_at - c.created_at == timedelta(hours=1)
    [note] = s.notifications()
    assert (note.kind, note.ref_id, note.body) == ("confirmation", c.id, c.summary)


def test_policy_fails_closed_when_overrides_are_unreadable() -> None:
    class BrokenStore(ConfirmationStore):
        def overrides(self) -> dict[tuple[str, str], Tier]:
            raise OSError("postgres down")

    s = ConfirmationService(BrokenStore(memory_engine()), default_registry())
    with pytest.raises(ConfirmationsUnavailableError):
        s.policy()


def test_replies() -> None:
    s = service(Clock())
    c = s.create(
        paused_run(), specialist="general", alias="local-main", ctx=ctx(), policy=s.policy()
    )

    assert ask_prompt(c) == (
        "I need your OK first: Send a test message to Sam: 'Hi'. Shall I go ahead?"
    )
    assert denied_reply(c) == "OK, I won't send a test message to Sam: 'Hi'."
    assert expired_reply(c) == "I didn't hear back, so I didn't send a test message to Sam: 'Hi'."
    irreversible = c.model_copy(update={"tier": "irreversible"})
    assert ask_prompt(irreversible).endswith("Please approve it in the Alfred app.")


def test_current_tier_follows_tightening_but_not_relaxing() -> None:
    s = service(Clock())
    c = s.create(
        paused_run(), specialist="general", alias="local-main", ctx=ctx(), policy=s.policy()
    )
    assert s.current_tier(c) == "write"

    s.set_override("test_messages", "send_test_message", "irreversible")
    assert s.current_tier(c) == "irreversible"

    # Relaxing out of irreversible is the server CLI's job (ADR 0016).
    s.set_override("test_messages", "send_test_message", "read", allow_relaxing_irreversible=True)
    assert s.current_tier(c) == "write"
