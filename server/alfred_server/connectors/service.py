"""The connector service: status, credentials, OAuth and tool loading for every connector.

Specialist agents call `await get_connector_service().load_tools("<specialist>")`; the REST
routes in `api.py` call the rest. Design and rules: docs/decisions/0010-connector-registry.md.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

from agno.tools import Toolkit
from agno.tools.mcp import MCPTools, StreamableHTTPClientParams
from mcp import StdioServerParameters
from pydantic import BaseModel
from sqlalchemy import create_engine

from alfred_server.config import get_settings
from alfred_server.connectors.models import (
    AgnoToolkitSource,
    AuthType,
    Connector,
    Credentials,
    McpHttpSource,
    McpStdioSource,
    Status,
    TokenSet,
    ToolMeta,
)
from alfred_server.connectors.oauth import OAuth2Client, OAuthError, new_code_verifier, new_state
from alfred_server.connectors.registry import ConnectorRegistry, default_registry
from alfred_server.connectors.store import ConnectorStore, StateRow, TokenRow
from alfred_server.connectors.vault import Vault, VaultError, VaultUnavailableError

logger = logging.getLogger(__name__)

# How long an authorization started with /auth/start can be completed.
OAUTH_STATE_TTL = timedelta(minutes=10)
# The background refresher renews tokens expiring within this margin.
REFRESH_MARGIN = timedelta(minutes=5)
# Tools are never built with a token this close to expiry: it's refreshed first.
MIN_TOKEN_LIFETIME = timedelta(seconds=60)

LoadedTools = Toolkit | MCPTools


class ConnectorNotFoundError(KeyError):
    """No connector with that id is registered."""


class ConnectorConfigError(ValueError):
    """A request doesn't fit the connector (e.g. an API key for an OAuth connector)."""


class ConnectorAuthError(RuntimeError):
    """An OAuth authorization can't be completed (bad state, provider refusal)."""


class ToolInfo(BaseModel):
    name: str
    description: str
    risk: str
    privacy: str


class ConnectorInfo(BaseModel):
    """What `GET /connectors` shows for one connector. Never contains a secret."""

    id: str
    name: str
    description: str
    auth_type: AuthType
    scopes: list[str]
    specialist: str
    enabled: bool
    status: Status
    last_error: str | None
    has_api_key: bool
    has_oauth_client: bool
    token_expires_at: datetime | None
    tools: list[ToolInfo]


def _fingerprint(creds: Credentials) -> str:
    raw = f"{creds.api_key}|{creds.access_token}|{sorted(creds.extra.items())}"
    return hashlib.sha256(raw.encode()).hexdigest()


def restrict_toolkit(toolkit: Toolkit, allowed: set[str]) -> Toolkit:
    """Drop any function the connector didn't declare (and so has no risk metadata for)."""
    for registry in (toolkit.functions, toolkit.async_functions):
        for name in list(registry):
            if name not in allowed:
                logger.warning("toolkit %s: dropping undeclared tool %s", toolkit.name, name)
                del registry[name]
    return toolkit


