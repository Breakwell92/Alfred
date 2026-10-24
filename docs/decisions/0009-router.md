# 0009: Router: classification, model tiers and fallback

- **Status:** accepted
- **Date:** 2026-10-24
- **Ticket:** STU-55

## Context

Every request (voice, text, scheduled) needs two decisions before an agent runs: which specialist handles it, and which model it runs on. Local models are the default. A cloud alias is used only when cloud is enabled, the budget service (ADR 0008) allows it and the task needs it. Specialists arrive in later tickets, so the routing mechanism has to work with just one specialist and take new ones without changes. The router sits in front of every request, so its own overhead had to stay under 300 ms (median).

## Decision

- **Modules.** `alfred_server/agents/registry.py` holds the specialists. Each has a name, a one-line description (shown to the classifier) and an `AgentFactory(ctx, model, db) -> Agent`. Specialist tickets register theirs in `get_registry()`. An unknown name resolves to `general`, which until specialists exist is the STU-53 echo agent. `alfred_server/agents/router.py` has the `Router`. `/chat` and `/chat/stream` call `router.route(text, ctx)`, then `router.run(...)` or `router.stream(...)`.
- **Classification on `local-router`.** A plain Agno agent with no history and no db, at temperature 0, with structured output (strict JSON schema, which LiteLLM passes to Ollama as `format`). It returns `Classification`: `specialist`, `complexity` (`simple` / `standard` / `complex`), `needs_long_context`, `expected_tool_calls`, `explicit_cloud_request` and `preferred_provider`.
  - The model writes short keys (`agent`, `level`, `long`, `tools`, `cloud`, `provider`) on one line, mapped to those fields in code. Every output token costs about 6 ms on the 3090, and the long field names (and the model sometimes pretty-printing) took the median from 414 ms to 277 ms when removed.
  - If the classifier fails (model error or invalid JSON), the request gets the default classification (`general`, `standard`, one tool call), so a broken classifier degrades to `local-main` rather than failing the request.
  - A regex safety net can only switch `explicit_cloud_request` *on*, never off. It matches only the command form, at the start of the utterance: an optional "Alfred," or "please", then ask / use / have, then Claude, GPT, ChatGPT, Gemini or "the smart(er) model", followed by "to", "about", "for", punctuation or the end. Questions *about* the products ("How do I use ChatGPT to write a CV?", "Can I use Claude Code?") don't match, because they would otherwise skip the restricted tier and spend cloud budget.
- **Model selection**, in order:
  1. No escalation wanted → `local-router` for `simple` requests with at most 1 tool call, otherwise `local-main`.
  2. Escalation wanted, with the budget `reason` in brackets: an explicit request (`user_request`), `complex` (`hard_task`), more than 3 expected tool calls (`auto`, task type `multi_step_tools`), or long context (`auto`, task type `long_document`). The budget service's restricted tier refuses `auto`, which gives the ticket's rule that the restricted tier escalates only for `complex` or explicit requests.
  3. The candidates are `preferred_aliases(task_type)`, which is empty when cloud is off or the settings can't be read. An explicit request puts the named provider's alias first, if that provider is enabled. If it's turned off, the next preferred alias is used and the reply starts by saying so ("Gemini is turned off, so I asked Claude."). Each candidate goes to `decide(alias, prompt_tokens, 500, reason)`. A `price_unavailable` refusal (alias not configured on the proxy) tries the next candidate. Any other refusal (cloud off, tier, cap, spend unreadable) would apply to all of them, so it stops and the request stays local.
  - The prompt-token estimate is deliberately rough: 2,000 + characters ÷ 4, plus 1,500 per expected tool call, plus 30,000 for long context. The budget's own check that an estimate fits in what's left this month still applies.
