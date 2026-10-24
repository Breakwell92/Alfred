import os
import re
import uuid
from typing import Any

import agno.tracing
import pytest
from agno.db.in_memory import InMemoryDb
from agno.db.postgres import PostgresDb
from fastapi.testclient import TestClient

from alfred_server.agents.base import BASE_INSTRUCTIONS, build_agent
from alfred_server.agents.context import RequestContext
from alfred_server.agents.echo import build_echo_agent, make_get_time
from alfred_server.agents.storage import AGNO_SCHEMA, get_agent_db
from alfred_server.config import Settings
from alfred_server.main import create_app
from alfred_server.models.gateway import GatewayNotConfiguredError, gateway_model
from tests.llm_fakes import FakeProxy, completion

# See tests/test_db.py: the Postgres test only runs when this is set (CI sets it).
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


def ctx(**fields: str) -> RequestContext:
    return RequestContext.build(
        timezone="Europe/London", device_type="phone", session_id="s1", **fields
    )


def gateway_settings() -> Settings:
    return Settings(_env_file=None, litellm_api_key="sk-test")


def test_shared_defaults() -> None:
    agent = build_agent(
        "home",
        ctx=ctx(active_room="kitchen"),
        alias="local-router",
        db=InMemoryDb(),
        instructions=["Control the lights."],
        settings=gateway_settings(),
    )

    assert agent.name == "home"
    assert agent.model is not None and agent.model.id == "local-router"
    assert agent.markdown is False
    assert agent.telemetry is False
    assert agent.add_history_to_context is True
    assert agent.timezone_identifier == "Europe/London"
    assert agent.instructions == [*BASE_INSTRUCTIONS, "Control the lights."]
    assert agent.additional_context is not None
    assert "active_room: kitchen" in agent.additional_context


def test_tags_calls_with_agent_and_trigger() -> None:
    agent = build_agent(
        "home", ctx=ctx(), alias="local-main", db=InMemoryDb(), settings=gateway_settings()
    )

    assert agent.model is not None
    assert agent.model.extra_body == {  # type: ignore[attr-defined]
        "metadata": {
            "tags": ["agent:home", "trigger:phone"],
            "spend_logs_metadata": {"agent": "home", "trigger": "phone"},
        }
    }


def test_needs_gateway_key() -> None:
    with pytest.raises(GatewayNotConfiguredError):
        build_echo_agent(ctx(), db=InMemoryDb(), settings=Settings(_env_file=None))


def test_echo_agent_uses_local_main_and_get_time() -> None:
    agent = build_echo_agent(ctx(), db=InMemoryDb(), settings=gateway_settings())

    assert agent.model is not None and agent.model.id == "local-main"
    assert isinstance(agent.tools, list)
    assert [getattr(t, "__name__", None) for t in agent.tools] == ["get_time"]


def test_get_time_reports_local_time() -> None:
    result = make_get_time("Europe/London")()

    assert re.fullmatch(r"\d\d:\d\d on \w+day \d{1,2} \w+ \d{4} \(Europe/London\)", result)


def test_agent_db_is_shared_and_uses_agno_schema() -> None:
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://u:p@db:5432/x")

    db = get_agent_db(settings)

    assert isinstance(db, PostgresDb)
    assert db.db_schema == AGNO_SCHEMA
    assert get_agent_db(settings) is db


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set")
def test_sessions_persist_in_postgres() -> None:
    assert TEST_DATABASE_URL
    db = get_agent_db(Settings(_env_file=None, database_url=TEST_DATABASE_URL))
    session_id = f"test-{uuid.uuid4()}"
    proxy = FakeProxy(
        completion({"role": "assistant", "content": "Noted, Dan."}),
        completion({"role": "assistant", "content": "Your name is Dan."}),
    )

    def run(text: str) -> None:
        model = gateway_model("local-main", settings=gateway_settings(), http_client=proxy.client())
        agent = build_echo_agent(ctx(), db=db, model=model)
        agent.run(text, session_id=session_id)

    run("My name is Dan.")
    run("What's my name?")

    second_request = [str(m.get("content")) for m in proxy.body(1)["messages"]]
    assert "My name is Dan." in second_request
    assert "Noted, Dan." in second_request
    db.delete_session(session_id)


@pytest.mark.parametrize("enabled", [True, False])
def test_lifespan_sets_up_tracing_only_when_enabled(
    monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    monkeypatch.setenv("ALFRED_AGENT_TRACING", str(enabled).lower())
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(agno.tracing, "setup_tracing", lambda **kwargs: calls.append(kwargs))

    with TestClient(create_app()):
        pass

    assert len(calls) == int(enabled)
    if enabled:
        assert calls[0]["db"] is get_agent_db()
