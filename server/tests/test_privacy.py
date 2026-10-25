"""Privacy routing (STU-58, docs/decisions/0026-privacy-routing.md).

`local_only` tool output stays on local models unless the privacy settings allow it: the router
keeps a run local when such a tool is loaded, and the gateway model refuses a cloud call that
carries such output anyway (from history), which the router retries locally.
"""

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from agno.agent import Agent
from agno.db.base import SessionType
from agno.db.sqlite import AsyncSqliteDb
from agno.models.base import Model
from agno.models.message import Message
from agno.tools import Toolkit
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from alfred_server.agents.base import LOW_CONFIDENCE_MARKER
from alfred_server.agents.context import RequestContext
from alfred_server.agents.registry import GENERAL, Specialist, SpecialistRegistry
from alfred_server.agents.router import (
    PRIVACY_ANNOUNCEMENT,
    Classification,
    Done,
    ModelChoice,
    Route,
    Router,
    Token,
)
from alfred_server.config import Settings
from alfred_server.connectors.models import AgnoToolkitSource, Connector, ToolMeta
from alfred_server.connectors.registry import ConnectorRegistry, default_registry
from alfred_server.main import create_app
from alfred_server.models.gateway import (
    CallMetadata,
    GatewayModel,
    ModelAlias,
    PrivateContentError,
    gateway_model,
)
from alfred_server.privacy.models import PrivacySettings, blocked_tools
from alfred_server.privacy.service import (
    PrivacyService,
    UnknownConnectorError,
    get_privacy_service,
)
from alfred_server.privacy.store import PrivacyStore
from tests.budget_helpers import memory_engine
from tests.llm_fakes import (
    FakeProxy,
    reply,
    route_completion,
    streamed_completion,
    tool_call_completion,
)
from tests.router_fakes import FakeBudget, FakeGate, FakePrivacy

CLOUD: list[ModelAlias] = ["cloud-claude", "cloud-gpt", "cloud-gemini"]
COMMS = "comms"
TOKEN = "test-token-not-a-secret"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

# A Comms-like connector: its inbox tool returns message bodies, so it's `local_only`.
COMMS_CONNECTOR = Connector(
    id="comms_test",
    name="Comms test",
    description="Messages for the privacy tests.",
    auth_type="none",
    tool_source=AgnoToolkitSource(lambda _creds: Toolkit(name="comms_test")),
    tools=(
        ToolMeta(
            name="read_inbox", description="Today's messages.", risk="read", privacy="local_only"
        ),
        ToolMeta(name="count_unread", description="How many are unread.", risk="read"),
        ToolMeta(
            name="send_reply",
            description="Reply to a message.",
            risk="write",
            confirm_summary="Reply to {to}",
        ),
    ),
    specialist=COMMS,
)

DENY = PrivacySettings(allow_private_to_cloud=False)


def read_inbox() -> str:
    """Today's messages."""
    return "Sam: the door code is 4417."


def count_unread() -> str:
    """How many messages are unread."""
    return "3"


def send_reply(to: str) -> str:
    """Reply to a message."""
    return f"Replied to {to}."


def ctx(session_id: str = "s1") -> RequestContext:
    return RequestContext.build(timezone="Europe/London", device_type="text", session_id=session_id)


class Harness:
    """A router with `general` (no tools) and a Comms-like specialist, cloud on."""

    def __init__(self, tmp_path: Path, privacy: FakePrivacy | None = None) -> None:
        self.proxy = FakeProxy()
        self.budget = FakeBudget(CLOUD)
        self.db = AsyncSqliteDb(db_file=str(tmp_path / "agno.db"))
        self.gate = FakeGate(COMMS_CONNECTOR)
        self.privacy = privacy or FakePrivacy(DENY)
        self.comms_tools: list[Any] = [read_inbox]
        registry = SpecialistRegistry()
        registry.register(Specialist(GENERAL, "anything", self._factory([])))
        registry.register(Specialist(COMMS, "messages", lambda c, m, d: self._agent(m, d)))
        self.registry = registry
        self.router = Router(
            registry, self.budget, self.model, self.db, self.gate, privacy=self.privacy
        )

    def _factory(self, tools: list[Any]) -> Any:
        return lambda c, m, d: Agent(
            model=m, db=d, tools=list(tools), add_history_to_context=True, telemetry=False
        )

    def _agent(self, model: Model, db: Any) -> Agent:
        return self._factory(self.comms_tools)(None, model, db)

    def model(self, alias: ModelAlias, metadata: CallMetadata, **kwargs: Any) -> Model:
        return gateway_model(
            alias,
            metadata=metadata,
            settings=Settings(_env_file=None, litellm_api_key="sk-test"),
            http_client=self.proxy.async_client(),
            **kwargs,
        )

    def models_called(self) -> list[str]:
        return [self.proxy.body(i)["model"] for i in range(len(self.proxy.requests))]

    def route(self, specialist: str = COMMS) -> Route:
        """A complex request for `specialist`, routed (before its tools load) to cloud."""
        c = Classification(specialist=specialist, complexity="complex")
        return Route(c, self.registry.get(specialist), ModelChoice("cloud-claude", "complex"))

    def run(self, route: Route) -> Any:
        """Run `route`; the stored runs are in `self.runs` afterwards."""

        async def go() -> Any:
            result = await self.router.run(route, "What did Sam say?", ctx())
            self.runs = await self.stored_runs()
            return result

        return asyncio.run(go())

    def stream(self, route: Route) -> list[Any]:
        async def collect() -> list[Any]:
            return [item async for item in self.router.stream(route, "What did Sam say?", ctx())]

        return asyncio.run(collect())

    async def stored_runs(self) -> list[Any]:
        session: Any = await self.db.get_session("s1", session_type=SessionType.AGENT)
        return list(session.runs) if session else []


