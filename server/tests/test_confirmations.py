"""Confirmation gating end to end through `POST /chat`, `/chat/stream` and the confirmations API.

The general (echo) agent has `get_time` (read) and the dummy `send_test_message` (write).
"""

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from agno.run.base import RunStatus

from alfred_server.connectors.builtin.test_messages import SENT_TEST_MESSAGES
from tests.llm_fakes import (
    reply,
    route_completion,
    streamed_tool_call,
    tool_call_completion,
)
from tests.router_fakes import FakeBudget
from tests.test_chat import AUTH, TOKEN, ChatHarness, RecordingDb, sse_events

SAM = {"recipient": "Sam", "text": "Running 10 mins late"}
SUMMARY = "Send a test message to Sam: 'Running 10 mins late'"
ASK = f"I need your OK first: {SUMMARY}. Shall I go ahead?"
DENIED = "OK, I won't send a test message to Sam: 'Running 10 mins late'."


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ChatHarness:
    monkeypatch.setenv("ALFRED_API_TOKEN", TOKEN)
    monkeypatch.setenv("ALFRED_TEST_TOOLS_ENABLED", "true")
    return ChatHarness(RecordingDb(tmp_path / "agno.db"))


@pytest.fixture(autouse=True)
def clear_outbox() -> Iterator[None]:
    SENT_TEST_MESSAGES.clear()
    yield
    SENT_TEST_MESSAGES.clear()


def ask_to_send(h: ChatHarness, **body: Any) -> dict[str, Any]:
    """A turn where the model calls send_test_message, which pauses."""
    h.reply_with(route_completion(), tool_call_completion("send_test_message", SAM))
    response = h.post("/chat", text="Text Sam that I'm running 10 mins late", **body)
    assert response.status_code == 200, response.text
    return response.json()


def pending(h: ChatHarness) -> list[dict[str, Any]]:
    response = h.client.get("/confirmations/pending", headers=AUTH)
    assert response.status_code == 200
    return response.json()["pending"]


def run_status(h: ChatHarness, run_id: str) -> RunStatus:
    run: Any = asyncio.run(h.db.get_run(run_id))
    return run.status


def answer(h: ChatHarness, confirmation_id: str, **body: Any) -> Any:
    return h.client.post(f"/confirmations/{confirmation_id}", json=body, headers=AUTH)


def test_a_read_tool_runs_immediately(harness: ChatHarness) -> None:
    harness.reply_with(
        route_completion(), tool_call_completion("get_time", {}), reply("It's two o'clock.")
    )

    body = harness.post("/chat", text="What time is it?", session_id="s1").json()

    assert body["reply"] == "It's two o'clock."
    assert body["confirmation"] is None
    assert pending(harness) == []


def test_a_write_tool_pauses_and_asks(harness: ChatHarness) -> None:
    body = ask_to_send(harness, session_id="s1")

    assert body["reply"] == ASK
    c = body["confirmation"]
    assert (c["summary"], c["tier"], c["channel"], c["tools"]) == (
        SUMMARY,
        "write",
        "text",
        ["send_test_message"],
    )
    assert SENT_TEST_MESSAGES == []  # nothing ran
    assert [p["id"] for p in pending(harness)] == [c["id"]]
    [record] = harness.confirmations.pending()
    assert run_status(harness, record.run_id) == RunStatus.paused


def test_approving_through_the_api_resumes_the_run(harness: ChatHarness) -> None:
    c = ask_to_send(harness, session_id="s1")["confirmation"]
    harness.reply_with(reply("Done, I've sent it."))

    response = answer(harness, c["id"], approve=True)

    assert response.status_code == 200
    assert response.json() == {
        "message": "Approved.",
        "reply": "Done, I've sent it.",
        "session_id": "s1",
        "confirmation": None,
    }
    assert SENT_TEST_MESSAGES == [("Sam", "Running 10 mins late")]
    assert pending(harness) == []
    # The model saw the tool's result when the run continued.
    assert harness.proxy.body(2)["messages"][-1]["role"] == "tool"
    # Answering twice is refused.
    assert answer(harness, c["id"], approve=True).status_code == 409


def test_yes_as_the_next_message_resumes_the_run(harness: ChatHarness) -> None:
    ask_to_send(harness, session_id="s1")
    harness.reply_with(reply("Sent."))  # no classifier call: the answer isn't routed

    body = harness.post("/chat", text="Yes please", session_id="s1").json()

    assert body == {"reply": "Sent.", "session_id": "s1", "confirmation": None}
    assert SENT_TEST_MESSAGES == [("Sam", "Running 10 mins late")]
    assert len(harness.proxy.requests) == 3


def test_yes_in_another_session_does_not_approve(harness: ChatHarness) -> None:
    ask_to_send(harness, session_id="s1")
    harness.reply_with(route_completion(), reply("Yes to what?"))

    harness.post("/chat", text="yes", session_id="s2")

    assert SENT_TEST_MESSAGES == []
    assert len(pending(harness)) == 1


