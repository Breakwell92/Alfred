# 0026: Privacy routing: keeping `local_only` tool output local

- **Status:** accepted
- **Date:** 2026-10-25
- **Ticket:** STU-58

## Context

Connectors declare a `privacy` per tool (ADR 0010): `cloud_ok` or `local_only`. A
`local_only` tool returns something that shouldn't leave the house unless Dan says so: email,
Slack or SMS bodies, notifications, Home Assistant states that reveal who's home. The router
(ADR 0009) may send a request to a cloud alias when it's complex, long or explicitly asked
for, and it retries a failed local run on cloud, so without a check that output could reach a
cloud provider.

Dan decided that, for now, everything may go to cloud models. The mechanism still has to exist
so he can switch it off globally or per connector without a code change.

## Decision

**Settings.** `PrivacySettings` (`alfred_server/privacy/`): `allow_private_to_cloud` (default
`true`) and `connectors`, a map of connector id to `true`/`false` that overrides the global
switch for that connector. One JSON row in the Postgres table `privacy_settings`, read on every
request (no cache, so a change applies to the next request). `GET /settings/privacy` and
`PUT /settings/privacy` (protected; the PUT is a full replacement, both fields required, unknown
connector ids are 422). `blocked_tools(registry, settings)` gives the names of the `local_only`
tools whose connector isn't allowed.

**Router pre-check.** Once a run's agent is built and its tools loaded and gated
(`Router._agent`), the router looks for a blocked tool among them (Agno toolkits by function
name; an MCP toolkit counts with every tool its connector declares). If there is one and the
chosen alias is cloud, the run moves to the local tier the classification would have used
(`local-router` for simple, else `local-main`) with reason `privacy`, and a local run is marked
private so the cloud fallback never applies to it. An explicit cloud request ("ask Claude …")
is told why: "That involves private information, so I kept it on the local model." The check
runs on `run`, `stream` and `resume` (a paused run continued on cloud also checks the messages
it already holds). It's done after the tools load, rather than at routing time, because whether
a tool is loaded depends on the connector being enabled and connected.

**Gateway backstop.** `gateway_model()` now returns `GatewayModel`, an `OpenAILike` whose four
call paths (`invoke`, `ainvoke`, `invoke_stream`, `ainvoke_stream`) first look at the outgoing
messages. On a cloud alias, a `tool` message whose `tool_name` is in the model's
`private_tools` makes it raise `PrivateContentError` before anything is sent, and record the
tools in `refused_tools`. The router arms every model it builds with the request's blocked set.
The tag is the tool name Agno puts on every tool-result message and keeps in stored history,
resolved against the registry; no extra marker is added, so nothing provider-specific is sent
and results from earlier turns are recognised too.

This catches what the pre-check can't see: history. A Comms turn that read the inbox locally,
followed in the same session by a `general` turn routed to cloud, would otherwise send the inbox
to the cloud model as history. Agno turns the exception into a failed run, so the router reads
`refused_tools`, forgets the failed run and retries once locally (reason `privacy`), unless a
tool already did something in it (a retry would repeat it); then the turn fails.

**Fail closed.** If the settings can't be read, every `local_only` tool counts as blocked. The
API answers 503.

**Logging.** A run that is kept local for privacy, or has private tools loaded, carries
`metadata["routing"]["privacy"] = {"connectors": [...], "downgraded_from": <alias or null>}`
next to `reason`, and the router logs each downgrade and each refused call.

## Consequences

- With the default settings nothing changes: no tool is blocked and the guard is disarmed.
- Turning the switch off makes Home Assistant turns (`ha_get_state`) and later Comms turns
  local-only, and with them the routines specialist, which reads Home Assistant states.
- A session that has used a blocked tool keeps its later turns local for as long as that
  output is in the history window (`HISTORY_RUNS`).
- Router construction takes an optional `privacy` gate. Without one (tests, evals) the default
  applies: private output may go to cloud.
- Future connectors with message bodies (Gmail, Slack, SMS) must declare those tools
  `local_only`; that's all they need to do.