@pytest.fixture
def h(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


# --- Settings ----------------------------------------------------------------------------------


def test_default_allows_private_output_to_cloud() -> None:
    settings = PrivacySettings()

    assert settings.allow_private_to_cloud is True
    assert blocked_tools(default_registry(), settings) == frozenset()


def test_disallowing_blocks_every_local_only_tool() -> None:
    registry = ConnectorRegistry([COMMS_CONNECTOR])

    assert blocked_tools(registry, DENY) == {"read_inbox"}


@pytest.mark.parametrize(
    ("settings", "blocked"),
    [
        (PrivacySettings(allow_private_to_cloud=True, connectors={"comms_test": False}), True),
        (PrivacySettings(allow_private_to_cloud=False, connectors={"comms_test": True}), False),
    ],
)
def test_connector_override_wins(settings: PrivacySettings, blocked: bool) -> None:
    registry = ConnectorRegistry([COMMS_CONNECTOR])

    assert ("read_inbox" in blocked_tools(registry, settings)) is blocked


def test_store_round_trips_and_seeds_defaults() -> None:
    store = PrivacyStore(memory_engine())
    assert store.load() == PrivacySettings()

    saved = PrivacySettings(allow_private_to_cloud=False, connectors={"home_assistant": True})
    store.save(saved)
    store.save(saved)  # second save updates the one row

    assert store.load() == saved


def test_service_rejects_unknown_connectors() -> None:
    service = PrivacyService(PrivacyStore(memory_engine()), default_registry())

    with pytest.raises(UnknownConnectorError):
        service.save_settings(PrivacySettings(connectors={"no_such": False}))


# --- API ---------------------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("ALFRED_API_TOKEN", TOKEN)
    service = PrivacyService(PrivacyStore(memory_engine()), default_registry())
    app = create_app()
    app.dependency_overrides[get_privacy_service] = lambda: service
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize("method", ["GET", "PUT"])
def test_routes_require_token(client: TestClient, method: str) -> None:
    assert client.request(method, "/settings/privacy").status_code == 401


def test_get_then_put_round_trips(client: TestClient) -> None:
    assert client.get("/settings/privacy", headers=AUTH).json() == {
        "allow_private_to_cloud": True,
        "connectors": {},
    }
    new = {"allow_private_to_cloud": False, "connectors": {"home_assistant": True}}

    assert client.put("/settings/privacy", json=new, headers=AUTH).json() == new
    assert client.get("/settings/privacy", headers=AUTH).json() == new


@pytest.mark.parametrize(
    "body",
    [
        {"allow_private_to_cloud": False},  # partial: would reset the overrides
        {"allow_private_to_cloud": True, "connectors": {"no_such": False}},
    ],
)
def test_put_rejects_bad_bodies(client: TestClient, body: dict[str, Any]) -> None:
    assert client.put("/settings/privacy", json=body, headers=AUTH).status_code == 422


def test_unreadable_settings_answer_503(monkeypatch: pytest.MonkeyPatch) -> None:
    class Broken(PrivacyStore):
        def load(self) -> PrivacySettings:
            raise OperationalError("SELECT", {}, Exception("down"))

    monkeypatch.setenv("ALFRED_API_TOKEN", TOKEN)
    app = create_app()
    service = PrivacyService(Broken(memory_engine()), default_registry())
    app.dependency_overrides[get_privacy_service] = lambda: service

    assert TestClient(app).get("/settings/privacy", headers=AUTH).status_code == 503


# --- Gateway guard -----------------------------------------------------------------------------


def crafted(tool_name: str) -> list[Message]:
    """A conversation holding the result of `tool_name`, as Agno builds one."""
    return [
        Message(role="user", content="What did Sam say?"),
        Message(
            role="assistant",
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": tool_name, "arguments": "{}"},
                }
            ],
        ),
        Message(role="tool", content=read_inbox(), tool_call_id="call_1", tool_name=tool_name),
    ]


