# 0012: Evaluation harness

- **Status:** accepted
- **Date:** 2026-10-25
- **Ticket:** STU-60

## Context

How good Alfred feels depends mostly on the local models: whether the router sends a request to
the right specialist, and whether the specialist calls the right tools with the right arguments.
Model, prompt and routing changes need measuring, not guessing, and the same suite has to keep
working as specialists and connectors are added. Running it must never change anything real
(lights, emails, messages) or spend cloud budget by accident.

## Decision

`server/evals/` (outside the `alfred_server` package, run with `uv run python -m evals.run`):

- **Cases** are YAML (`cases/*.yaml`): input, optional `RequestContext` fields, expected
  specialist, expected tools with an argument subset, forbidden tools (`*` = none at all), the
  highest tier the router may want, optional text the reply must contain, notes. Cases for
  specialists or connectors that don't exist yet are tagged `pending` and skipped; their
  specialist and tool names are placeholders that the ticket adding each one renames. Every case
  runs at a fixed time (14:05, Saturday 24 October 2026, Europe/London) so time answers can be
  checked.
- **Mock mode** uses Agno's `tool_hooks`: the runner adds a hook to the specialist's agent that
  records each call and answers it from `fixtures/tools.yaml` without calling the real function.
  This works the same for plain functions, toolkits and MCP tools, so connector tools will be
  mocked without connector changes. A tool without a fixture gets `{"ok": true}` and is listed.
- **Routing** is the real `Router`: classification on `local-router`, then `select_model`
  asked with a budget gate that allows every cloud alias, so the score shows where the router
  *would* send the request (`max_tier`). The run itself uses a router with cloud off, so it never
  falls back to cloud; with `--model-alias` the specialist runs on that alias, otherwise on the
  router's local choice. `--allow-cloud` lets runs use cloud (costs money).
- **Scores** per case: specialist, required tools, forbidden tools, tier, reply text, plus
  latency (routing and total, after an untimed warm-up call per model) and tokens. A case passes
  when every check it has passes. Reports are markdown (summary table per alias and a per-case
  comparison) and JSON in `evals/reports/` (git-ignored).
- **Comparing models**: repeat `--model-alias`. A second local model is served by the proxy as
  `eval-candidate` when `LITELLM_EVAL_CANDIDATE_MODEL` is set (`infra/litellm/config.yaml`). It is
  not a server alias (`ModelAlias`); only the eval runner's model factory accepts it.
- The starter set is 53 cases: 16 runnable now on the `general` (echo) specialist, tagged
  `smoke` (time and date, chat, facts, explicit and implied cloud requests, refusals), and 37
  pending (weather, lights, heating, calendar, email with confirmation, music, Plex, TV,
  reminders and schedules, "movie time", ambiguous people, refusals with tools available).

## Consequences

- Run the smoke set before merging changes to prompts, routing or model configuration
  (`CLAUDE.md`). CI can't: it needs the gateway and the GPU.
- Each specialist or connector ticket adds fixtures for its tools, renames the placeholders in
  `cases/pending.yaml` and un-pends its cases.
- Confirmation gating (STU-57) isn't scored yet: email and message sends are pending cases whose
  notes say they must pause. Once confirmations exist, add a check that the call was paused.
- The model comparison result is recorded in `0003-local-models.md`.
- Reply quality beyond `reply_contains` (tone, refusals) isn't scored; an LLM judge could be
  added later if needed.