def test_no_cancels_with_a_spoken_message(harness: ChatHarness) -> None:
    ask_to_send(harness, session_id="s1")
    [record] = harness.confirmations.pending()

    body = harness.post("/chat", text="No", session_id="s1").json()

    assert body == {"reply": DENIED, "session_id": "s1", "confirmation": None}
    assert SENT_TEST_MESSAGES == []
    assert pending(harness) == []
    assert run_status(harness, record.run_id) == RunStatus.cancelled
    assert len(harness.proxy.requests) == 2  # no model call to say no

    # The next turn's history has no dangling tool call from the cancelled run.
    harness.reply_with(route_completion(), reply("Hello."))
    harness.post("/chat", text="Hi", session_id="s1")
    messages = harness.proxy.body(3)["messages"]
    assert not any(m.get("tool_calls") for m in messages)
    assert not any(m["role"] == "tool" for m in messages)


def test_denying_through_the_api(harness: ChatHarness) -> None:
    c = ask_to_send(harness, session_id="s1")["confirmation"]

    response = answer(harness, c["id"], approve=False)

    assert response.status_code == 200
    assert response.json()["reply"] == DENIED
    assert SENT_TEST_MESSAGES == []
    assert pending(harness) == []


def test_expiry_cancels(harness: ChatHarness) -> None:
    c = ask_to_send(harness, session_id="s1")["confirmation"]
    [record] = harness.confirmations.pending()
    harness.clock.advance(seconds=121)

    assert pending(harness) == []
    assert answer(harness, c["id"], approve=True).status_code == 409
    assert run_status(harness, record.run_id) == RunStatus.cancelled
    stored = harness.confirmations.get(c["id"])
    assert stored is not None and stored.status == "expired"

    # A late "yes" is just a message now: it's routed, and nothing is sent.
    harness.reply_with(route_completion(), reply("Yes to what?"))
    body = harness.post("/chat", text="yes", session_id="s1").json()
    assert body["reply"] == "Yes to what?"
    assert SENT_TEST_MESSAGES == []


def test_the_sweeper_expires_without_a_request(harness: ChatHarness) -> None:
    ask_to_send(harness, session_id="s1")
    harness.clock.advance(seconds=121)

    expired = asyncio.run(harness.confirmations.expire_due(harness.db))

    assert len(expired) == 1
    assert run_status(harness, expired[0].run_id) == RunStatus.cancelled


def test_moving_on_cancels_a_pending_write(harness: ChatHarness) -> None:
    ask_to_send(harness, session_id="s1")
    harness.reply_with(route_completion(), reply("It's sunny."))

    body = harness.post("/chat", text="What's the weather like?", session_id="s1").json()

    assert body["reply"] == "It's sunny."
    assert pending(harness) == []
    assert SENT_TEST_MESSAGES == []


def set_risk(h: ChatHarness, risk: str | None) -> Any:
    return h.client.put(
        "/settings/permissions/test_messages/send_test_message", json={"risk": risk}, headers=AUTH
    )


def test_irreversible_rejects_text_approval(harness: ChatHarness) -> None:
    assert set_risk(harness, "irreversible").status_code == 200
    body = ask_to_send(harness, session_id="s1")
    c = body["confirmation"]
    assert c["tier"] == "irreversible"
    assert body["reply"].endswith("Please approve it in the Alfred app.")

    # Text "yes" doesn't approve it, and doesn't cancel it either.
    said = harness.post("/chat", text="yes", session_id="s1").json()
    assert said["reply"] == "That one needs approving in the Alfred app."
    assert answer(harness, c["id"], approve=True).status_code == 403  # channel defaults to text
    assert answer(harness, c["id"], approve=True, channel="text").status_code == 403
    assert SENT_TEST_MESSAGES == []
    assert len(pending(harness)) == 1

    # Moving on leaves it for the app too.
    harness.reply_with(route_completion(), reply("Hello."))
    harness.post("/chat", text="Hi", session_id="s1")
    assert len(pending(harness)) == 1

    harness.reply_with(reply("Sent."))
    response = answer(harness, c["id"], approve=True, channel="app_ui")
    assert response.status_code == 200
    assert response.json()["reply"] == "Sent."
    assert SENT_TEST_MESSAGES == [("Sam", "Running 10 mins late")]


def test_tightening_a_tool_while_it_waits_raises_the_bar(harness: ChatHarness) -> None:
    c = ask_to_send(harness, session_id="s1")["confirmation"]
    assert c["tier"] == "write"
    set_risk(harness, "irreversible")

    said = harness.post("/chat", text="yes", session_id="s1").json()

    assert said["reply"] == "That one needs approving in the Alfred app."
    assert answer(harness, c["id"], approve=True, channel="text").status_code == 403
    assert SENT_TEST_MESSAGES == []
    assert len(pending(harness)) == 1


