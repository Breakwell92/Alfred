# 0005: LiteLLM model gateway and model aliases

- **Status:** accepted
- **Date:** 2026-10-24
- **Ticket:** STU-52

## Context

Alfred uses local models by default and escalates to Claude, GPT or Gemini only when cloud is enabled, under budget and needed. The agent layer (Agno) must not depend on any one provider, every call's cost has to be recorded so the budget service (STU-54) can enforce a monthly cap, and providers must be swappable by config.

## Decision

- **One gateway.** All LLM calls go through a LiteLLM proxy (`ghcr.io/berriai/litellm-database`, pinned in compose) that speaks the OpenAI chat API. The server builds Agno models with `alfred_server.models.gateway.gateway_model(alias)`, an `OpenAILike` pointed at the proxy. Nothing in the server names a provider or a provider model.
- **Stable aliases.** Code uses only these names. What they point at lives in `infra/litellm/config.yaml` and can be overridden per alias from `infra/.env`.

  | Alias | Default target | Why |
  | --- | --- | --- |
  | `local-router` | `ollama_chat/qwen3:4b-instruct-2507-q4_K_M` | ADR 0003. |
  | `local-main` | `ollama_chat/gpt-oss:20b` | ADR 0003. |
  | `cloud-claude` | `anthropic/claude-sonnet-5-5` | Current Sonnet: strong tool use at $2 / $10 per M tokens (in/out). |
  | `cloud-gpt` | `openai/gpt-6.1-sol` | Newest GPT at the same $2 / $10 price point. |
  | `cloud-gemini` | `gemini/gemini-4-argon` | Newest Gemini at $2 / $10. |

  The three cloud defaults sit at the same price tier on purpose, so escalation can pick on quality rather than cost. Prices are from the cost map shipped in LiteLLM v1.106.3, which the proxy uses for spend (`LITELLM_LOCAL_MODEL_COST_MAP=True`). The cheaper or bigger siblings (Haiku 5.5, Opus 5.5, `gpt-6-luna`, `gpt-6-astra`, Gemini flash models) are one env var away.
- **Cloud aliases exist only when their key does.** LiteLLM's config can't make an entry conditional, so the container starts through `infra/litellm/render_config.py`, which drops entries whose `requires_env` variables are empty and expands `${VAR:-default}`. Keys stay as `os.environ/…` references, so the rendered config holds no secrets. Calling a disabled alias gets a 400 (`Invalid model name`).
- **Spend in Postgres.** LiteLLM writes spend logs (tokens, cost, tags, metadata) to its own `litellm` database in the compose Postgres, created by the one-shot `litellm-db-init` service. Prompts and replies are not stored (`store_prompts_in_spend_logs: false`, `turn_off_message_logging: true`). Local aliases are priced at 0.
- **Call metadata.** `CallMetadata(agent, task_id, trigger)` goes in the request's `metadata`: `agent:<name>` and `trigger:<kind>` as LiteLLM request tags (for per-tag spend reports), and all three in `spend_logs_metadata` (stored with each spend log row). `task_id` is not a tag because tags are meant to be low-cardinality.
- **Auth.** The proxy requires `LITELLM_MASTER_KEY`; the server sends `ALFRED_LITELLM_API_KEY` (the master key for now; a per-service virtual key later). The port binds to loopback by default, like Postgres and Ollama.

## Consequences

- Adding or swapping a provider is a config change (`docs/infra.md#litellm-model-gateway`). Adding a new *alias* also needs `ModelAlias` in `gateway.py` updated; `tests/test_litellm_config.py` fails if the two drift.
- The budget service (STU-54) can read actual spend from `LiteLLM_SpendLogs` (or LiteLLM's `/spend/*` APIs) and estimate costs from the same price table. Enforcement is not in the gateway.
- gpt-oss's reasoning comes back in `reasoning_content`, separate from `content` and `tool_calls` (checked through the proxy), which settles the open question in ADR 0003. Agno keeps it out of the reply.
- Ollama calls also pass through the proxy: a few ms of overhead per call, in exchange for one place for logging and retries.
- LiteLLM releases often and the image is large (about 2 GB). Bump `LITELLM_IMAGE_TAG` deliberately: it runs Prisma migrations on the `litellm` database at start.
- LiteLLM trusts only its own CA bundle (or `SSL_CERT_FILE`), not the system store. `render_config.py` therefore appends the certs in `infra/certs/` to certifi's bundle and sets `SSL_CERT_FILE` (STU-107). The home server needs this because AVG Web Shield intercepts its HTTPS.
