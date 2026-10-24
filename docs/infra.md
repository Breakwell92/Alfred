# Infrastructure

The home server runs Alfred's backing services with Docker Compose from `infra/`. So far:

| Service | Image | Host port (default) | Data volume | Notes |
| --- | --- | --- | --- | --- |
| `postgres` | `pgvector/pgvector:pg18` | `127.0.0.1:5432` | `alfred_postgres-data` | Agno sessions, memory, schedules, connector registry, logs. pgvector included. |
| `ollama` | `ollama/ollama:0.40.10` | `127.0.0.1:11434` | `alfred_ollama-models` | Local LLMs on the GPU. Models: [ADR 0003](decisions/0003-local-models.md). |

LiteLLM, STT, TTS and the server join the stack in later tickets.

Both ports bind to loopback by default. Postgres holds tokens and memory, and the Ollama API has no authentication, so never bind either to `0.0.0.0`. To reach Ollama from other tailnet devices, set `OLLAMA_BIND_ADDRESS` to this machine's Tailscale IP (`tailscale ip -4`).

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
cp infra/.env.example infra/.env     # once; set POSTGRES_PASSWORD (and the same password in ALFRED_DATABASE_URL)
cd infra
docker compose up -d                 # start; `docker compose ps` should show both (healthy)
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

## GPU and VRAM

- `nvidia-smi` shows total VRAM use; the Ollama runner appears as a process (on Windows/WSL it may show as "Insufficient permissions" or not at all, but the memory total still counts it).
- `docker compose exec ollama ollama ps` shows which models are loaded, their size and the CPU/GPU split. Anything other than `100% GPU` means the model spilled to system RAM and will be slow.
- `docker compose logs ollama | grep "inference compute"` confirms Ollama found the GPU at startup.
- Budget on the 3090 (24 GB): router + one main model resident, leaving about 4 GB for STT and TTS. Measured numbers are in [ADR 0003](decisions/0003-local-models.md).
- Ollama settings that affect VRAM (all in `infra/.env`): `OLLAMA_CONTEXT_LENGTH` (default 16384), `OLLAMA_KV_CACHE_TYPE` (`q8_0`), `OLLAMA_MAX_LOADED_MODELS` (2), `OLLAMA_KEEP_ALIVE` (30m). Flash attention is always on.

## Gotchas

- **Native Ollama on the same machine.** A desktop Ollama install also listens on 11434, and the container then fails to bind (or you talk to the wrong one). Either quit the desktop app (and stop it starting at login) or set `OLLAMA_PORT` (e.g. `11435`) and `ALFRED_OLLAMA_BASE_URL` to match. The native app may also hold VRAM.
- **Intercepted HTTPS** (antivirus web scanning such as AVG/Avast Web Shield, or a corporate proxy): `ollama pull` fails with `x509: certificate signed by unknown authority`. Put the intercepting root CA as PEM in `infra/certs/` (git-ignored) and recreate the container with `docker compose up -d ollama`. On Windows, export it with PowerShell:

  ```powershell
  $c = Get-ChildItem Cert:\LocalMachine\Root | Where-Object Subject -like '*Web/Mail Shield Root*'
  "-----BEGIN CERTIFICATE-----`n$([Convert]::ToBase64String($c.RawData, 'InsertLineBreaks'))`n-----END CERTIFICATE-----" | Set-Content -Encoding ascii infra\certs\local-ca.crt
  ```

  The same interception breaks `uv` (set `UV_SYSTEM_CERTS=1`, see `CLAUDE.md`).
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
| `ALFRED_OLLAMA_BASE_URL` | server | Ollama API base URL. |