def test_relaxing_a_tool_while_it_waits_does_not_lower_the_bar(harness: ChatHarness) -> None:
    set_risk(harness, "irreversible")
    c = ask_to_send(harness, session_id="s1")["confirmation"]
    set_risk(harness, "read")

    said = harness.post("/chat", text="yes", session_id="s1").json()

    assert said["reply"] == "That one needs approving in the Alfred app."
    assert answer(harness, c["id"], approve=True).status_code == 403
    assert SENT_TEST_MESSAGES == []


def test_relaxing_a_tool_to_read_runs_it_immediately(harness: ChatHarness) -> None:
    set_risk(harness, "read")
    harness.reply_with(
        route_completion(), tool_call_completion("send_test_message", SAM), reply("Sent.")
    )

    body = harness.post("/chat", text="Text Sam", session_id="s1").json()

    assert body["reply"] == "Sent."
    assert SENT_TEST_MESSAGES == [("Sam", "Running 10 mins late")]


def test_scheduled_runs_are_never_auto_approved(harness: ChatHarness) -> None:
    body = ask_to_send(harness, session_id="s1", device_type="scheduled")

    assert body["confirmation"]["channel"] == "scheduled"
    assert SENT_TEST_MESSAGES == []
    [note] = harness.confirmations.notifications()
    assert note.ref_id == body["confirmation"]["id"]
    assert note.body == SUMMARY


def test_stream_pauses_with_the_confirmation_in_done(harness: ChatHarness) -> None:
    harness.reply_with(route_completion(), streamed_tool_call("send_test_message", SAM))

    response = harness.client.post(
        "/chat/stream", json={"text": "Text Sam", "session_id": "s1"}, headers=AUTH
    )

    events = sse_events(response.text)
    assert [e for e, _ in events] == ["token", "done"]
    assert events[0][1]["text"] == ASK
    done = events[1][1]
    assert done["reply"] == ASK
    assert done["confirmation"]["summary"] == SUMMARY
    assert done["turn"]["tools"] == []  # it hasn't run: it's waiting in `confirmation.tools`
    assert done["confirmation"]["tools"] == ["send_test_message"]
    assert SENT_TEST_MESSAGES == []


def test_stream_yes_resumes_the_run(harness: ChatHarness) -> None:
    ask_to_send(harness, session_id="s1")
    harness.reply_with(reply("Sent."))

    response = harness.client.post(
        "/chat/stream", json={"text": "yes", "session_id": "s1"}, headers=AUTH
    )

    events = sse_events(response.text)
    assert [e for e, _ in events] == ["token", "done"]
    assert events[1][1]["reply"] == "Sent."
    assert events[1][1]["turn"]["tools"] == ["send_test_message"]
    assert SENT_TEST_MESSAGES == [("Sam", "Running 10 mins late")]


def test_resume_on_cloud_asks_the_budget_again(harness: ChatHarness) -> None:
    harness.budget = FakeBudget(["cloud-claude"])
    harness.reply_with(
        route_completion(level="complex"), tool_call_completion("send_test_message", SAM)
    )
    c = harness.post("/chat", text="Text Sam", session_id="s1").json()["confirmation"]
    asked = len(harness.budget.calls)
    harness.reply_with(reply("Sent."))

    answer(harness, c["id"], approve=True)

    assert harness.budget.calls[asked:] == [("cloud-claude", "auto")]
    assert harness.proxy.body(2)["model"] == "cloud-claude"


# --- Permissions API -----------------------------------------------------------------------


def test_permissions_list_declared_and_effective(harness: ChatHarness) -> None:
    set_risk(harness, "irreversible")

    rows = harness.client.get("/settings/permissions", headers=AUTH).json()

    assert {
        "connector_id": "test_messages",
        "tool": "send_test_message",
        "declared": "write",
        "override": "irreversible",
        "effective": "irreversible",
    } in rows
    assert {
        "connector_id": "time",
        "tool": "get_time",
        "declared": "read",
        "override": None,
        "effective": "read",
    } in rows


def test_removing_an_override(harness: ChatHarness) -> None:
    set_risk(harness, "read")

    body = set_risk(harness, None).json()

    assert (body["override"], body["effective"]) == (None, "write")


def test_permissions_for_unknown_tools_are_404(harness: ChatHarness) -> None:
    for path in ("/settings/permissions/nope/x", "/settings/permissions/time/send_test_message"):
        response = harness.client.put(path, json={"risk": "read"}, headers=AUTH)
        assert response.status_code == 404


def test_invalid_risk_is_rejected(harness: ChatHarness) -> None:
    assert set_risk(harness, "whatever").status_code == 422


def test_unknown_confirmation_is_404(harness: ChatHarness) -> None:
    assert answer(harness, "nope", approve=True).status_code == 404


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/confirmations/pending"),
        ("POST", "/confirmations/x"),
        ("GET", "/settings/permissions"),
        ("PUT", "/settings/permissions/time/get_time"),
    ],
)
def test_routes_require_the_token(harness: ChatHarness, method: str, path: str) -> None:
    assert harness.client.request(method, path, json={}).status_code == 401
