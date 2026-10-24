# Connectors

A connector is a service Alfred can use: its auth, the tools it provides and how risky and
private each tool is. Code is in `server/alfred_server/connectors/`; the design and why is in
[ADR 0010](decisions/0010-connector-registry.md). Per-connector setup notes go in
`docs/connectors/<name>.md` as connectors are added.

## Concepts

| Piece | Where | What |
| --- | --- | --- |
| `Connector` | `connectors/models.py` | Definition: `id`, `name`, `auth_type`, `scopes`, `tool_source`, `tools` (`ToolMeta`), `specialist`, `oauth`. |
| `ToolMeta` | `connectors/models.py` | Per tool: `risk` (`read` / `write` / `irreversible`) and `privacy` (`cloud_ok` / `local_only`). Undeclared tools never load. |
| Tool sources | `connectors/models.py` | `AgnoToolkitSource(factory)`, `McpStdioSource(command, args, env)`, `McpHttpSource(url, headers)`. Factories get decrypted `Credentials`. |
| `ConnectorRegistry` | `connectors/registry.py` | All definitions; `for_specialist()`, `tool_meta(tool_name)`. |
| `ConnectorService` | `connectors/service.py` | Status, credentials, OAuth, refresh and `load_tools(specialist)`. Get it with `get_connector_service()`. |
| Vault | `connectors/vault.py` | Fernet encryption with `ALFRED_VAULT_KEY`. Only ciphertext is stored. |

Statuses: `disabled`, `needs_auth` (enabled, no key/tokens yet), `connected` (the only status
whose tools load), `needs_reauth` (a token refresh failed: sign in again), `error` (secrets
can't be read, e.g. `ALFRED_VAULT_KEY` unset or changed).

## Using connectors in an agent

```python
from alfred_server.connectors.service import get_connector_service

tools = await get_connector_service().load_tools("home")   # Toolkits and MCPTools
agent = build_agent("home", ctx=ctx, alias=alias, db=db, tools=tools, ...)
```

`load_tools` returns only enabled, connected connectors' tools, refreshes an expiring token
first, and skips (and logs) a connector whose tools fail to build. Look up a tool's risk or
privacy with `get_connector_service().tool_meta("tool_name")`.

## Adding a connector

1. Create `server/alfred_server/connectors/builtin/<name>.py` with a `Connector`.
2. Add it to `BUILTIN_CONNECTORS` in `connectors/builtin/__init__.py`.
3. Declare every tool the agent may use in `tools`, with honest `risk` and `privacy`. Tool
   names must be unique across all connectors (use a prefix such as `spotify_`).
4. Test it: definition registers, tools load when enabled, undeclared tools are dropped.
5. Write `docs/connectors/<name>.md`: how to get credentials, scopes, redirect URI.

### Worked example: the `time` connector

`connectors/builtin/clock.py`, no auth and one read-only tool:

```python
def build_time_toolkit(_creds: Credentials) -> Toolkit:
    return Toolkit(name="time", tools=[make_get_time(get_settings().timezone)])


TIME_CONNECTOR = Connector(
    id="time",
    name="Time",
    description="The current date and time in the user's timezone.",
    auth_type="none",
    tool_source=AgnoToolkitSource(build_time_toolkit),
    tools=(ToolMeta(name="get_time", description="Current local date and time for the user.", risk="read"),),
    specialist="general",
)
```

Enable it and load its tools:

```bash
curl -X PUT -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"enabled": true}' http://glados:8000/connectors/time
```

```python
await get_connector_service().load_tools("general")   # [Toolkit(time: get_time)]
```

### An API-key connector with an Agno toolkit

```python
Connector(
    id="weather",
    name="Weather",
    description="Forecasts.",
    auth_type="api_key",
    tool_source=AgnoToolkitSource(lambda creds: WeatherTools(api_key=creds.api_key)),
    tools=(ToolMeta(name="weather_forecast", description="Forecast for a place", risk="read"),),
    specialist="general",
)
```

Set the key with `PUT /connectors/weather {"enabled": true, "api_key": "..."}`.

### An MCP server

Stdio (Alfred starts the process and keeps it running between requests):

```python
tool_source=McpStdioSource(
    command="npx",
    args=("-y", "@example/mcp-server"),
    env=lambda creds: {"EXAMPLE_TOKEN": creds.api_key or ""},
)
```

Remote, streamable HTTP:

```python
tool_source=McpHttpSource(
    url="https://mcp.example.com/mcp",
    headers=lambda creds: {"Authorization": f"Bearer {creds.access_token}"},
)
```

Only tools listed in `tools` are exposed (`include_tools`), whatever else the server offers.

### An OAuth2 connector

```python
Connector(
    id="google",
    name="Google",
    description="Gmail, Calendar and Tasks.",
    auth_type="oauth2",
    scopes=("https://www.googleapis.com/auth/calendar.readonly",),
    oauth=OAuth2Config(
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        extra_authorize_params={"access_type": "offline", "prompt": "consent"},
    ),
    tool_source=...,
    tools=(...),
    specialist="planner",
)
```

Setting it up:

1. Register an OAuth app with the provider. Redirect URI:
   `<ALFRED_PUBLIC_URL>/connectors/<id>/auth/callback` (e.g.
   `http://glados:8000/connectors/google/auth/callback`). Some providers (Google web clients)
   only accept HTTPS or `localhost`: use a URL they accept and set `ALFRED_PUBLIC_URL` to it.
2. `PUT /connectors/<id>` with `{"enabled": true, "oauth_client": {"client_id": "...", "client_secret": "..."}}`.
3. `GET /connectors/<id>/auth/start` (with the API token) returns `{"url": ...}`. Open it in a
   browser and sign in. The provider redirects to the callback, which stores the tokens and
   says "connected".
4. Tokens refresh in the background. If the provider rejects a refresh the connector shows
   `needs_reauth`: repeat step 3. Network errors and provider outages are retried
   automatically (the error shows in `last_error` meanwhile).

## REST API

| Route | Auth | What |
| --- | --- | --- |
| `GET /connectors` | token | Every connector: definition, `enabled`, `status`, `last_error`, `has_api_key`, `has_oauth_client`, `token_expires_at`, tools with risk/privacy. Never secrets. |
| `PUT /connectors/{id}` | token | Partial update: `enabled`, `api_key`, `oauth_client {client_id, client_secret}`. 422 for the wrong kind of secret, 503 if `ALFRED_VAULT_KEY` is unset. |
| `DELETE /connectors/{id}/auth` | token | Forget tokens and API key (the OAuth client is kept). |
| `GET /connectors/{id}/auth/start` | token | `{url}` to sign in at the provider. |
| `GET /connectors/{id}/auth/callback` | **open** | Provider redirect target. Accepts only a one-time `state` from `/auth/start` (10 minutes), plus PKCE. Shows a provider `error` only if it's a plain error code. |

## Configuration

| Variable | Default | What |
| --- | --- | --- |
| `ALFRED_VAULT_KEY` | unset | Fernet key for secrets. Required for any connector with a secret; never change it once set. |
| `ALFRED_PUBLIC_URL` | request URL | Base for OAuth redirect URIs. |
| `ALFRED_CONNECTOR_REFRESH_INTERVAL_S` | 60 | Background token refresh check interval. |
