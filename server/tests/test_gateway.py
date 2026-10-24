import json
from typing import Any

import pytest
from agno.agent import Agent

from alfred_server.config import Settings
from alfred_server.models.gateway import (
    MODEL_ALIASES,
    CallMetadata,
    GatewayNotConfiguredError,
    gateway_model,
)
from tests.llm_fakes import FakeProxy, completion

BASE_URL = "http://litellm.test:4000"
KEY = "sk-test-key"


def settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"litellm_base_url": BASE_URL, "litellm_api_key": KEY}
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_calls_proxy_with_alias_key_and_metadata() -> None:
    proxy = FakeProxy(completion({"role": "assistant", "content": "Hello, Dan."}))
    model = gateway_model(
        "local-main",
        metadata=CallMetadata(agent="main", task_id="task-1", trigger="voice"),
        settings=settings(),
        http_client=proxy.client(),
    )

    run = Agent(model=model, telemetry=False).run("Say hello.")

    assert run.content == "Hello, Dan."
    request = proxy.requests[0]
    assert str(request.url) == f"{BASE_URL}/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    body = proxy.body()
    assert body["model"] == "local-main"
    assert body["messages"][-1] == {"role": "user", "content": "Say hello."}
    assert body["metadata"] == {
        "tags": ["agent:main", "trigger:voice"],
        "spend_logs_metadata": {"agent": "main", "task_id": "task-1", "trigger": "voice"},
    }


def test_tool_call_round_trip() -> None:
    calls: list[str] = []

    def light_on(room: str) -> str:
        """Turn on the light in a room."""
        calls.append(room)
        return f"{room} light is on"

    tool_call = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "light_on", "arguments": json.dumps({"room": "kitchen"})},
    }
    proxy = FakeProxy(
        completion({"role": "assistant", "content": "", "tool_calls": [tool_call]}, "tool_calls"),
        completion({"role": "assistant", "content": "The kitchen light is on."}),
    )
    model = gateway_model("local-main", settings=settings(), http_client=proxy.client())

    run = Agent(model=model, tools=[light_on], telemetry=False).run("Kitchen light on.")

    assert calls == ["kitchen"]
    assert run.content == "The kitchen light is on."
    assert proxy.body(0)["tools"][0]["function"]["name"] == "light_on"
    tool_result = proxy.body(1)["messages"][-1]
    assert tool_result["role"] == "tool"
    assert tool_result["tool_call_id"] == "call_1"
    assert "kitchen light is on" in tool_result["content"]


def test_no_metadata_sends_no_metadata() -> None:
    proxy = FakeProxy(completion({"role": "assistant", "content": "ok"}))
    model = gateway_model("local-router", settings=settings(), http_client=proxy.client())

    Agent(model=model, telemetry=False).run("hi")

    assert "metadata" not in proxy.body()


def test_metadata_without_optional_fields() -> None:
    assert CallMetadata(agent="router").to_litellm() == {
        "tags": ["agent:router"],
        "spend_logs_metadata": {"agent": "router"},
    }


def test_metadata_merges_with_caller_extra_body() -> None:
    model = gateway_model(
        "cloud-claude",
        metadata=CallMetadata(agent="main"),
        settings=settings(),
        extra_body={"metadata": {"session_id": "s1"}, "num_retries": 1},
    )

    assert model.extra_body == {
        "metadata": {
            "session_id": "s1",
            "tags": ["agent:main"],
            "spend_logs_metadata": {"agent": "main"},
        },
        "num_retries": 1,
    }


@pytest.mark.parametrize("alias", MODEL_ALIASES)
def test_every_alias_maps_to_itself(alias: Any) -> None:
    model = gateway_model(alias, settings=settings())

    assert model.id == alias
    assert model.base_url == BASE_URL


def test_rejects_unknown_alias() -> None:
    with pytest.raises(ValueError, match="unknown model alias"):
        gateway_model("gpt-4o", settings=settings())  # type: ignore[arg-type]


@pytest.mark.parametrize("key", [None, ""])
def test_requires_api_key(key: str | None) -> None:
    with pytest.raises(GatewayNotConfiguredError):
        gateway_model("local-main", settings=settings(litellm_api_key=key))


def test_empty_api_key_from_env_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # infra/.env.example ships `ALFRED_LITELLM_API_KEY=`, which loads as SecretStr("").
    monkeypatch.setenv("ALFRED_LITELLM_API_KEY", "")

    with pytest.raises(GatewayNotConfiguredError):
        gateway_model("local-main", settings=Settings(_env_file=None))


def test_reads_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALFRED_LITELLM_BASE_URL", "http://100.64.0.1:4000")
    monkeypatch.setenv("ALFRED_LITELLM_API_KEY", "sk-from-env")

    model = gateway_model("local-main")

    assert model.base_url == "http://100.64.0.1:4000"
    assert model.api_key == "sk-from-env"
