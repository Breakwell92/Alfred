import asyncio
import os
import re
import socket
import time
import uuid
from typing import Any

import agno.tracing
import opentelemetry.trace
import pytest
from agno.db.in_memory import InMemoryDb
from agno.db.postgres import AsyncPostgresDb
from fastapi.testclient import TestClient

from alfred_server.agents import storage
from alfred_server.agents.base import BASE_INSTRUCTIONS, build_agent
from alfred_server.agents.context import RequestContext
from alfred_server.agents.echo import build_echo_agent, make_get_time
from alfred_server.agents.storage import (
    AGNO_SCHEMA,
    CONNECT_TIMEOUT_S,
    StorageUnavailableError,
    check_agent_db,
    get_agent_db,
)
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
    assert [getattr(t, "__name__", None) for t in agent.tools] == ["get_time", "send_test_message"]


def test_get_time_reports_local_time() -> None:
    result = make_get_time("Europe/London")()

    assert re.fullmatch(r"\d\d:\d\d on \w+day \d{1,2} \w+ \d{4} \(Europe/London\)", result)


def test_agent_db_is_async_shared_and_uses_agno_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    engine_calls: list[tuple[str, dict[str, Any]]] = []
    real_engine = storage.create_async_postgres_engine

    def recording_engine(url: str, **kwargs: Any) -> Any:
        engine_calls.append((url, kwargs))
        return real_engine(url, **kwargs)

    monkeypatch.setattr(storage, "create_async_postgres_engine", recording_engine)
    url = "postgresql+psycopg://u:p@db:5432/agent-db-test"
    settings = Settings(_env_file=None, database_url=url)

    db = get_agent_db(settings)

    # Async, so agent.arun() awaits storage instead of blocking the event loop.
    assert isinstance(db, AsyncPostgresDb)
    assert db.db_schema == AGNO_SCHEMA
    assert get_agent_db(settings) is db
    assert engine_calls == [(url, {"connect_args": {"connect_timeout": CONNECT_TIMEOUT_S}})]


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set")
def test_sessions_persist_in_postgres() -> None:
    assert TEST_DATABASE_URL
    db = get_agent_db(Settings(_env_file=None, database_url=TEST_DATABASE_URL))
    session_id = f"test-{uuid.uuid4()}"
    proxy = FakeProxy(
        completion({"role": "assistant", "content": "Noted, Dan."}),
        completion({"role": "assistant", "content": "Your name is Dan."}),
    )

    async def conversation() -> None:
        for text in ("My name is Dan.", "What's my name?"):
            client = proxy.async_client()
            model = gateway_model("local-main", settings=gateway_settings(), http_client=client)
            agent = build_echo_agent(ctx(), db=db, model=model)
            await agent.arun(text, session_id=session_id)
        await db.delete_session(session_id)
        await db.db_engine.dispose()

    # psycopg async needs a selector loop (Windows defaults to Proactor).
    asyncio.run(conversation(), loop_factory=asyncio.SelectorEventLoop)

    second_request = [str(m.get("content")) for m in proxy.body(1)["messages"]]
    assert "My name is Dan." in second_request
    assert "Noted, Dan." in second_request


@pytest.mark.parametrize("enabled", [True, False])
def test_lifespan_sets_up_tracing_only_when_enabled(
    monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    monkeypatch.setenv("ALFRED_AGENT_TRACING", str(enabled).lower())
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(agno.tracing, "setup_tracing", lambda **kwargs: calls.append(kwargs))
    shutdowns: list[bool] = []

    class FakeProvider:
        def shutdown(self) -> None:
            shutdowns.append(True)

    monkeypatch.setattr(opentelemetry.trace, "get_tracer_provider", FakeProvider)

    with TestClient(create_app()):
        assert len(calls) == int(enabled)
        assert shutdowns == []

    if enabled:
        assert calls[0]["db"] is get_agent_db()
    # Queued spans are flushed when the app shuts down.
    assert len(shutdowns) == int(enabled)


def run_selector(coro: Any) -> Any:
    # psycopg async needs a selector loop (Windows defaults to Proactor).
    return asyncio.run(coro, loop_factory=asyncio.SelectorEventLoop)


def test_storage_check_fails_fast_when_postgres_refuses() -> None:
    # Nothing listens on port 1: the connection is refused.
    db = storage._agent_db("postgresql+psycopg://u:p@127.0.0.1:1/x")

    start = time.monotonic()
    with pytest.raises(StorageUnavailableError):
        run_selector(check_agent_db(db))

    assert time.monotonic() - start < CONNECT_TIMEOUT_S + 2


def test_storage_check_times_out_when_postgres_hangs() -> None:
    # A listener that accepts TCP but never speaks Postgres: the handshake hangs.
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        db = storage._agent_db(f"postgresql+psycopg://u:p@127.0.0.1:{port}/x")

        start = time.monotonic()
        with pytest.raises(StorageUnavailableError):
            run_selector(check_agent_db(db, timeout_s=0.5))

        assert time.monotonic() - start < 3


def test_storage_check_skips_dbs_without_an_engine() -> None:
    asyncio.run(check_agent_db(InMemoryDb()))  # type: ignore[arg-type]


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set")
def test_storage_check_passes_on_real_postgres() -> None:
    assert TEST_DATABASE_URL
    db = get_agent_db(Settings(_env_file=None, database_url=TEST_DATABASE_URL))

    async def check() -> None:
        await check_agent_db(db)
        await db.db_engine.dispose()

    run_selector(check())
