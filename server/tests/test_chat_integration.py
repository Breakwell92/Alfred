"""`/chat` through the router against the real LiteLLM gateway (`pytest -m integration`).

Needs the compose stack running with `local-router` and `local-main` pulled (docs/infra.md).
Set `LITELLM_TEST_URL` (e.g. http://127.0.0.1:4000) and `LITELLM_TEST_KEY` (LITELLM_MASTER_KEY);
not ALFRED_-prefixed, so the autouse fixture leaves them alone. Sessions are kept in memory.

The budget service is real (gateway prices) but with an in-memory settings store and no spend,
so cloud is enabled without touching Dan's settings. The cloud test costs a fraction of a penny
and skips unless `cloud-claude` is configured on the proxy (ANTHROPIC_API_KEY set).
"""

import os
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from agno.db.base import SessionType
from agno.db.in_memory import InMemoryDb
from fastapi.testclient import TestClient

from alfred_server.budget.models import BudgetSettings
from alfred_server.budget.prices import GatewayPrices
from alfred_server.budget.service import BudgetService
from alfred_server.budget.spend import SpendRow
from alfred_server.budget.store import BudgetStore
from alfred_server.chat import (
    agent_db_dependency,
    budget_dependency,
    confirmation_service_dependency,
)
from alfred_server.confirmations.service import ConfirmationService
from alfred_server.confirmations.store import ConfirmationStore
from alfred_server.connectors.builtin.test_messages import SENT_TEST_MESSAGES
from alfred_server.connectors.registry import default_registry
from alfred_server.main import create_app
from tests.budget_helpers import memory_engine

LITELLM_TEST_URL = os.environ.get("LITELLM_TEST_URL")
LITELLM_TEST_KEY = os.environ.get("LITELLM_TEST_KEY")
TOKEN = "test-token-not-a-secret"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (LITELLM_TEST_URL and LITELLM_TEST_KEY), reason="LITELLM_TEST_URL/KEY not set"
    ),
]


class NoSpend:
    def rows(self, start: datetime, end: datetime) -> list[SpendRow]:
        return []


def proxy_aliases() -> set[str]:
    response = httpx.get(
        f"{LITELLM_TEST_URL}/v1/models",
        headers={"Authorization": f"Bearer {LITELLM_TEST_KEY}"},
        timeout=10,
    )
    response.raise_for_status()
    return {m["id"] for m in response.json()["data"]}


class Client:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALFRED_API_TOKEN", TOKEN)
        monkeypatch.setenv("ALFRED_LITELLM_BASE_URL", LITELLM_TEST_URL or "")
        monkeypatch.setenv("ALFRED_LITELLM_API_KEY", LITELLM_TEST_KEY or "")
        store = BudgetStore(memory_engine())
        store.save(BudgetSettings(cloud_enabled=True))
        budget = BudgetService(
            store,
            NoSpend(),
            GatewayPrices(LITELLM_TEST_URL or "", LITELLM_TEST_KEY),
            usd_to_gbp=0.8,
            timezone="Europe/London",
        )
        self.db = InMemoryDb()
        self.confirmations = ConfirmationService(
            ConfirmationStore(memory_engine()), default_registry()
        )
        app = create_app()
        app.dependency_overrides[agent_db_dependency] = lambda: self.db
        app.dependency_overrides[budget_dependency] = lambda: budget
        app.dependency_overrides[confirmation_service_dependency] = lambda: self.confirmations
        self.client = TestClient(app)

    def chat_json(self, text: str, session_id: str) -> dict[str, Any]:
        response = self.client.post(
            "/chat",
            json={"text": text, "session_id": session_id},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        return body

    def chat(self, text: str, session_id: str) -> str:
        reply: str = self.chat_json(text, session_id)["reply"]
        return reply

    def runs(self, session_id: str) -> list[Any]:
        session: Any = self.db.get_session(session_id, session_type=SessionType.AGENT)
        return list(session.runs)


def test_what_time_is_it_stays_local(monkeypatch: pytest.MonkeyPatch) -> None:
    client = Client(monkeypatch)

    london = ZoneInfo("Europe/London")
    before = datetime.now(london).strftime("%H:")
    reply = client.chat("What time is it?", "integration-1")
    after = datetime.now(london).strftime("%H:")

    (run,) = client.runs("integration-1")
    assert run.metadata["routing"]["tier"] == "local"
    # From get_time or the request context's current_time; either way the real time.
    assert before in reply or after in reply


def test_ask_claude_uses_cloud_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    if "cloud-claude" not in proxy_aliases():
        pytest.skip("cloud-claude is not configured on the proxy (ANTHROPIC_API_KEY unset)")
    client = Client(monkeypatch)

    assert client.chat("Ask Claude to say hello in five words.", "integration-2")

    (run,) = client.runs("integration-2")
    routing = run.metadata["routing"]
    assert routing["alias"] == "cloud-claude"
    assert routing["reason"] == "explicit_request"
    assert run.model == "cloud-claude"


def test_send_test_message_waits_for_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALFRED_TEST_TOOLS_ENABLED", "true")
    client = Client(monkeypatch)
    SENT_TEST_MESSAGES.clear()

    body = client.chat_json(
        "Send a test message to Sam saying I'm running 10 minutes late.", "integration-3"
    )

    assert body["confirmation"] is not None, body["reply"]
    assert body["confirmation"]["tools"] == ["send_test_message"]
    assert body["reply"].startswith("I need your OK first: Send a test message to Sam")
    assert SENT_TEST_MESSAGES == []

    client.chat("Yes", "integration-3")

    [(recipient, text)] = SENT_TEST_MESSAGES
    assert recipient == "Sam"
    assert "late" in text
    SENT_TEST_MESSAGES.clear()
