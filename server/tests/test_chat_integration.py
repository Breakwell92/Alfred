"""`/chat` against the real LiteLLM gateway and local model (`pytest -m integration`).

Needs the compose stack running with `local-main` pulled (docs/infra.md). Set
`LITELLM_TEST_URL` (e.g. http://127.0.0.1:4000) and `LITELLM_TEST_KEY` (LITELLM_MASTER_KEY);
not ALFRED_-prefixed, so the autouse fixture leaves them alone. Sessions are kept in memory.
"""

import os
from typing import Any

import pytest
from agno.db.base import SessionType
from agno.db.in_memory import InMemoryDb
from fastapi.testclient import TestClient

from alfred_server.chat import agent_db_dependency
from alfred_server.main import create_app

LITELLM_TEST_URL = os.environ.get("LITELLM_TEST_URL")
LITELLM_TEST_KEY = os.environ.get("LITELLM_TEST_KEY")
TOKEN = "test-token-not-a-secret"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (LITELLM_TEST_URL and LITELLM_TEST_KEY), reason="LITELLM_TEST_URL/KEY not set"
    ),
]


def test_what_time_is_it_on_the_local_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALFRED_API_TOKEN", TOKEN)
    monkeypatch.setenv("ALFRED_LITELLM_BASE_URL", LITELLM_TEST_URL or "")
    monkeypatch.setenv("ALFRED_LITELLM_API_KEY", LITELLM_TEST_KEY or "")
    app = create_app()
    db = InMemoryDb()
    app.dependency_overrides[agent_db_dependency] = lambda: db
    client = TestClient(app)

    response = client.post(
        "/chat",
        json={"text": "What time is it?", "session_id": "integration-1"},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )

    assert response.status_code == 200, response.text
    reply: str = response.json()["reply"]
    assert reply
    session: Any = db.get_session("integration-1", session_type=SessionType.AGENT)
    tool_names = [t.tool_name for run in session.runs for t in (run.tools or [])]
    assert "get_time" in tool_names
