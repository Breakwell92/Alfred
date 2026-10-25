import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from agno.agent import Agent
from agno.db.base import SessionType
from agno.db.sqlite import AsyncSqliteDb
from agno.models.base import Model

from alfred_server.agents.base import LOW_CONFIDENCE_MARKER
from alfred_server.agents.context import RequestContext
from alfred_server.agents.registry import GENERAL, Specialist, SpecialistRegistry, get_registry
from alfred_server.agents.router import (
    NOT_SURE_REPLY,
    AgentRunError,
    Classification,
    Done,
    Failed,
    ModelChoice,
    Route,
    Router,
    Token,
)
from alfred_server.config import Settings
from alfred_server.models.gateway import CallMetadata, ModelAlias, gateway_model
from tests.llm_fakes import (
    FakeProxy,
    reply,
    route_completion,
    streamed_completion,
    streamed_tool_call,
    tool_call_completion,
)
from tests.router_fakes import FakeBudget, FakeGate, deny, read_only_connector

CLOUD: list[ModelAlias] = ["cloud-claude", "cloud-gpt", "cloud-gemini"]


def ctx(session_id: str = "s1") -> RequestContext:
    return RequestContext.build(timezone="Europe/London", device_type="text", session_id=session_id)


class Harness:
    """A router over a fake proxy (every alias), async SQLite storage and a fake budget."""

    def __init__(self, tmp_path: Path, budget: FakeBudget | None = None) -> None:
        self.proxy = FakeProxy()
        self.budget = budget or FakeBudget()
        self.db = AsyncSqliteDb(db_file=str(tmp_path / "agno.db"))
        self.metadata: list[CallMetadata] = []
        # The tools these tests give their agents are declared `read`, so they aren't gated.
        self.gate = FakeGate(read_only_connector("broken", "switch_on"))
        self.router = Router(get_registry(), self.budget, self.model, self.db, self.gate)

    def model(self, alias: ModelAlias, metadata: CallMetadata, **kwargs: Any) -> Model:
        self.metadata.append(metadata)
        return gateway_model(
            alias,
            metadata=metadata,
            settings=Settings(_env_file=None, litellm_api_key="sk-test"),
            http_client=self.proxy.async_client(),
            **kwargs,
        )

    def models_called(self) -> list[str]:
        return [self.proxy.body(i)["model"] for i in range(len(self.proxy.requests))]

    def route(self, choice: ModelChoice, c: Classification | None = None) -> Route:
        return Route(c or Classification(), get_registry().get(GENERAL), choice)

    async def stored_runs(self, session_id: str = "s1") -> list[Any]:
        session: Any = await self.db.get_session(session_id, session_type=SessionType.AGENT)
        return list(session.runs) if session else []


@pytest.fixture
def h(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


def select(h: Harness, text: str = "do it", **fields: Any) -> ModelChoice:
    return asyncio.run(h.router.select_model(text, Classification(**fields)))


# --- Model selection -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fields", "alias"),
    [
        ({"complexity": "simple", "expected_tool_calls": 0}, "local-router"),
        ({"complexity": "simple", "expected_tool_calls": 1}, "local-router"),
        ({"complexity": "simple", "expected_tool_calls": 2}, "local-main"),
        ({"complexity": "standard", "expected_tool_calls": 0}, "local-main"),
        ({"complexity": "standard", "expected_tool_calls": 3}, "local-main"),
    ],
)
def test_local_tiers(h: Harness, fields: dict[str, Any], alias: str) -> None:
    h.budget.aliases = CLOUD  # cloud on, but nothing calls for it

    choice = select(h, **fields)

    assert choice.alias == alias
    assert choice.tier == "local"
    assert h.budget.calls == []


@pytest.mark.parametrize(
    "fields",
    [
        {"complexity": "complex"},
        {"expected_tool_calls": 4},
        {"needs_long_context": True},
        {"explicit_cloud_request": True, "preferred_provider": "claude"},
    ],
)
def test_cloud_off_stays_local(h: Harness, fields: dict[str, Any]) -> None:
    h.budget.decisions = {"cloud-claude": deny("cloud_disabled", "Cloud models are turned off.")}

    choice = select(h, **fields)

    assert choice.tier == "local"
    assert choice.alias == "local-main"