def guarded(proxy: FakeProxy, alias: ModelAlias, *, sync: bool = False) -> GatewayModel:
    client = proxy.client() if sync else proxy.async_client()
    return gateway_model(
        alias,
        settings=Settings(_env_file=None, litellm_api_key="sk-test"),
        http_client=client,
        private_tools=frozenset({"read_inbox"}),
    )


def test_guard_blocks_a_crafted_cloud_call_with_a_private_tool_result() -> None:
    proxy = FakeProxy(reply("ok"))
    model = guarded(proxy, "cloud-claude")
    assistant = Message(role="assistant")

    with pytest.raises(PrivateContentError, match="read_inbox"):
        asyncio.run(model.ainvoke(crafted("read_inbox"), assistant))

    assert proxy.requests == []  # nothing left the server
    assert model.refused_tools == ("read_inbox",)


def test_guard_blocks_every_call_path() -> None:
    proxy = FakeProxy()
    sync = guarded(proxy, "cloud-gpt", sync=True)
    model = guarded(proxy, "cloud-gpt")
    assistant = Message(role="assistant")

    async def stream() -> None:
        async for _ in model.ainvoke_stream(crafted("read_inbox"), assistant):
            pass

    with pytest.raises(PrivateContentError):
        sync.invoke(crafted("read_inbox"), assistant)
    with pytest.raises(PrivateContentError):
        list(sync.invoke_stream(crafted("read_inbox"), assistant))
    with pytest.raises(PrivateContentError):
        asyncio.run(stream())
    assert proxy.requests == []


@pytest.mark.parametrize(
    ("alias", "tool"), [("local-main", "read_inbox"), ("cloud-claude", "count_unread")]
)
def test_guard_lets_local_calls_and_other_tools_through(alias: ModelAlias, tool: str) -> None:
    proxy = FakeProxy(reply("Sam says hi."))
    model = guarded(proxy, alias)

    asyncio.run(model.ainvoke(crafted(tool), Message(role="assistant")))

    assert len(proxy.requests) == 1
    assert model.refused_tools == ()


# --- Router ------------------------------------------------------------------------------------


def privacy_routing(run: Any) -> dict[str, Any]:
    return run.metadata["routing"]


@pytest.mark.parametrize("explicit", [False, True])
def test_private_specialist_never_selects_a_cloud_alias(h: Harness, explicit: bool) -> None:
    provider = "claude" if explicit else "none"
    h.proxy.responses += [
        route_completion(agent=COMMS, level="complex", cloud=explicit, provider=provider),
        tool_call_completion("read_inbox", {}),
        reply("Sam sent the door code."),
    ]

    async def go() -> Any:
        route = await h.router.route("What did Sam say?", ctx())
        assert route.choice.tier == "cloud"  # what it would have used
        result = await h.router.run(route, "What did Sam say?", ctx())
        h.runs = await h.stored_runs()
        return result

    result = asyncio.run(go())

    assert h.models_called() == ["local-router", "local-main", "local-main"]
    assert result.route.choice.alias == "local-main"
    assert result.route.choice.reason == "privacy"
    # An explicit cloud request is told why it stayed local.
    assert result.reply.startswith(PRIVACY_ANNOUNCEMENT) is explicit
    routing = privacy_routing(h.runs[0])
    assert routing["reason"] == "privacy"
    assert routing["privacy"] == {"connectors": ["comms_test"], "downgraded_from": "cloud-claude"}


def test_private_specialist_stream_never_uses_cloud(h: Harness) -> None:
    h.proxy.responses.append(streamed_completion("Nothing new."))

    items = h.stream(h.route())

    assert h.models_called() == ["local-main"]
    done = items[-1]
    assert isinstance(done, Done)
    assert done.route.choice.reason == "privacy"
    assert "".join(i.text for i in items if isinstance(i, Token)) == "Nothing new."


def test_allowing_private_output_restores_normal_routing(tmp_path: Path) -> None:
    h = Harness(tmp_path, FakePrivacy())  # the seed: allowed
    h.proxy.responses.append(reply("Sam sent the door code."))

    result = h.run(h.route())

    assert h.models_called() == ["cloud-claude"]
    assert result.route.choice.reason == "complex"
    assert "privacy" not in privacy_routing(h.runs[0])