- **What the user is told.** The once-a-month "I've used this month's cloud budget…" message (`decision.announce`) goes before the reply. An **explicit** cloud request that is refused always gets its reason ("Cloud models are turned off.", "That would cost about…"). The user asked for a cloud model, and answering locally without saying so would mislead. Automatic escalations that are refused are silent.
- **Fallback.** A local run is retried once on the first allowed cloud alias when the *model* failed:
  - it made two or more invalid tool calls: calls Agno couldn't start (unknown tool, unparseable or invalid arguments), or
  - its whole reply is the low-confidence marker `<<UNSURE>>`, which every agent is told to reply with when it can't answer reliably (`BASE_INSTRUCTIONS`).

  Agno records an invalid call only as a tool message with no `ToolExecution`, so invalid calls are counted from the run's own tool messages. Tools that ran and raised (Home Assistant offline, an expired token) don't count: a cloud model would hit the same error and cost money.

  **A run in which any tool executed successfully is never retried and never deleted.** Retrying would repeat its actions (a second email, a light toggled back), and the run is the action log's record of them.

  The retry asks the budget with `reason="hard_task"`: the local model has just failed, so the request has shown itself to be hard, and the restricted tier may use cloud for it. The failed run is deleted from storage first (`db.delete_run`), so the retry's history doesn't hold the question twice. If the delete fails or deletes nothing (Agno returns `False`, e.g. a sandbox turn still holds the run), a warning is logged and the retry goes ahead with the failed attempt in its history. If no cloud model is allowed, a bare marker becomes "Sorry, I'm not sure about that." Cloud runs are never retried.
- **Streaming.** Text that could still turn into the marker (`"<<UN"` …) is held back until it can't. A run can therefore be retried before anything reaches the user. Once any text has been sent, the run is never retried. The announcement is the first `token` event.
- **Recorded decision.** Each run carries `metadata.routing`, stored with the Agno run for the action log (STU-93). It holds:
  - `alias`, `tier` (`local` / `cloud`) and `reason` (e.g. `simple`, `complex`, `explicit_request`, `complex; budget refused`, `fallback from local-main`);
  - `budget_code` and `budget_tier`;
  - `specialist` and the full `classification`.

  LiteLLM calls are tagged `agent:router` for the classifier and `agent:<specialist>` for the run (ADR 0005), with `trigger:<device_type>`.

## Measurements

`scripts/bench_router.py` on GLaDOS (RTX 3090, `qwen3:4b-instruct-2507-q4_K_M` via LiteLLM v1.106.3 and Ollama, model warm, `gpt-oss:20b` also resident), 10 typical requests × 5 rounds, 2026-10-24:

| | Median | p90 | Max |
| --- | --- | --- | --- |
| Classifier call (the router's overhead) | **289 ms** | 306 ms | 317 ms |

Model selection adds a settings read (a few ms) when cloud is off. When escalation is wanted, it adds one `decide` per candidate, served from the 60 s spend cache and 1 h price cache. With cloud off, which is the default, simple requests also get *faster* overall, because they run on `local-router` instead of `local-main`.

The margin under 300 ms is small. If prompts or specialists grow, the levers are:
- dropping the `agent` key while only one specialist exists;
- a shorter prompt;
- a smaller router model.

## Consequences

- A specialist ticket adds a `Specialist(name, description, factory)` in `get_registry()`. Its description is what the classifier sees, so keep it short and distinct. Each added specialist lengthens the prompt slightly; re-run `bench_router.py`.
- The classifier sees only the current message, not the conversation. That keeps the specialist's history free of router turns. With several specialists, a follow-up like "and tomorrow?" may need history-aware routing (pass the last turn to the classifier).
- `RequestContext` carries the current time, so "what time is it" is answered on `local-router` without a tool call. `get_time` is still there for the agent to use.
- The fallback costs a second model call, and on `/chat` (not streamed) a second wait. It only happens when the local run has visibly failed.
- If a local model starts replying `<<UNSURE>>` often, cloud spend rises (within the budget). Check the `fallback from …` reasons in the run metadata, or the `agent:` tags in spend logs.