def test_cloud_off_says_why_to_an_explicit_request(h: Harness) -> None:
    h.budget.decisions = {"cloud-gpt": deny("cloud_disabled", "Cloud models are turned off.")}

    choice = select(h, explicit_cloud_request=True, preferred_provider="gpt")

    assert choice.announcement == "Cloud models are turned off."
    assert h.budget.calls == [("cloud-gpt", "user_request")]


def test_cloud_off_is_silent_for_automatic_escalation(h: Harness) -> None:
    choice = select(h, complexity="complex")

    assert choice.announcement is None
    assert h.budget.calls == []  # never asks about prices when cloud is off


def test_explicit_request_uses_the_named_provider(h: Harness) -> None:
    h.budget.aliases = CLOUD

    choice = select(h, explicit_cloud_request=True, preferred_provider="gemini")

    assert choice.alias == "cloud-gemini"
    assert choice.reason == "explicit_request"
    assert h.budget.calls == [("cloud-gemini", "user_request")]


def test_explicit_request_without_a_provider_uses_the_preferred_order(h: Harness) -> None:
    h.budget.aliases = ["cloud-gpt", "cloud-claude"]

    choice = select(h, explicit_cloud_request=True)

    assert choice.alias == "cloud-gpt"


def test_explicit_request_for_a_disabled_provider_says_so(h: Harness) -> None:
    h.budget.aliases = ["cloud-claude", "cloud-gpt"]  # Gemini turned off

    choice = select(h, explicit_cloud_request=True, preferred_provider="gemini")

    assert choice.alias == "cloud-claude"
    assert choice.announcement == "Gemini is turned off, so I asked Claude."


def test_explicit_request_for_a_disabled_provider_refused_by_budget(h: Harness) -> None:
    h.budget.aliases = ["cloud-claude"]  # Gemini turned off
    h.budget.decisions = {"cloud-claude": deny("over_remaining", "That would cost too much.")}

    choice = select(h, explicit_cloud_request=True, preferred_provider="gemini")

    assert choice.alias == "local-main"
    assert choice.announcement == "Gemini is turned off. That would cost too much."


def test_explicit_request_refused_by_budget_says_why(h: Harness) -> None:
    h.budget.aliases = CLOUD
    h.budget.decisions = {"cloud-claude": deny("over_remaining", "That would cost too much.")}

    choice = select(h, explicit_cloud_request=True, preferred_provider="claude")

    assert choice.alias == "local-main"
    assert choice.budget_code == "over_remaining"
    assert choice.announcement == "That would cost too much."


def test_complex_escalates_as_a_hard_task(h: Harness) -> None:
    h.budget.aliases = CLOUD

    choice = select(h, complexity="complex")

    assert choice.alias == "cloud-claude"
    assert choice.tier == "cloud"
    assert choice.reason == "complex"
    assert h.budget.calls == [("cloud-claude", "hard_task")]
    assert h.budget.task_types == ["default"]


def test_many_tool_calls_escalates_automatically(h: Harness) -> None:
    h.budget.aliases = CLOUD

    choice = select(h, expected_tool_calls=4)

    assert choice.alias == "cloud-claude"
    assert h.budget.calls == [("cloud-claude", "auto")]
    assert h.budget.task_types == ["multi_step_tools"]


def test_long_context_escalates_automatically(h: Harness) -> None:
    h.budget.aliases = ["cloud-gemini", "cloud-claude"]

    choice = select(h, needs_long_context=True)

    assert choice.alias == "cloud-gemini"
    assert h.budget.calls == [("cloud-gemini", "auto")]
    assert h.budget.task_types == ["long_document"]