def test_connector_allowed_by_override_uses_cloud(tmp_path: Path) -> None:
    allowed = PrivacySettings(allow_private_to_cloud=False, connectors={"comms_test": True})
    h = Harness(tmp_path, FakePrivacy(allowed))
    h.proxy.responses.append(reply("Hi."))

    h.run(h.route())

    assert h.models_called() == ["cloud-claude"]


def test_specialist_without_private_tools_still_uses_cloud(h: Harness) -> None:
    h.comms_tools = [count_unread]
    h.proxy.responses.append(reply("Three unread."))

    h.run(h.route())

    assert h.models_called() == ["cloud-claude"]


def test_unreadable_settings_keep_private_output_local(tmp_path: Path) -> None:
    h = Harness(tmp_path, FakePrivacy(error=RuntimeError("postgres down")))
    h.proxy.responses.append(reply("Hi."))

    result = h.run(h.route())

    assert h.models_called() == ["local-main"]
    assert result.route.choice.reason == "privacy"


def test_private_local_run_is_not_retried_on_cloud(h: Harness) -> None:
    h.proxy.responses.append(reply(LOW_CONFIDENCE_MARKER))
    route = Route(
        Classification(specialist=COMMS), h.registry.get(COMMS), ModelChoice("local-main", "x")
    )

    asyncio.run(h.router.run(route, "hm", ctx()))

    assert h.models_called() == ["local-main"]
    assert h.budget.calls == []


def after_private_history(h: Harness, route: Route, *, stream: bool = False) -> Any:
    """Turn 1: the Comms specialist reads the inbox (locally, as it must). Turn 2: `route`.

    Both turns in one event loop (the async SQLite storage is tied to it). Returns turn 2's
    result (the streamed items if `stream`), with only turn 2's model calls recorded.
    """
    # Ahead of the turn 2 replies the test has queued.
    h.proxy.responses[:0] = [tool_call_completion("read_inbox", {}), reply("Sam sent the code.")]

    async def go() -> Any:
        await h.router.run(h.route(), "What did Sam say?", ctx())
        h.proxy.requests.clear()
        if stream:
            result: Any = [i async for i in h.router.stream(route, "Thanks!", ctx())]
        else:
            result = await h.router.run(route, "Thanks!", ctx())
        h.runs = await h.stored_runs()
        return result

    return asyncio.run(go())


def test_guard_refuses_private_history_and_the_router_retries_locally(h: Harness) -> None:
    # Turn 2 goes to `general`, which has no private tools, so it's routed to cloud; but its
    # history holds the inbox. The guard refuses the cloud call before it's sent.
    h.proxy.responses.append(reply("You're welcome."))

    result = after_private_history(h, h.route(GENERAL))

    assert h.models_called() == ["local-main"]
    assert result.reply == "You're welcome."
    assert result.route.choice.reason == "privacy"
    assert len(h.runs) == 2  # the refused attempt was forgotten
    assert privacy_routing(h.runs[1])["privacy"] == {
        "connectors": ["comms_test"],
        "downgraded_from": "cloud-claude",
    }


def test_stream_guard_refusal_retries_locally(h: Harness) -> None:
    h.proxy.responses.append(streamed_completion("You're welcome."))

    items = after_private_history(h, h.route(GENERAL), stream=True)

    assert h.models_called() == ["local-main"]
    done = items[-1]
    assert isinstance(done, Done)
    assert done.reply == "You're welcome."
    assert done.route.choice.reason == "privacy"


def test_allowed_history_goes_to_cloud(tmp_path: Path) -> None:
    h = Harness(tmp_path, FakePrivacy())
    h.proxy.responses.append(reply("You're welcome."))

    after_private_history(h, h.route(GENERAL))

    assert h.models_called() == ["cloud-claude"]


def test_resume_keeps_private_output_from_before_the_pause_local(tmp_path: Path) -> None:
    # The run read the inbox on cloud (allowed then), then paused before a reply.
    h = Harness(tmp_path, FakePrivacy())
    h.comms_tools = [read_inbox, send_reply]
    h.proxy.responses += [
        tool_call_completion("read_inbox", {}, "call_1"),
        tool_call_completion("send_reply", {"to": "Sam"}, "call_2"),
        reply("Replied."),
    ]

    async def go() -> Any:
        paused = await h.router.run(h.route(), "Reply to Sam", ctx())
        assert paused.confirmation is not None
        assert h.models_called() == ["cloud-claude", "cloud-claude"]
        # Dan switches private output off, and the inbox connector goes away, so only the
        # paused run's own messages show the private output.
        h.privacy.value = DENY
        h.comms_tools = [send_reply]
        return await h.router.resume(paused.confirmation, ctx())

    result = asyncio.run(go())

    assert result.reply == "Replied."
    assert h.models_called()[-1] == "local-main"
    assert result.route.choice.reason == "privacy"
    assert result.route.choice.private == ("comms_test",)
