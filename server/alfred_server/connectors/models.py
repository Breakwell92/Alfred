"""Connector definitions: what a connector is, how it authenticates and where its tools come from.

A connector is pure data plus a tool factory. Its runtime state (enabled, status, credentials,
tokens) lives in Postgres (`store.py`); see docs/connectors.md for how to add one.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from agno.tools import Toolkit
from pydantic import BaseModel, Field

AuthType = Literal["oauth2", "api_key", "local", "none"]
# What a tool can do, for confirmation gating (STU-57): `read` never changes anything,
# `write` changes something that can be put back, `irreversible` can't be undone.
Risk = Literal["read", "write", "irreversible"]
# `local_only`: the tool's output must never be sent to a cloud model (STU-58).
Privacy = Literal["cloud_ok", "local_only"]
# `connected` is the only healthy status: tools load only then.
Status = Literal["disabled", "needs_auth", "connected", "needs_reauth", "error"]


class ToolError(str):
    """A tool's answer saying it failed or did nothing, e.g. "Home Assistant isn't reachable".

    It's still a plain sentence for the agent to say; routines (docs/decisions/0021-routines.md)
    use the type to report the step as failed. Tools that never raise return one of these on
    every failure path.
    """

    __slots__ = ()


class ToolMeta(BaseModel):
    """Metadata for one tool a connector exposes. Tools without it are never loaded."""

    name: str
    description: str
    risk: Risk
    privacy: Privacy = "cloud_ok"
    # What a confirmation prompt says the call will do, formatted with the call's arguments,
    # e.g. "Send an email to {to}: '{subject}'". Unset (or a missing argument) falls back to
    # the tool name and its arguments. See docs/decisions/0013-confirmation-gating.md.
    confirm_summary: str | None = None
    # The confirmation tier when Dan hasn't overridden it, if lower than `risk`: e.g. media
    # controls are honestly `write` but run without asking. Never higher than `risk` (the
    # registry checks). See docs/decisions/0013-confirmation-gating.md.
    default_tier: Risk | None = None
    # Builds the confirmation summary when a template can't (e.g. `save_routine` reading back
    # each of the routine's steps): called with the `ToolPolicy` and the call's arguments.
    summarise: Callable[[Any, Mapping[str, Any]], str] | None = Field(default=None, exclude=True)

    @property
    def tier(self) -> Risk:
        """The tool's confirmation tier before any override of Dan's."""
        return self.default_tier or self.risk


@dataclass(frozen=True)
class OAuth2Config:
    """Provider endpoints for the generic authorization-code flow (`oauth.py`)."""

    authorize_url: str
    token_url: str
    # Provider-specific extras, e.g. Google's {"access_type": "offline", "prompt": "consent"}.
    extra_authorize_params: Mapping[str, str] = field(default_factory=dict)
    pkce: bool = True


@dataclass(frozen=True)
class Credentials:
    """Decrypted secrets handed to a tool source when its tools are built. Never logged."""

    api_key: str | None = None
    access_token: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgnoToolkitSource:
    """Tools from an Agno `Toolkit` built per load by `factory`."""

    factory: Callable[[Credentials], Toolkit]


@dataclass(frozen=True)
class McpStdioSource:
    """An MCP server run as a subprocess (stdio). The registry owns its lifecycle."""

    command: str
    args: tuple[str, ...] = ()
    # Extra environment for the subprocess, e.g. {"API_TOKEN": creds.api_key}.
    env: Callable[[Credentials], dict[str, str]] | None = None
    timeout_seconds: int = 10


@dataclass(frozen=True)
class McpHttpSource:
    """A remote MCP server over streamable HTTP."""

    url: str
    # Request headers, e.g. {"Authorization": f"Bearer {creds.access_token}"}.
    headers: Callable[[Credentials], dict[str, str]] | None = None
    timeout_seconds: int = 10


ToolSource = AgnoToolkitSource | McpStdioSource | McpHttpSource


@dataclass(frozen=True)
class Connector:
    """One service Alfred can use. Register it in `registry.py`."""

    id: str
    name: str
    description: str
    auth_type: AuthType
    tool_source: ToolSource
    tools: tuple[ToolMeta, ...]
    # The specialist agent that loads this connector's tools (e.g. "general", "home").
    specialist: str
    scopes: tuple[str, ...] = ()
    oauth: OAuth2Config | None = None

    @property
    def tool_names(self) -> list[str]:
        return [t.name for t in self.tools]


class TokenSet(BaseModel):
    """OAuth tokens as stored (encrypted) in the vault."""

    access_token: str
    refresh_token: str | None = None
    expires_at: datetime | None = None
    token_type: str = "Bearer"
    scope: str | None = None