def test_restricted_tier_keeps_automatic_escalation_local(h: Harness) -> None:
    h.budget.aliases = CLOUD
    h.budget.decisions = {"cloud-claude": deny("restricted", "Saving it.", tier="restricted")}

    choice = select(h, expected_tool_calls=5)

    assert choice.alias == "local-main"
    assert choice.budget_code == "restricted"
    assert choice.announcement is None
    assert len(h.budget.calls) == 1  # the tier refuses every alias; no point asking again


def test_exhausted_announces_once(h: Harness) -> None:
    h.budget.aliases = CLOUD
    h.budget.decisions = {"cloud-claude": deny("exhausted", "Over budget.", announce=True)}

    assert select(h, complexity="complex").announcement == "Over budget."

    h.budget.decisions = {"cloud-claude": deny("exhausted", "Over budget.", announce=False)}
    assert select(h, complexity="complex").announcement is None


def test_unpriced_alias_moves_on_to_the_next(h: Harness) -> None:
    h.budget.aliases = CLOUD
    h.budget.decisions = {"cloud-claude": deny("price_unavailable")}

    choice = select(h, complexity="complex")

    assert choice.alias == "cloud-gpt"
    assert [alias for alias, _ in h.budget.calls] == ["cloud-claude", "cloud-gpt"]


# --- Classification ------------------------------------------------------------------------


def classify(h: Harness, text: str) -> Classification:
    return asyncio.run(h.router.classify(text, ctx()))


def test_classifier_output_maps_to_the_ticket_fields(h: Harness) -> None:
    h.proxy.responses.append(
        route_completion(level="complex", long=True, tools=4, cloud=True, provider="gemini")
    )

    c = classify(h, "Read these and plan my week")

    assert c == Classification(
        specialist="general",
        complexity="complex",
        needs_long_context=True,
        expected_tool_calls=4,
        explicit_cloud_request=True,
        preferred_provider="gemini",
    )
    body = h.proxy.body()
    assert body["model"] == "local-router"
    assert body["temperature"] == 0
    assert "agent:router" in body["metadata"]["tags"]
    assert "general = " in body["messages"][0]["content"]


def test_classifier_garbage_gives_the_default(h: Harness) -> None:
    h.proxy.responses.append(reply("I think this is a general question."))

    assert classify(h, "Hi") == Classification()


def test_classifier_model_failure_gives_the_default(h: Harness) -> None:
    assert classify(h, "Hi") == Classification()  # nothing queued: the call fails


def test_unknown_specialist_becomes_general(h: Harness) -> None:
    h.proxy.responses.append(route_completion(agent="plumber"))

    assert classify(h, "Fix the sink").specialist == GENERAL


@pytest.mark.parametrize(
    ("text", "provider"),
    [
        ("Ask Claude to write me a poem", "claude"),
        ("Alfred, use ChatGPT for this", "gpt"),
        ("please ask Gemini about the tides", "gemini"),
        ("Have Claude: summarise this", "claude"),
        ("Use the smart model to explain tides", None),
        ("use the smarter model.", None),
    ],
)
def test_explicit_cloud_phrases_are_never_missed(
    h: Harness, text: str, provider: str | None
) -> None:
    h.proxy.responses.append(route_completion(cloud=False))

    c = classify(h, text)

    assert c.explicit_cloud_request is True
    assert c.preferred_provider == provider


@pytest.mark.parametrize(
    "text",
    [
        "How do I use ChatGPT to write a CV?",
        "Can I use Claude Code on Windows?",
        "Should I get the Gemini app?",
        "Ask Claude Code questions in the terminal",
        "My friend said to ask Claude to help",
    ],
)
def test_questions_about_the_products_stay_local(h: Harness, text: str) -> None:
    h.proxy.responses.append(route_completion(cloud=False))

    c = classify(h, text)

    assert c.explicit_cloud_request is False
    assert c.preferred_provider is None


# --- Running and fallback ------------------------------------------------------------------


def run(h: Harness, route: Route, text: str = "What's the answer?") -> Any:
    return asyncio.run(h.router.run(route, text, ctx()))


