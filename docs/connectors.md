# Connectors

A connector is a service Alfred can use: its auth, the tools it provides and how risky and
private each tool is. Code is in `server/alfred_server/connectors/`; the design and why is in
[ADR 0010](decisions/0010-connector-registry.md). Per-connector setup notes go in
`docs/connectors/<name>.md` as connectors are added.

## Concepts

| Piece | Where | What |
| --- | --- | --- |
| `Connector` | `connectors/models.py` | Definition: `id`, `name`, `auth_type`, `scopes`, `tool_source`, `tools` (`ToolMeta`), `specialist`, `oauth`. |
| `ToolMeta` | `connectors/models.py` | Per tool: `risk` (`read` / `write` / `irreversible`), `privacy` (`cloud_ok` / `local_only`), for gated tools a `confirm_summary`, and optionally `default_tier` (a lower confirmation tier, e.g. media controls). Undeclared tools never load. |
| Tool sources | `connectors/models.py` | `AgnoToolkitSource(factory)`, `McpStdioSource(command, args, env)`, `McpHttpSource(url, headers)`. Factories get decrypted `Credentials`. |
| `ConnectorRegistry` | `connectors/registry.py` | All definitions; `for_specialist()`, `tool_meta(tool_name)`. |
| `ConnectorService` | `connectors/service.py` | Status, credentials, OAuth, refresh and `load_tools(specialist)`. Get it with `get_connector_service()`. |
| Vault | `connectors/vault.py` | Fernet encryption with `ALFRED_VAULT_KEY`. Only ciphertext is stored. |

Statuses: `disabled`, `needs_auth` (enabled, no key/tokens yet), `connected` (the only status
whose tools load), `needs_reauth` (a token refresh failed: sign in again), `error` (secrets
can't be read, e.g. `ALFRED_VAULT_KEY` unset or changed).

## Using connectors in an agent

Register the specialist with `uses_connectors=True` and the router loads its connectors' tools
(every connector whose `specialist` is its name) on each request, adds them to the agent the
factory built and gates them ([ADR 0014](decisions/0014-spotify-media.md)):

```python
registry.register(Specialist("media", "music: …", _media_factory, uses_connectors=True))
```

The factory builds the agent with only its own tools (if any). Behind it is:

```python
tools = await get_connector_service().load_tools("media")   # Toolkits and MCPTools
```

`load_tools` returns only enabled, connected connectors' tools, refreshes an expiring token
first, and skips (and logs) a connector whose tools fail to build. If loading fails altogether
the agent runs without them, so its instructions should say what to tell Dan when it has no
tools (e.g. "Spotify isn't connected yet"). Look up a tool's risk or
privacy with `get_connector_service().tool_meta("tool_name")`.

## Risk and confirmations

The router gates every agent's tools by risk ([ADR 0013](decisions/0013-confirmation-gating.md)):
`read` tools run straight away, `write` tools pause the run until Dan says yes (by text, voice or
in the app), and `irreversible` tools until he approves them in the app. Dan can override a
tool's risk with `PUT /settings/permissions/{connector}/{tool}` `{"risk": ...}`.

Ordinary control that's easily put back and that Dan wouldn't want asked about every time
(play, pause, volume) can declare `default_tier="read"` while keeping its honest `risk="write"`:
it runs without asking unless Dan tightens it. The registry rejects a `default_tier` above
`risk`. Use it sparingly; the reviewer checks every use.

Give every `write` or `irreversible` tool a `confirm_summary`: a sentence starting with a verb
that is formatted with the call's arguments and read back to Dan, e.g.
`"Send an email to {to}: '{subject}'"`. It's also used in "OK, I won't send an email to …", so
start it with a capital verb and no full stop. The dummy `test_messages` connector
(`connectors/builtin/test_messages.py`) is a worked example; set `ALFRED_TEST_TOOLS_ENABLED=true` to give it to the general agent and try the flow in `/ui/chat`.

## Adding a connector

1. Create `server/alfred_server/connectors/builtin/<name>.py` with a `Connector`.
2. Add it to `BUILTIN_CONNECTORS` in `connectors/builtin/__init__.py`.
3. Declare every tool the agent may use in `tools`, with honest `risk` and `privacy`, and a
   `confirm_summary` for anything not `read`. Tool names must be unique across all
   connectors (use a prefix such as `spotify_`).
4. Test it: definition registers, tools load when enabled, undeclared tools are dropped.
5. Write `docs/connectors/<name>.md`: how to get credentials, scopes, redirect URI.
6. If it's a new specialist, register it with `uses_connectors=True` and give its tools eval
   fixtures (`server/evals/README.md`).

Connectors so far: [Spotify](connectors/spotify.md) (OAuth2, own async Web API toolkit),
[Home Assistant](connectors/home-assistant.md) (API key, own toolkit over HA's MCP server).

When an MCP server's tools are too generic to give one honest risk (Home Assistant's
`HassTurnOn` switches lights and unlocks doors alike), wrap them in an Agno toolkit of your own
whose tools each call the server with the risky arguments pinned, rather than exposing the
server's tools directly ([ADR 0019](decisions/0019-home-assistant.md)).

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
