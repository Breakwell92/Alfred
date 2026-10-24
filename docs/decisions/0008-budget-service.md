# 0008: Budget service: monthly cloud cap, tiers and pre-call estimates

- **Status:** accepted
- **Date:** 2026-10-24
- **Ticket:** STU-54

## Context

Cloud LLMs are used only when enabled and while month-to-date spend is under a cap Dan sets. The router (STU-55) needs a single question to ask before escalating ("may this call use cloud alias X?"), and Dan needs to see what's been spent. LiteLLM (ADR 0005) already logs every call's cost and knows every alias's price, so the budget service shouldn't keep a second ledger.

## Decision

- **Package `alfred_server/budget/`.** The router calls `get_budget_service().decide(alias, prompt_tokens, expected_output_tokens, reason)`, or `estimate_cost` followed by `check(alias, estimate, reason)`, and gets back a `BudgetDecision`: `allowed`, `tier`, `code`, a human `message` and an `announce` flag.
- **Tiers.** fraction = month-to-date ÷ cap.

  | Tier | Fraction | Behaviour |
  | --- | --- | --- |
  | `free` | below `restricted` (0.7) | Escalate freely. |
  | `restricted` | from 0.7 up to `exhausted` (1.0) | Allowed only when `reason` is `hard_task` or `user_request`, not `auto`. |
  | `exhausted` | 1.0 and above | Denied. A cap of 0 is always exhausted. |

  Separately, any call whose estimate is more than what's left (cap − spend) is refused, whatever the tier.
- **Announce once.** The first exhausted check of each month sets a flag in `budget_months` and returns `announce=True` with a sentence to say ("I've used this month's £20.00 cloud budget, so I'll use local models until 1 November."). Later checks that month return `announce=False`.
- **Fail closed.** Each of the following denies the call, with its own `code`, rather than letting it through:

  | `code` | Cause |
  | --- | --- |
  | `cloud_disabled` | Cloud is turned off. |
  | `provider_disabled` | The alias's provider is turned off. |
  | `spend_unavailable` | The spend logs can't be read. |
  | `price_unavailable` | No price for the alias: the gateway isn't reachable or the alias isn't configured on it. |
  | `settings_unavailable` | The budget settings can't be read. |

  Local aliases are always allowed and need no database or network access.
- **Cloud is off by default.** The ticket says cloud models are used "only when enabled", so the seed settings are `cloud_enabled=false`, a £20 cap, thresholds 0.7 / 1.0 and all providers enabled. Provider preferences start as: Claude first for `multi_step_tools`, Gemini first for `long_document`, `cheapest` for `summary` (ordered by the current gateway price), and Claude, GPT, Gemini for `default`. Dan changes all of it with `PUT /settings/budget`, which replaces the whole document; the app UI comes in STU-94.
- **Settings in the server's Postgres.** The settings are one JSON row in `budget_settings`, with the per-month state in `budget_months`. Both tables are created on first use with SQLAlchemy `create_all`: there is no migration tool yet. Stored as a JSON document, the settings can gain fields without a schema change, and Pydantic validates them on every load.
- **Actual spend from LiteLLM's database.** The service reads `"LiteLLM_SpendLogs"` in the `litellm` database. It uses rows with `spend > 0` and reads `startTime` (naive UTC), `model_group` (the alias), `metadata.spend_logs_metadata` (agent / task_id / trigger), and `request_tags` (`agent:` / `trigger:`) as a fallback. Grouping is done in Python: a month's cloud rows are few. Month-to-date spend is cached for 60 s. We read the table rather than LiteLLM's `/spend/*` APIs because the table is stable within a pinned image, can be seeded in tests and is cheap to query. Checked against the v1.106.3 schema.
- **Prices from the gateway.** `GET /model/info` on the proxy gives each deployment's `input_cost_per_token` / `output_cost_per_token`: the same cost map LiteLLM bills with, so estimates agree with the spend logs. Prices are cached for an hour. If an alias has several deployments, the dearest is used. A deployment with no price is treated as unknown, never as free. The server never imports `litellm` or names a provider model.
- **GBP.** LiteLLM prices in USD. The service converts at a fixed `ALFRED_BUDGET_USD_TO_GBP` rate (default 0.80, set slightly above recent rates on purpose so the cap errs on the safe side) rather than fetching a live rate. `GET /spend/summary` reports the rate it used.
- **Calendar.** Months and days start at local midnight in `ALFRED_TIMEZONE` (Europe/London), so BST shifts the boundaries by an hour.

## Consequences

- The router (STU-55) must call `decide` before every cloud call and speak `message` when `announce` is set. The budget isn't enforced in the gateway.
- LiteLLM writes spend logs in batches, up to about a minute late, and month-to-date is cached for 60 s. Calls made in quick succession can therefore go slightly over the cap, by roughly a minute's worth of calls. Each one is still refused if its own estimate exceeds what's left. If this matters, the next step is to hold allowed estimates in memory until their rows land.
- If Dan changes `LITELLM_DB`, `ALFRED_LITELLM_DATABASE_URL` must be set to match.
- A new cloud alias needs an entry in `PROVIDER_ALIASES` (`budget/models.py`) as well as `ModelAlias`.
- `GET /spend/summary` covers cloud spend only. Local calls cost 0 and are filtered out.