def test_run_records_the_routing_decision(h: Harness) -> None:
    h.proxy.responses.append(reply("42."))

    result = run(h, h.route(ModelChoice("local-main", "standard")))

    assert result.reply == "42."
    routing = asyncio.run(h.stored_runs())[0].metadata["routing"]
    assert routing["alias"] == "local-main"
    assert routing["tier"] == "local"
    assert routing["reason"] == "standard"
    assert routing["specialist"] == GENERAL
    assert routing["classification"]["complexity"] == "standard"
    assert h.metadata[-1] == CallMetadata(agent=GENERAL, trigger="text")


def test_low_confidence_retries_on_cloud_and_forgets_the_local_run(h: Harness) -> None:
    h.budget.aliases = CLOUD
    h.proxy.responses += [reply(LOW_CONFIDENCE_MARKER), reply("It's 42.")]

    result = run(h, h.route(ModelChoice("local-main", "standard")))

    assert result.reply == "It's 42."
    assert h.models_called() == ["local-main", "cloud-claude"]
    assert h.budget.calls == [("cloud-claude", "hard_task")]
    runs = asyncio.run(h.stored_runs())
    assert [r.metadata["routing"]["alias"] for r in runs] == ["cloud-claude"]
    assert runs[0].metadata["routing"]["reason"] == "fallback from local-main"
    # The retry's history doesn't hold the failed attempt.
    texts = [str(m.get("content")) for m in h.proxy.body(1)["messages"]]
    assert LOW_CONFIDENCE_MARKER not in texts


def test_two_failed_tool_calls_retry_on_cloud(h: Harness) -> None:
    h.budget.aliases = CLOUD
    h.proxy.responses += [
        tool_call_completion("no_such_tool", {}, "call_1"),
        tool_call_completion("no_such_tool", {}, "call_2"),
        reply("I couldn't do that."),
        reply("Done properly."),
    ]

    result = run(h, h.route(ModelChoice("local-main", "standard")))

    assert result.reply == "Done properly."
    assert h.models_called()[-1] == "cloud-claude"


def broken() -> str:
    """Always fails, like Home Assistant being offline."""
    raise RuntimeError("boom")


def switch_on() -> str:
    """Does something in the world."""
    return "Switched on."


def with_tools(h: Harness, *tools: Any) -> Route:
    """A `general` specialist with `tools`, on `local-main`, with cloud on."""

    def factory(ctx: RequestContext, model: Model, db: Any) -> Agent:
        return Agent(model=model, db=db, tools=list(tools), telemetry=False)

    registry = SpecialistRegistry()
    registry.register(Specialist(GENERAL, "anything", factory))
    h.router.registry = registry
    h.budget.aliases = CLOUD
    return Route(Classification(), registry.get(GENERAL), ModelChoice("local-main", "standard"))


def test_tools_that_raise_are_not_retried(h: Harness) -> None:
    # The model called the tool correctly; cloud would fail the same way and cost money.
    route = with_tools(h, broken)
    h.proxy.responses += [
        tool_call_completion("broken", {}, "call_1"),
        tool_call_completion("broken", {}, "call_2"),
        reply("Home Assistant isn't answering."),
    ]

    result = run(h, route)

    assert result.reply == "Home Assistant isn't answering."
    assert h.budget.calls == []


def test_a_successful_tool_call_prevents_a_retry(h: Harness) -> None:
    route = with_tools(h, switch_on)
    h.proxy.responses += [
        tool_call_completion("switch_on", {}, "call_1"),
        tool_call_completion("no_such_tool", {}, "call_2"),
        tool_call_completion("no_such_tool", {}, "call_3"),
        reply("Done, mostly."),
    ]

    result = run(h, route)

    assert result.reply == "Done, mostly."
    assert h.budget.calls == []  # retrying would switch it on twice
    assert len(asyncio.run(h.stored_runs())) == 1  # and the record is kept


