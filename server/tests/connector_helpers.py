"""Test connectors, a fake OAuth provider and a service factory for the connector tests."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
from agno.tools import Toolkit
from cryptography.fernet import Fernet
from sqlalchemy import Engine

from alfred_server.connectors.models import (
    AgnoToolkitSource,
    Connector,
    Credentials,
    McpStdioSource,
    OAuth2Config,
    ToolMeta,
)
from alfred_server.connectors.oauth import OAuth2Client
from alfred_server.connectors.registry import ConnectorRegistry, default_registry
from alfred_server.connectors.service import ConnectorService
from alfred_server.connectors.store import ConnectorStore
from alfred_server.connectors.vault import Vault
from tests.budget_helpers import memory_engine

VAULT_KEY = Fernet.generate_key().decode()
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
TOKEN_URL = "https://auth.acme.test/token"
MCP_SERVER = Path(__file__).parent / "fixtures" / "mcp_server.py"


class Clock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


def acme_toolkit(creds: Credentials) -> Toolkit:
    def acme_read() -> str:
        """Read the user's Acme items."""
        return f"items for {creds.access_token}"

    def acme_delete() -> str:
        """Delete everything."""
        return "deleted"

    return Toolkit(name="acme", tools=[acme_read, acme_delete])


def keyed_toolkit(creds: Credentials) -> Toolkit:
    def keyed_lookup() -> str:
        """Look something up."""
        return f"key={creds.api_key}"

    def keyed_admin() -> str:
        """Not declared in the connector's tools, so it must never load."""
        return "admin"

    return Toolkit(name="keyed", tools=[keyed_lookup, keyed_admin])


OAUTH_CONNECTOR = Connector(
    id="acme",
    name="Acme",
    description="Test OAuth connector.",
    auth_type="oauth2",
    oauth=OAuth2Config(
        authorize_url="https://auth.acme.test/authorize",
        token_url=TOKEN_URL,
        extra_authorize_params={"access_type": "offline"},
    ),
    scopes=("items.read", "items.write"),
    tool_source=AgnoToolkitSource(acme_toolkit),
    tools=(
        ToolMeta(name="acme_read", description="Read items", risk="read", privacy="local_only"),
        ToolMeta(name="acme_delete", description="Delete items", risk="irreversible"),
    ),
    specialist="acme",
)

API_KEY_CONNECTOR = Connector(
    id="keyed",
    name="Keyed",
    description="Test API-key connector.",
    auth_type="api_key",
    tool_source=AgnoToolkitSource(keyed_toolkit),
    tools=(ToolMeta(name="keyed_lookup", description="Look up", risk="read"),),
    specialist="general",
)

MCP_CONNECTOR = Connector(
    id="mcpdemo",
    name="MCP demo",
    description="Stdio MCP server from tests/fixtures/mcp_server.py.",
    auth_type="api_key",
    tool_source=McpStdioSource(
        command=sys.executable,
        args=(str(MCP_SERVER),),
        env=lambda creds: {"TEST_MCP_TOKEN": creds.api_key or ""},
        timeout_seconds=30,
    ),
    tools=(
        ToolMeta(name="echo", description="Echo text", risk="read"),
        ToolMeta(name="whoami", description="Token the server got", risk="read"),
    ),
    specialist="mcp",
)


def build_registry() -> ConnectorRegistry:
    registry = default_registry()
    for connector in (OAUTH_CONNECTOR, API_KEY_CONNECTOR, MCP_CONNECTOR):
        registry.register(connector)
    return registry


class FakeProvider:
    """OAuth token endpoint behind `httpx.MockTransport`. Issues numbered tokens."""

    def __init__(self) -> None:
        self.requests: list[dict[str, str]] = []
        self.issued = 0
        self.fail_with: int | None = None
        self.rotate_refresh_token = True
        self.expires_in = 3600

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == TOKEN_URL
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.requests.append(form)
        if self.fail_with is not None:
            return httpx.Response(self.fail_with, json={"error": "invalid_grant"})
        self.issued += 1
        body: dict[str, Any] = {
            "access_token": f"access-{self.issued}",
            "token_type": "Bearer",
            "expires_in": self.expires_in,
        }
        if form["grant_type"] == "authorization_code" or self.rotate_refresh_token:
            body["refresh_token"] = f"refresh-{self.issued}"
        return httpx.Response(200, content=json.dumps(body))

    @property
    def client(self) -> OAuth2Client:
        return OAuth2Client(transport=httpx.MockTransport(self.handler))


def make_service(
    *,
    engine: Engine | None = None,
    key: str | None = VAULT_KEY,
    registry: ConnectorRegistry | None = None,
    provider: FakeProvider | None = None,
    clock: Clock | None = None,
) -> ConnectorService:
    return ConnectorService(
        registry or build_registry(),
        ConnectorStore(engine or memory_engine()),
        Vault(key),
        (provider or FakeProvider()).client,
        clock=clock or Clock(datetime.now(UTC)),
    )
