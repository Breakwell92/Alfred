# Infrastructure

The home server runs Alfred's backing services with Docker Compose from `infra/`. So far:

| Service | Image | Host port (default) | Data volume | Notes |
| --- | --- | --- | --- | --- |
| `postgres` | `pgvector/pgvector:pg18` | `127.0.0.1:5432` | `alfred_postgres-data` | Agno sessions, memory, schedules, connector registry, logs. pgvector included. |
| `ollama` | `ollama/ollama:0.40.10` | `127.0.0.1:11434` | `alfred_ollama-models` | Local LLMs on the GPU. Models: [ADR 0003](decisions/0003-local-models.md). |
| `litellm` | `ghcr.io/berriai/litellm-database:v1.106.3` | `127.0.0.1:4000` | (`litellm` database in Postgres) | Model gateway for local and cloud LLMs, with spend logs. See [below](#litellm-model-gateway) and [ADR 0005](decisions/0005-model-gateway.md). |
| `litellm-db-init` | `pgvector/pgvector:pg18` | – | – | One-shot: creates the `litellm` database if missing, then exits. |

STT, TTS and the server join the stack in later tickets.

All ports bind to loopback by default. Postgres holds tokens and memory, the Ollama API has no authentication, and LiteLLM holds the cloud API keys, so never bind any of them to `0.0.0.0`. To reach Ollama from other tailnet devices, set `OLLAMA_BIND_ADDRESS` to this machine's Tailscale IP (`tailscale ip -4`). Nothing then listens on loopback, so set `ALFRED_OLLAMA_BASE_URL` to the same address and port (`infra/scripts/check-models.sh` follows `OLLAMA_BIND_ADDRESS` itself).

## Prerequisites

- **NVIDIA driver** recent enough for CUDA 13 (the 3090 box runs 610.x). Check with `nvidia-smi`.
- **Docker with GPU support:**
  - Linux: Docker Engine + Compose v2, plus [nvidia-container-toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) (`sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker`).
  - Windows: Docker Desktop on the WSL 2 backend. GPU passthrough comes with the Windows NVIDIA driver; nothing to install inside WSL.
  - Check: `docker run --rm --gpus all ubuntu nvidia-smi` should list the 3090.
- **Bash** for `infra/scripts/` (Git Bash on Windows).
- About 35 GB of disk for the default models.

## Start and stop

```bash
cp infra/.env.example infra/.env     # once; set POSTGRES_PASSWORD (and the same password in ALFRED_DATABASE_URL),
                                     # LITELLM_MASTER_KEY and LITELLM_SALT_KEY
cd infra
docker compose up -d                 # start; `docker compose ps` should show postgres, ollama, litellm (healthy)
docker compose logs -f ollama        # follow logs
docker compose down                  # stop (data volumes are kept)
docker compose down -v               # stop and DELETE the database and downloaded models
```

Then pull and check the models (from the repo root, stack running):

```bash
infra/scripts/pull-models.sh         # pulls OLLAMA_MODELS_ROUTER + OLLAMA_MODELS_MAIN
infra/scripts/check-models.sh        # lists /api/tags, prompts each model, shows `ollama ps` and nvidia-smi
```

### Checking the server can reach Postgres

With `ALFRED_DATABASE_URL` set in `server/.env`:

```bash
cd server
uv run python -m alfred_server.db                     # prints the Postgres version, exit 1 on failure
TEST_DATABASE_URL=postgresql+psycopg://alfred:<password>@127.0.0.1:5432/alfred uv run pytest tests/test_db.py
```

The smoke test in `tests/test_db.py` is skipped unless `TEST_DATABASE_URL` is set. CI sets it and runs against a Postgres service container.

## LiteLLM model gateway

Every LLM call, local or cloud, goes through the LiteLLM proxy on port 4000 (OpenAI-compatible API). Code asks for an **alias**, never a provider model. Why and which models: [ADR 0005](decisions/0005-model-gateway.md).

| Alias | Default target | Enabled when |
| --- | --- | --- |
| `local-router` | Ollama `qwen3:4b-instruct-2507-q4_K_M` | always |
| `local-main` | Ollama `gpt-oss:20b` | always |
| `cloud-claude` | Anthropic `claude-sonnet-5-5` | `ANTHROPIC_API_KEY` is set |
| `cloud-gpt` | OpenAI `gpt-6.1-sol` | `OPENAI_API_KEY` is set |
| `cloud-gemini` | Google `gemini-4-argon` | `GEMINI_API_KEY` is set |

Files: `infra/litellm/config.yaml` (aliases and settings) and `infra/litellm/render_config.py`, which runs at container start: it fills `${VAR:-default}` from the environment, drops cloud aliases whose key is empty, then starts the proxy. Its first log lines list the enabled and disabled aliases (`docker compose logs litellm | grep render_config`).

**Setup.** In `infra/.env` set `LITELLM_MASTER_KEY` (starts with `sk-`) and `LITELLM_SALT_KEY` (never change it once set), and in `server/.env` set `ALFRED_LITELLM_API_KEY` to the master key. `POSTGRES_PASSWORD` is put into a URL for LiteLLM, so keep it to URL-safe characters (letters, digits, `-`, `_`, `.`).

**API keys** for cloud providers go only in `infra/.env` (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`), never in `server/.env`, code or the config file. After changing them run `docker compose up -d litellm` (recreates the container and re-renders the config).

**Check it** (from `infra/`, with the master key in `$KEY`):

```bash
curl -s localhost:4000/v1/models -H "Authorization: Bearer $KEY"     # enabled aliases
curl -s localhost:4000/v1/chat/completions -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"local-main","messages":[{"role":"user","content":"Say hello."}],
       "metadata":{"tags":["agent:curl"],"spend_logs_metadata":{"agent":"curl","task_id":"t1"}}}'
docker compose exec postgres psql -U alfred -d litellm -c \
  "SELECT \"startTime\", model_group, spend, total_tokens, request_tags, metadata->'spend_logs_metadata' FROM \"LiteLLM_SpendLogs\" ORDER BY \"startTime\" DESC LIMIT 5"
```

Spend logs are written in batches, so a row can take up to a minute to appear. Prompts and replies are never stored. gpt-oss returns its reasoning in `reasoning_content`, separate from `content`.

**From the server:**

```python
from alfred_server.models.gateway import CallMetadata, gateway_model

model = gateway_model("local-main", metadata=CallMetadata(agent="main", task_id=task_id, trigger="voice"))
agent = Agent(model=model, tools=[...])
```

`CallMetadata` becomes the request tags `agent:<name>` and `trigger:<kind>` plus `spend_logs_metadata` with all three fields, so spend can be grouped by agent, task or trigger.

**Add or swap a provider:**

1. Add an entry to `model_list` in `infra/litellm/config.yaml` (the [LiteLLM provider docs](https://docs.litellm.ai/docs/providers) give the `model:` prefix), with `api_key: os.environ/<PROVIDER>_API_KEY` and `requires_env: [<PROVIDER>_API_KEY]`.
2. Pass the key through: add `<PROVIDER>_API_KEY: ${<PROVIDER>_API_KEY:-}` to the `litellm` service's `environment` in `docker-compose.yml`, and an empty `<PROVIDER>_API_KEY=` to `infra/.env.example`.
3. A new alias also goes in `ModelAlias` in `server/alfred_server/models/gateway.py` (`tests/test_litellm_config.py` checks they match). Pointing an existing alias at another model needs no code change: set its `LITELLM_*_MODEL` variable, e.g. `LITELLM_CLOUD_CLAUDE_MODEL=claude-opus-5-5`.
4. Run `docker compose up -d litellm` and check `/v1/models`.

Costs come from the price table shipped in the pinned image (`LITELLM_LOCAL_MODEL_COST_MAP=True`). A model missing from it is logged with spend 0: add `input_cost_per_token` / `output_cost_per_token` under its `litellm_params`.

## GPU and VRAM

- `nvidia-smi` shows total VRAM use; the Ollama runner appears as a process (on Windows/WSL it may show as "Insufficient permissions" or not at all, but the memory total still counts it).
- `docker compose exec ollama ollama ps` shows which models are loaded, their size and the CPU/GPU split. Anything other than `100% GPU` means the model spilled to system RAM and will be slow.
- `docker compose logs ollama | grep "inference compute"` confirms Ollama found the GPU at startup.
- Budget on the 3090 (24 GB): router + one main model resident, leaving about 6 GB for STT and TTS with `gpt-oss:20b` (about 3 GB with `mistral-small3.2`). Measured numbers are in [ADR 0003](decisions/0003-local-models.md).
- Ollama settings that affect VRAM (all in `infra/.env`): `OLLAMA_CONTEXT_LENGTH` (default 16384), `OLLAMA_KV_CACHE_TYPE` (`q8_0`), `OLLAMA_MAX_LOADED_MODELS` (2), `OLLAMA_KEEP_ALIVE` (30m). Flash attention is always on.

## Gotchas

- **Native Ollama on the same machine.** A desktop Ollama install also listens on 11434, and the container then fails to bind (or you talk to the wrong one). Either quit the desktop app (and stop it starting at login) or set `OLLAMA_PORT` (e.g. `11435`) and `ALFRED_OLLAMA_BASE_URL` to match. The native app may also hold VRAM.
- **Intercepted HTTPS** (antivirus web scanning such as AVG/Avast Web Shield, or a corporate proxy): `ollama pull` fails with `x509: certificate signed by unknown authority`. Put the intercepting root CA as PEM in `infra/certs/` (git-ignored) and recreate the container with `docker compose up -d ollama`. On Windows, export it with PowerShell:

  ```powershell
  $c = Get-ChildItem Cert:\LocalMachine\Root | Where-Object Subject -like '*Web/Mail Shield Root*'
  "-----BEGIN CERTIFICATE-----`n$([Convert]::ToBase64String($c.RawData, 'InsertLineBreaks'))`n-----END CERTIFICATE-----" | Set-Content -Encoding ascii infra\certs\local-ca.crt
  ```

  The same interception breaks `uv` (set `UV_SYSTEM_CERTS=1`, see `CLAUDE.md`).
- **LiteLLM on a new image tag** runs Prisma migrations against the `litellm` database at start (about a minute the first time), so `docker compose ps` shows it `starting` for a while. Its admin UI at `/ui` uses the master key.
- **LiteLLM and intercepted HTTPS.** LiteLLM ignores `infra/certs/` (it uses its own CA bundle, or `SSL_CERT_FILE`), so on a host with HTTPS interception cloud calls fail with certificate errors. Not handled yet.
- **Postgres 18 data layout.** The pg18 image stores data in `/var/lib/postgresql/18/docker`, so the volume is mounted at `/var/lib/postgresql`. A future major upgrade needs `pg_upgrade` or a dump/restore; don't just bump the tag.

## Environment variables

All documented in `infra/.env.example`. Compose reads `infra/.env`; the server reads `server/.env`.

| Variable | Used by | Purpose |
| --- | --- | --- |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | compose | Database credentials (password required). |
| `POSTGRES_BIND_ADDRESS`, `POSTGRES_PORT` | compose | Host interface/port for Postgres. |
| `OLLAMA_BIND_ADDRESS`, `OLLAMA_PORT` | compose, scripts | Host interface/port for Ollama. |
| `OLLAMA_IMAGE_TAG` | compose | Pinned Ollama version. |
| `OLLAMA_MODELS_ROUTER`, `OLLAMA_MODELS_MAIN` | scripts | Space-separated model tags to pull and check. |
| `OLLAMA_MAX_LOADED_MODELS`, `OLLAMA_KEEP_ALIVE`, `OLLAMA_CONTEXT_LENGTH`, `OLLAMA_KV_CACHE_TYPE` | compose | Ollama memory behaviour. |
| `ALFRED_DATABASE_URL` | server | SQLAlchemy URL (`postgresql+psycopg://…`). |
| `ALFRED_OLLAMA_BASE_URL` | server | Ollama API base URL; must match `OLLAMA_BIND_ADDRESS` and `OLLAMA_PORT`. |
| `LITELLM_BIND_ADDRESS`, `LITELLM_PORT` | compose | Host interface/port for the LiteLLM proxy. |
| `LITELLM_IMAGE_TAG` | compose | Pinned LiteLLM version. |
| `LITELLM_MASTER_KEY` | compose | Admin key for the proxy (required, `sk-…`). |
| `LITELLM_SALT_KEY` | compose | Encrypts credentials LiteLLM stores in its database (required, never change). |
| `LITELLM_DB` | compose | Database LiteLLM uses in the compose Postgres (default `litellm`). |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY` | compose (LiteLLM) | Cloud keys; empty disables the alias. |
| `LITELLM_LOCAL_ROUTER_MODEL`, `LITELLM_LOCAL_MAIN_MODEL`, `LITELLM_CLOUD_{CLAUDE,GPT,GEMINI}_MODEL` | compose (LiteLLM) | Optional: what each alias points at. |
| `ALFRED_LITELLM_BASE_URL` | server | LiteLLM proxy URL; must match `LITELLM_BIND_ADDRESS` and `LITELLM_PORT`. |
| `ALFRED_LITELLM_API_KEY` | server | Key the server sends to the proxy (the master key or a virtual key). |
| `ALFRED_API_TOKEN` | server | Shared bearer token the Android app sends; required on every route except `/health` (unset → 503). See `docs/decisions/0004-android-client-stack.md`. |
