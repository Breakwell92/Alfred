import json
import re
from collections.abc import Iterator
from typing import Any

import pytest
from agno.db.in_memory import InMemoryDb
from agno.models.base import Model
from fastapi.testclient import TestClient

from alfred_server.chat import agent_db_dependency, chat_model_dependency
from alfred_server.config import Settings
from alfred_server.main import create_app
from alfred_server.models.gateway import gateway_model
from tests.llm_fakes import FakeProxy, completion, streamed_completion, tool_call_completion

TOKEN = "test-token-not-a-secret"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class ChatHarness:
    """The app with a mocked LiteLLM proxy and in-memory Agno storage."""

    def __init__(self) -> None:
        self.proxy = FakeProxy()
        self.db = InMemoryDb()
        self.app = create_app()
        self.app.dependency_overrides[agent_db_dependency] = lambda: self.db
        self.app.dependency_overrides[chat_model_dependency] = self._model
        self.client = TestClient(self.app)

    def _model(self) -> Model:
        settings = Settings(_env_file=None, litellm_api_key="sk-test")
        return gateway_model("local-main", settings=settings, http_client=self.proxy.async_client())

    def reply_with(self, *responses: Any) -> None:
        self.proxy.responses.extend(responses)

    def post(self, path: str, **body: Any) -> Any:
        return self.client.post(path, json=body, headers=AUTH)


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> Iterator[ChatHarness]:
    monkeypatch.setenv("ALFRED_API_TOKEN", TOKEN)
    yield ChatHarness()


def sse_events(text: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for block in text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((fields["event"], json.loads(fields["data"])))
    return events


def message_texts(body: dict[str, Any]) -> list[str]:
    return [str(m.get("content")) for m in body["messages"]]


def test_what_time_is_it_uses_the_get_time_tool(harness: ChatHarness) -> None:
    harness.reply_with(
        tool_call_completion("get_time", {}),
        completion({"role": "assistant", "content": "It's five past two."}),
    )

    response = harness.post("/chat", text="What time is it?", session_id="s1")

    assert response.status_code == 200
    assert response.json() == {"reply": "It's five past two.", "session_id": "s1"}
    first, second = harness.proxy.body(0), harness.proxy.body(1)
    assert first["model"] == "local-main"
    assert [t["function"]["name"] for t in first["tools"]] == ["get_time"]
    tool_result = second["messages"][-1]
    assert tool_result["role"] == "tool"
    assert re.search(r"\d\d:\d\d on \w+day \d+ \w+ \d{4} \(Europe/London\)", tool_result["content"])


def test_request_context_is_in_the_system_message(harness: ChatHarness) -> None:
    harness.reply_with(completion({"role": "assistant", "content": "Hello."}))

    harness.post("/chat", text="Hi", session_id="s1", device_id="pixel-9", device_type="phone")

    system = harness.proxy.body()["messages"][0]
    assert system["role"] == "system"
    assert "<request_context>" in system["content"]
    assert "device_type: phone" in system["content"]
    assert "device_id: pixel-9" in system["content"]
    assert "session_id: s1" in system["content"]
    assert "timezone: Europe/London" in system["content"]


def test_follow_up_in_same_session_sees_earlier_turns(harness: ChatHarness) -> None:
    harness.reply_with(
        completion({"role": "assistant", "content": "Nice to meet you, Dan."}),
        completion({"role": "assistant", "content": "Your name is Dan."}),
        completion({"role": "assistant", "content": "I don't know."}),
    )

    harness.post("/chat", text="My name is Dan.", session_id="s1")
    second = harness.post("/chat", text="What's my name?", session_id="s1")
    harness.post("/chat", text="What's my name?", session_id="s2")

    assert second.json()["reply"] == "Your name is Dan."
    same_session = message_texts(harness.proxy.body(1))
    assert "My name is Dan." in same_session
    assert "Nice to meet you, Dan." in same_session
    other_session = message_texts(harness.proxy.body(2))
    assert "My name is Dan." not in other_session


def test_new_session_id_when_none_given(harness: ChatHarness) -> None:
    harness.reply_with(completion({"role": "assistant", "content": "Hello."}))

    response = harness.post("/chat", text="Hi")

    session_id = response.json()["session_id"]
    assert re.fullmatch(r"[0-9a-f-]{36}", session_id)


def test_stream_yields_tokens_then_done(harness: ChatHarness) -> None:
    harness.reply_with(streamed_completion("It's ", "five ", "past ", "two."))

    response = harness.post("/chat/stream", text="What time is it?", session_id="s1")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = sse_events(response.text)
    tokens = [data["text"] for name, data in events if name == "token"]
    assert tokens == ["It's ", "five ", "past ", "two."]
    assert events[-1] == ("done", {"reply": "It's five past two.", "session_id": "s1"})
    assert harness.proxy.body()["stream"] is True


def test_stream_turn_is_stored_for_the_next_request(harness: ChatHarness) -> None:
    harness.reply_with(
        streamed_completion("Hello ", "Dan."),
        completion({"role": "assistant", "content": "You said hello."}),
    )

    harness.post("/chat/stream", text="I'm Dan.", session_id="s1")
    harness.post("/chat", text="What did I say?", session_id="s1")

    assert "I'm Dan." in message_texts(harness.proxy.body(1))


def test_stream_reports_model_failure(harness: ChatHarness) -> None:
    # No responses queued, so the proxy call fails.
    response = harness.post("/chat/stream", text="Hi", session_id="s1")

    assert sse_events(response.text)[-1] == (
        "error",
        {"message": "The agent run failed", "session_id": "s1"},
    )


def test_chat_reports_model_failure(harness: ChatHarness) -> None:
    response = harness.post("/chat", text="Hi", session_id="s1")

    assert response.status_code == 502


@pytest.mark.parametrize("path", ["/chat", "/chat/stream"])
def test_requires_token(harness: ChatHarness, path: str) -> None:
    response = harness.client.post(path, json={"text": "Hi"})

    assert response.status_code == 401


@pytest.mark.parametrize("path", ["/chat", "/chat/stream"])
def test_503_without_gateway_key(harness: ChatHarness, path: str) -> None:
    del harness.app.dependency_overrides[chat_model_dependency]

    response = harness.post(path, text="Hi")

    assert response.status_code == 503
    assert "ALFRED_LITELLM_API_KEY" in response.json()["detail"]


@pytest.mark.parametrize(
    "body",
    [{"text": ""}, {"text": "x" * 4001}, {"text": "Hi", "device_type": "toaster"}, {}],
)
def test_rejects_invalid_body(harness: ChatHarness, body: dict[str, Any]) -> None:
    response = harness.client.post("/chat", json=body, headers=AUTH)

    assert response.status_code == 422