def test_a_successful_tool_call_prevents_a_marker_retry(h: Harness) -> None:
    route = with_tools(h, switch_on)
    h.proxy.responses += [tool_call_completion("switch_on", {}), reply(LOW_CONFIDENCE_MARKER)]

    result = run(h, route)

    assert result.reply == NOT_SURE_REPLY
    assert h.budget.calls == []


def test_retry_goes_ahead_when_the_failed_run_isnt_deleted(
    h: Harness, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def not_deleted(run_id: str) -> bool:
        return False

    monkeypatch.setattr(h.db, "delete_run", not_deleted)
    h.budget.aliases = CLOUD
    h.proxy.responses += [reply(LOW_CONFIDENCE_MARKER), reply("It's 42.")]

    result = run(h, h.route(ModelChoice("local-main", "standard")))

    assert result.reply == "It's 42."
    assert "wasn't deleted" in caplog.text


def test_one_failed_tool_call_is_not_retried(h: Harness) -> None:
    h.budget.aliases = CLOUD
    h.proxy.responses += [tool_call_completion("no_such_tool", {}), reply("Done anyway.")]

    result = run(h, h.route(ModelChoice("local-main", "standard")))

    assert result.reply == "Done anyway."
    assert h.budget.calls == []


def test_low_confidence_without_cloud_says_not_sure(h: Harness) -> None:
    h.proxy.responses.append(reply(LOW_CONFIDENCE_MARKER))

    result = run(h, h.route(ModelChoice("local-main", "standard")))

    assert result.reply == NOT_SURE_REPLY
    assert h.models_called() == ["local-main"]


def test_fallback_refused_by_budget_announces_once(h: Harness) -> None:
    h.budget.aliases = CLOUD
    h.budget.decisions = {"cloud-claude": deny("exhausted", "Over budget.", announce=True)}
    h.proxy.responses.append(reply(LOW_CONFIDENCE_MARKER))

    result = run(h, h.route(ModelChoice("local-main", "standard")))

    assert result.reply == f"Over budget. {NOT_SURE_REPLY}"


def test_cloud_run_is_never_retried(h: Harness) -> None:
    h.budget.aliases = CLOUD
    h.proxy.responses.append(reply(LOW_CONFIDENCE_MARKER))

    result = run(h, h.route(ModelChoice("cloud-claude", "complex")))

    assert result.reply == NOT_SURE_REPLY
    assert h.budget.calls == []


def test_announcement_goes_before_the_reply(h: Harness) -> None:
    h.proxy.responses.append(reply("Here you go."))

    result = run(h, h.route(ModelChoice("local-main", "x", announcement="Cloud is off.")))

    assert result.reply == "Cloud is off. Here you go."


def test_failed_run_raises(h: Harness) -> None:
    with pytest.raises(AgentRunError):
        run(h, h.route(ModelChoice("local-main", "standard")))


# --- Streaming -----------------------------------------------------------------------------


def stream(h: Harness, route: Route) -> list[Token | Done | Failed]:
    async def collect() -> list[Token | Done | Failed]:
        items: AsyncIterator[Token | Done | Failed] = h.router.stream(route, "Q?", ctx())
        return [item async for item in items]

    return asyncio.run(collect())


def tokens(items: list[Token | Done | Failed]) -> list[str]:
    return [item.text for item in items if isinstance(item, Token)]


def test_stream_sends_tokens_then_done(h: Harness) -> None:
    h.proxy.responses.append(streamed_completion("It's ", "42."))

    items = stream(h, h.route(ModelChoice("local-main", "standard")))

    assert tokens(items) == ["It's ", "42."]
    assert isinstance(items[-1], Done) and items[-1].reply == "It's 42."


def test_stream_holds_back_a_marker_and_retries_on_cloud(h: Harness) -> None:
    h.budget.aliases = CLOUD
    h.proxy.responses += [
        streamed_completion("<<UN", "SURE>>"),
        streamed_completion("Cloud ", "answer."),
    ]

    items = stream(h, h.route(ModelChoice("local-main", "standard")))

    assert tokens(items) == ["Cloud ", "answer."]
    done = items[-1]
    assert isinstance(done, Done)
    assert done.reply == "Cloud answer."
    assert done.route.choice.alias == "cloud-claude"
    assert h.models_called() == ["local-main", "cloud-claude"]


def test_stream_two_failed_tool_calls_retry_on_cloud(h: Harness) -> None:
    h.budget.aliases = CLOUD
    h.proxy.responses += [
        streamed_tool_call("no_such_tool", {}, "call_1"),
        streamed_tool_call("no_such_tool", {}, "call_2"),
        streamed_completion("<<UN", "SURE>>"),
        streamed_completion("Cloud ", "answer."),
    ]

    items = stream(h, h.route(ModelChoice("local-main", "standard")))

    assert tokens(items) == ["Cloud ", "answer."]
    assert h.models_called() == ["local-main"] * 3 + ["cloud-claude"]


def test_stream_with_a_successful_tool_call_is_not_retried(h: Harness) -> None:
    route = with_tools(h, switch_on)
    h.proxy.responses += [
        streamed_tool_call("switch_on", {}, "call_1"),
        streamed_tool_call("no_such_tool", {}, "call_2"),
        streamed_tool_call("no_such_tool", {}, "call_3"),
        streamed_completion("<<UN", "SURE>>"),
    ]

    items = stream(h, route)

    assert tokens(items) == [NOT_SURE_REPLY]
    assert h.budget.calls == []


def test_stream_that_already_sent_text_is_not_retried(h: Harness) -> None:
    h.budget.aliases = CLOUD
    h.proxy.responses += [
        streamed_tool_call("no_such_tool", {}, "call_1"),
        streamed_tool_call("no_such_tool", {}, "call_2"),
        streamed_completion("I ", "couldn't."),
    ]

    items = stream(h, h.route(ModelChoice("local-main", "standard")))

    assert tokens(items) == ["I ", "couldn't."]
    assert h.budget.calls == []


def test_stream_marker_without_cloud_says_not_sure(h: Harness) -> None:
    h.proxy.responses.append(streamed_completion("<<UN", "SURE>>"))

    items = stream(h, h.route(ModelChoice("local-main", "standard")))

    assert tokens(items) == [NOT_SURE_REPLY]


def test_stream_text_that_starts_like_the_marker_is_sent(h: Harness) -> None:
    h.proxy.responses.append(streamed_completion("<", "b>bold"))

    items = stream(h, h.route(ModelChoice("local-main", "standard")))

    assert "".join(tokens(items)) == "<b>bold"


def test_stream_announcement_is_the_first_token(h: Harness) -> None:
    h.proxy.responses.append(streamed_completion("Hi."))

    items = stream(h, h.route(ModelChoice("local-main", "x", announcement="Over budget.")))

    assert tokens(items) == ["Over budget. ", "Hi."]
    assert isinstance(items[-1], Done) and items[-1].reply == "Over budget. Hi."


def test_stream_failure(h: Harness) -> None:
    items = stream(h, h.route(ModelChoice("local-main", "standard")))

    assert isinstance(items[-1], Failed)


# --- Registry ------------------------------------------------------------------------------


def _factory(ctx: RequestContext, model: Model, db: Any) -> Agent:
    return Agent(model=model, db=db)


def test_registry_rejects_duplicates() -> None:
    registry = SpecialistRegistry()
    registry.register(Specialist(GENERAL, "anything", _factory))

    with pytest.raises(ValueError, match="already registered"):
        registry.register(Specialist(GENERAL, "again", _factory))


def test_registry_unknown_name_gives_general() -> None:
    registry = SpecialistRegistry()
    registry.register(Specialist(GENERAL, "anything", _factory))
    registry.register(Specialist("home", "lights and heating", _factory))

    assert registry.get("plumber").name == GENERAL
    assert registry.get("home").name == "home"
    assert registry.describe() == "general = anything\nhome = lights and heating"


def test_default_registry_has_general() -> None:
    assert get_registry().names() == [GENERAL]