class ConnectorService:
    def __init__(
        self,
        registry: ConnectorRegistry,
        store: ConnectorStore,
        vault: Vault,
        oauth: OAuth2Client | None = None,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.registry = registry
        self.store = store
        self.vault = vault
        self.oauth = oauth or OAuth2Client()
        self.clock = clock
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        # Connected MCP toolkits, reused across requests: connector id → (creds fingerprint, tools).
        self._mcp: dict[str, tuple[str, MCPTools]] = {}
        self._mcp_lock = asyncio.Lock()

    # --- Helpers ----------------------------------------------------------------------------

    def _connector(self, connector_id: str) -> Connector:
        connector = self.registry.get(connector_id)
        if connector is None:
            raise ConnectorNotFoundError(connector_id)
        return connector

    def _lock(self, connector_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(connector_id, threading.Lock())

    def _secrets(self, connector_id: str) -> dict[str, Any]:
        """Decrypted credentials (`api_key`, `client_id`, `client_secret`), or {}."""
        ciphertext = self.store.credentials(connector_id)
        return {} if ciphertext is None else self.vault.decrypt(ciphertext)

    def _token_set(self, row: TokenRow | None) -> TokenSet | None:
        return None if row is None else TokenSet.model_validate(self.vault.decrypt(row.ciphertext))

    def _status(
        self,
        connector: Connector,
        row: StateRow | None,
        secrets: dict[str, Any] | None,
        tokens: TokenRow | None,
    ) -> tuple[Status, str | None]:
        if row is None or not row.enabled:
            return "disabled", None
        if connector.auth_type in ("none", "local"):
            return "connected", None
        if secrets is None:  # couldn't decrypt
            return "error", row.last_error
        if connector.auth_type == "api_key":
            return ("connected" if secrets.get("api_key") else "needs_auth"), None
        if row.status == "needs_reauth":
            return "needs_reauth", row.last_error
        if tokens is None:
            return "needs_auth", None
        # Still connected; last_error may note a transient refresh failure being retried.
        return "connected", row.last_error

    def _set_status(self, connector_id: str, status: Status, last_error: str | None = None) -> None:
        row = self.store.state(connector_id)
        self.store.set_state(
            connector_id,
            enabled=bool(row and row.enabled),
            status=status,
            last_error=last_error,
        )

    # --- Reading ----------------------------------------------------------------------------

    def info(self, connector_id: str) -> ConnectorInfo:
        return self._info(self._connector(connector_id), self.store.state(connector_id))

    def list_connectors(self) -> list[ConnectorInfo]:
        states = self.store.states()
        return [self._info(c, states.get(c.id)) for c in self.registry.all()]

    def _info(self, connector: Connector, row: StateRow | None) -> ConnectorInfo:
        secrets: dict[str, Any] | None = {}
        tokens: TokenRow | None = None
        error: str | None = None
        if connector.auth_type in ("api_key", "oauth2"):
            tokens = self.store.tokens(connector.id)
            try:
                secrets = self._secrets(connector.id)
            except (VaultError, VaultUnavailableError) as exc:
                secrets, error = None, str(exc)
            if not self.vault.available and row is not None and row.enabled:
                secrets, error = None, "ALFRED_VAULT_KEY is not set"
        status, last_error = self._status(connector, row, secrets, tokens)
        return ConnectorInfo(
            id=connector.id,
            name=connector.name,
            description=connector.description,
            auth_type=connector.auth_type,
            scopes=list(connector.scopes),
            specialist=connector.specialist,
            enabled=bool(row and row.enabled),
            status=status,
            last_error=error if status == "error" and error else last_error,
            has_api_key=bool(secrets and secrets.get("api_key")),
            has_oauth_client=bool(secrets and secrets.get("client_id")),
            token_expires_at=tokens.expires_at if tokens else None,
            tools=[ToolInfo(**t.model_dump()) for t in connector.tools],
        )

    def tool_meta(self, tool_name: str) -> ToolMeta | None:
        """Risk and privacy of a tool by name (confirmation gating, privacy routing)."""
        found = self.registry.tool_meta(tool_name)
        return None if found is None else found[1]

    # --- Changing ---------------------------------------------------------------------------

    def update(
        self,
        connector_id: str,
        *,
        enabled: bool | None = None,
        api_key: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> ConnectorInfo:
        """Enable/disable a connector and/or store its API key or OAuth client."""
        connector = self._connector(connector_id)
        if api_key is not None and connector.auth_type != "api_key":
            raise ConnectorConfigError(f"{connector.name} doesn't use an API key")
        if (client_id is not None or client_secret is not None) and connector.auth_type != "oauth2":
            raise ConnectorConfigError(f"{connector.name} doesn't use OAuth")
        if api_key is not None or client_id is not None or client_secret is not None:
            with self._lock(connector_id):
                secrets = self._secrets(connector_id)
                if api_key is not None:
                    secrets["api_key"] = api_key
                if client_id is not None:
                    secrets["client_id"] = client_id
                if client_secret is not None:
                    secrets["client_secret"] = client_secret
                self.store.set_credentials(connector_id, self.vault.encrypt(secrets))
        row = self.store.state(connector_id)
        self.store.set_state(
            connector_id,
            enabled=enabled if enabled is not None else bool(row and row.enabled),
            status=row.status if row else "needs_auth",
            last_error=row.last_error if row else None,
        )
        return self.info(connector_id)

    def delete_auth(self, connector_id: str) -> ConnectorInfo:
        """Forget the connector's tokens and API key (the OAuth client app is kept)."""
        self._connector(connector_id)
        with self._lock(connector_id):
            self.store.delete_tokens(connector_id)
            ciphertext = self.store.credentials(connector_id)
            if ciphertext is not None:
                try:
                    secrets = self.vault.decrypt(ciphertext)
                except (VaultError, VaultUnavailableError):
                    secrets = {}  # unreadable: drop it all
                secrets.pop("api_key", None)
                kept = {k: v for k, v in secrets.items() if k in ("client_id", "client_secret")}
                self.store.set_credentials(connector_id, self.vault.encrypt(kept) if kept else None)
            self._set_status(connector_id, "needs_auth")
        return self.info(connector_id)

    # --- OAuth ------------------------------------------------------------------------------

    def start_auth(self, connector_id: str, redirect_uri: str) -> str:
        """Begin an authorization: returns the provider URL to open in a browser."""
        connector = self._connector(connector_id)
        if connector.auth_type != "oauth2" or connector.oauth is None:
            raise ConnectorConfigError(f"{connector.name} doesn't use OAuth")
        client_id = self._secrets(connector_id).get("client_id")
        if not client_id:
            raise ConnectorConfigError(
                f"Set {connector.name}'s OAuth client_id first (PUT /connectors/{connector_id})"
            )
        self.store.purge_oauth_states(self.clock() - OAUTH_STATE_TTL)
        state = new_state()
        verifier = new_code_verifier() if connector.oauth.pkce else None
        payload = {"verifier": verifier, "redirect_uri": redirect_uri}
        self.store.add_oauth_state(
            state, connector_id, self.vault.encrypt(payload), created_at=self.clock()
        )
        return self.oauth.authorization_url(
            connector,
            client_id=client_id,
            redirect_uri=redirect_uri,
            state=state,
            code_verifier=verifier,
        )

    def complete_auth(self, connector_id: str, *, code: str, state: str) -> ConnectorInfo:
        """Finish an authorization from the provider's callback. Each `state` works once."""
        connector = self._connector(connector_id)
        pending = self.store.pop_oauth_state(state)
        if pending is None or pending.connector_id != connector_id:
            raise ConnectorAuthError("Unknown or already used authorization; start again.")
        if self.clock() - pending.created_at > OAUTH_STATE_TTL:
            raise ConnectorAuthError("The authorization expired; start again.")
        payload = self.vault.decrypt(pending.ciphertext)
        secrets = self._secrets(connector_id)
        try:
            tokens = self.oauth.exchange_code(
                connector,
                code=code,
                client_id=secrets.get("client_id", ""),
                client_secret=secrets.get("client_secret"),
                redirect_uri=payload["redirect_uri"],
                code_verifier=payload.get("verifier"),
                now=self.clock(),
            )
        except OAuthError as exc:
            raise ConnectorAuthError(str(exc)) from exc
        with self._lock(connector_id):
            self._save_tokens(connector_id, tokens)
            self._set_status(connector_id, "connected")
        return self.info(connector_id)

    def _save_tokens(self, connector_id: str, tokens: TokenSet) -> None:
        self.store.set_tokens(
            connector_id, self.vault.encrypt(tokens.model_dump(mode="json")), tokens.expires_at
        )

    def refresh(self, connector_id: str, *, force: bool = False) -> bool:
        """Refresh the connector's access token if it's due (or `force`). True if refreshed.

        On failure the connector becomes `needs_reauth`, its tools stop loading and the
        error is kept in `last_error`.
        """
        connector = self._connector(connector_id)
        with self._lock(connector_id):
            row = self.store.tokens(connector_id)  # re-read: another thread may have refreshed
            if row is None:
                return False
            if not force and (
                row.expires_at is None or row.expires_at - self.clock() > REFRESH_MARGIN
            ):
                return False
            try:
                current = self._token_set(row)
                assert current is not None
                if not current.refresh_token:
                    raise OAuthError(f"{connector.name} gave no refresh token; sign in again")
                secrets = self._secrets(connector_id)
                tokens = self.oauth.refresh(
                    connector,
                    refresh_token=current.refresh_token,
                    client_id=secrets.get("client_id", ""),
                    client_secret=secrets.get("client_secret"),
                    now=self.clock(),
                )
            except OAuthError as exc:
                logger.warning("connector %s: token refresh failed: %s", connector_id, exc)
                if exc.retryable:
                    # Network blip or provider outage: the grant may still be fine. Keep the
                    # status, note the error and let the next refresher tick try again.
                    self._set_status(connector_id, "connected", str(exc))
                else:
                    self._set_status(connector_id, "needs_reauth", str(exc))
                return False
            except (VaultError, VaultUnavailableError) as exc:
                logger.warning("connector %s: token refresh failed: %s", connector_id, exc)
                self._set_status(connector_id, "needs_reauth", str(exc))
                return False
            self._save_tokens(connector_id, tokens)
            self._set_status(connector_id, "connected")
            return True

    def refresh_due(self) -> list[str]:
        """Refresh every enabled OAuth connector whose token expires within the margin."""
        states = self.store.states()
        refreshed = []
        for connector_id in self.store.tokens_expiring_before(self.clock() + REFRESH_MARGIN):
            connector = self.registry.get(connector_id)
            row = states.get(connector_id)
            if connector is None or row is None or not row.enabled:
                continue
            if row.status == "needs_reauth":
                continue  # a human has to sign in again
            if self.refresh(connector_id):
                refreshed.append(connector_id)
        return refreshed

    # --- Tools ------------------------------------------------------------------------------

    def credentials_for(self, connector_id: str) -> Credentials | None:
        """Credentials to build the connector's tools with, or None if it isn't usable now."""
        connector = self._connector(connector_id)
        info = self.info(connector_id)
        if info.status != "connected":
            return None
        if connector.auth_type in ("none", "local"):
            return Credentials()
        try:
            if connector.auth_type == "api_key":
                return Credentials(api_key=self._secrets(connector_id)["api_key"])
            row = self.store.tokens(connector_id)
            expires_at = row.expires_at if row is not None else None
            if expires_at is not None and expires_at - self.clock() < MIN_TOKEN_LIFETIME:
                if not self.refresh(connector_id, force=True):
                    return None
                row = self.store.tokens(connector_id)
            tokens = self._token_set(row)
        except (VaultError, VaultUnavailableError) as exc:
            logger.warning("connector %s: can't read credentials: %s", connector_id, exc)
            return None
        if tokens is None:
            return None
        return Credentials(
            access_token=tokens.access_token, extra={"token_type": tokens.token_type}
        )

    def _usable(self, specialist: str) -> list[tuple[Connector, Credentials]]:
        usable = []
        for connector in self.registry.for_specialist(specialist):
            creds = self.credentials_for(connector.id)
            if creds is not None:
                usable.append((connector, creds))
        return usable

    async def load_tools(self, specialist: str) -> list[LoadedTools]:
        """Tools of `specialist`'s enabled, connected connectors, ready to give an agent.

        Disabled or unhealthy connectors contribute nothing; a connector whose tools fail to
        build is skipped (and logged) rather than failing the request. Only declared tools
        are exposed.
        """
        usable = await asyncio.to_thread(self._usable, specialist)
        loaded: list[LoadedTools] = []
        async with self._mcp_lock:
            usable_ids = {c.id for c, _ in usable}
            for connector in self.registry.for_specialist(specialist):
                if connector.id not in usable_ids:
                    await self._close_mcp(connector.id)
            for connector, creds in usable:
                try:
                    loaded.append(await self._build(connector, creds))
                except Exception as exc:  # one broken connector mustn't break the request
                    logger.warning("connector %s: can't load tools: %s", connector.id, exc)
        return loaded

    async def _build(self, connector: Connector, creds: Credentials) -> LoadedTools:
        source = connector.tool_source
        allowed = set(connector.tool_names)
        if isinstance(source, AgnoToolkitSource):
            return restrict_toolkit(source.factory(creds), allowed)
        fingerprint = _fingerprint(creds)
        cached = self._mcp.get(connector.id)
        if cached is not None:
            if cached[0] == fingerprint and await cached[1].is_alive():
                return cached[1]
            await self._close_mcp(connector.id)
        tools = self._mcp_tools(connector, source, creds)
        await tools.connect()
        if not tools.initialized:
            raise RuntimeError("MCP server didn't start")
        self._mcp[connector.id] = (fingerprint, tools)
        return tools

    @staticmethod
    def _mcp_tools(
        connector: Connector, source: McpStdioSource | McpHttpSource, creds: Credentials
    ) -> MCPTools:
        include = connector.tool_names
        if isinstance(source, McpStdioSource):
            params = StdioServerParameters(
                command=source.command,
                args=list(source.args),
                env=source.env(creds) if source.env else None,
            )
            return MCPTools(
                name=connector.id,
                server_params=params,
                transport="stdio",
                include_tools=include,
                timeout_seconds=source.timeout_seconds,
            )
        http = StreamableHTTPClientParams(
            url=source.url, headers=source.headers(creds) if source.headers else None
        )
        return MCPTools(
            name=connector.id,
            server_params=http,
            transport="streamable-http",
            include_tools=include,
            timeout_seconds=source.timeout_seconds,
        )

    async def _close_mcp(self, connector_id: str) -> None:
        cached = self._mcp.pop(connector_id, None)
        if cached is not None:
            try:
                await cached[1].close()
            except Exception as exc:
                logger.warning("connector %s: error closing MCP server: %s", connector_id, exc)

    async def aclose(self) -> None:
        """Close every MCP session and stop stdio servers (server shutdown)."""
        async with self._mcp_lock:
            for connector_id in list(self._mcp):
                await self._close_mcp(connector_id)


def build_connector_service() -> ConnectorService:
    settings = get_settings()
    key = settings.vault_key.get_secret_value() if settings.vault_key else None
    engine = create_engine(settings.database_url.get_secret_value(), pool_pre_ping=True)
    return ConnectorService(default_registry(), ConnectorStore(engine), Vault(key))


@lru_cache
def get_connector_service() -> ConnectorService:
    """The process-wide connector service. Use as a FastAPI dependency (overridden in tests)."""
    return build_connector_service()
