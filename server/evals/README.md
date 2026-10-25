# Evals

Typical requests run through the real router and specialists, with every tool mocked, scored on
routing and tool calls. Use them to choose models and to check every change to prompts, routing
or model configuration (`CLAUDE.md`: run the smoke set before merging those). Design:
`docs/decisions/0012-eval-harness.md`.

## Running

From `server/`, with the compose stack up and `ALFRED_LITELLM_BASE_URL` / `ALFRED_LITELLM_API_KEY`
set (as for the server, e.g. in `server/.env`):

```bash
uv run python -m evals.run --model-alias local-main --tag smoke     # the smoke set on local-main
uv run python -m evals.run --tag smoke                              # on whatever the router picks
uv run python -m evals.run --model-alias local-main --model-alias eval-candidate   # compare two
uv run python -m evals.run --case time-now -v                       # one case, with logs
```

It prints a line per case and a summary table, writes `reports/<time>-<aliases>.md` and `.json`
(git-ignored), and exits 1 if any case failed.

- `--model-alias` sets the model the specialist runs on, alone: a failed run is never retried
  on another model, so candidates are compared like for like. The classifier always runs on
  `local-router`. Without it, the run uses the router's choice (cloud fallback included).
- The router's model selection is first asked with a budget that allows every cloud alias, so the
  report shows where it *would* send each request (scored by `max_tier`). Nothing is spent on
  that decision.
- Runs stay local unless `--allow-cloud` is given. Then every cloud call goes through the real
  budget service (`get_budget_service().decide`), so the cap, tiers and enabled providers apply
  and a refusal keeps the run local (or fails the case, for a forced cloud alias). A cloud
  `--model-alias` without `--allow-cloud` is refused before anything runs.
- A model that isn't a server alias can be compared by serving it on the proxy as
  `eval-candidate`: set `LITELLM_EVAL_CANDIDATE_MODEL` (an Ollama tag) in `infra/.env` and
  restart LiteLLM. Only two models fit in VRAM next to each other, so the first case on each
  alias waits for a load; the runner warms each model up before timing.

## Cases

`cases/*.yaml`, each a list of:

```yaml
- id: lights-on-room              # unique, kebab-case
  input: Turn on the kitchen lights.
  tags: [smoke, home]             # `pending` = skipped (specialist/connector not built yet)
  context: {active_room: kitchen} # RequestContext fields over the defaults in cases.py
  expected_specialist: home
  expected_tools:                 # each must be called with at least these arguments
    - {name: light_turn_on, args_subset: {area: kitchen}}
    # args_contain: {device: kitchen}  # value contains this text (any case), e.g. "the kitchen"
  must_not_call: [light_turn_off] # ["*"] = no tools at all
  max_tier: local                 # local = the router must not want cloud
  reply_contains: ["kitchen"]     # optional, case-insensitive
  expect_confirmation: irreversible  # optional: the run must pause for a write/irreversible OK
  notes: Why the case exists.
```

Every case runs at 14:05 on Saturday 24 October 2026 (Europe/London) unless its `context` sets
`now`. String arguments are compared case-insensitively; extra arguments and extra tool calls
are allowed unless forbidden.

## Mock tools

`fixtures/tools.yaml` maps a tool name to its canned result (see `mocks.py`). The tool itself never
runs. A tool without a fixture returns `{"ok": true}` and is listed in the report. When a ticket
adds a specialist or connector: give its tools fixtures, update the placeholder names in
`cases/pending.yaml`, drop `pending` from the cases it now handles and tag the stable ones `smoke`.
