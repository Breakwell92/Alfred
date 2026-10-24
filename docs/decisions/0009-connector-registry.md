# 0009: Connector registry and encrypted token vault

- **Status:** accepted
- **Date:** 2026-10-24
- **Ticket:** STU-56

## Context

Alfred will use many services (Google, Home Assistant, Spotify, Plex, Slack, SmartThings,
GitHub…). Each needs authentication, provides tools either as an Agno toolkit or as an MCP
server, and its tools have different risks (reading the calendar vs sending an email) and
privacy needs (some content must never reach a cloud model). Service credentials must live
only on the server; the phone never holds them. Specialist agents should only get the tools
of their own, working connectors.

## Decision

- **Definitions in code, state in Postgres.** A `Connector` (`alfred_server/connectors/models.py`)
  is a frozen definition: id, auth type (`oauth2` | `api_key` | `local` | `none`), scopes,
  tool source, the specialist it belongs to, and `ToolMeta` per tool (`risk`: `read` |
  `write` | `irreversible`; `privacy`: `cloud_ok` | `local_only`). Built-ins are registered
  in `connectors/builtin/`. Enabled/status/last error, credentials and tokens are rows in
  `connector_state`, `connector_credentials`, `connector_tokens` (and pending authorizations
  in `connector_oauth_states`).
- **Declared tools only.** Every loaded toolkit or MCP server is restricted to the tool names
  the connector declares (MCP `include_tools`; undeclared toolkit functions dropped), so no
  tool without risk/privacy metadata ever reaches an agent. Tool names are unique across all
  connectors so `ConnectorRegistry.tool_meta(name)` can answer for gating (STU-57) and
  privacy routing (STU-58).
- **Vault: Fernet with `ALFRED_VAULT_KEY`.** `cryptography`'s Fernet (AES-128-CBC +
  HMAC-SHA256, authenticated, timestamped) is simple, well reviewed and enough for one
  server. API keys, OAuth client id/secret and token sets are each one encrypted JSON blob;
  only `expires_at` is in clear, so the refresher can find due tokens. Unset or invalid key
  → secrets can't be stored (503) and credentialed connectors show `error` and load nothing
  (fail closed). Connectors with no secret (`none`/`local`) still work.
- **OAuth client registration in the vault (Dan).** Each OAuth connector's client id/secret is
  set with `PUT /connectors/{id}` and stored encrypted, not as per-connector env vars, so
  adding a connector needs no config change and the app can set it.
- **Generic OAuth2 authorization-code flow with PKCE (S256).** The token-protected
  `GET /connectors/{id}/auth/start` creates a random one-time `state` (10-minute life, PKCE
  verifier stored encrypted) and returns the provider URL. The callback
  `GET /connectors/{id}/auth/callback` is **open** (Dan): the provider redirects the
  browser there, so it can't carry the API token. It is guarded by the one-time state,
  consumed atomically on first use (also on provider errors), and PKCE. It's the second
  unauthenticated route after `/health`.
- **Refresh.** A background task (`refresher.py`, every `ALFRED_CONNECTOR_REFRESH_INTERVAL_S`,
  only when the vault key is set) refreshes enabled connectors' tokens expiring within
  5 minutes; tools are never built with a token under 60 s from expiry (refreshed on use).
  A failed refresh sets `needs_reauth` with the error and isn't retried until Dan signs in
  again. Providers that don't rotate refresh tokens keep the old one.
- **MCP lifecycle owned by the service.** `load_tools` connects MCP toolkits itself and caches
  them per connector, so Agno doesn't close them after each run: stdio servers keep running
  between requests. They're restarted when credentials change or the session dies, closed when
  the connector stops being usable (on the next load for its specialist) and on shutdown.
  Agno's `MCPTools` needs `fastmcp` 4.x and `mcp` 2.x.
- **Sync store and OAuth HTTP, async `load_tools`.** Like the budget service, the store uses a
  sync SQLAlchemy engine called from FastAPI's threadpool; `load_tools` runs those calls in a
  thread so agents' event loop never blocks.

## Consequences

- Later connector tickets add a module under `connectors/builtin/` and a line in
  `BUILTIN_CONNECTORS`; docs/connectors.md is the how-to.
- Google web OAuth clients don't accept a plain-HTTP Tailscale host name as a redirect URI.
  STU-61 has to pick a redirect that Google accepts (e.g. `http://localhost:8000` from the
  server itself, or HTTPS via Tailscale Serve) and set `ALFRED_PUBLIC_URL` to match.
- Losing or changing `ALFRED_VAULT_KEY` makes every stored secret unreadable: connectors show
  `error` until re-entered. Key rotation (Fernet `MultiFernet`) isn't implemented.
- A disabled connector's MCP server is stopped on the next `load_tools` for its specialist,
  not the instant it's disabled.
