# 0003: Local models for the router and main agent

- **Status:** accepted
- **Date:** 2026-10-24
- **Ticket:** STU-48

## Context

Local models are the default for every request. The RTX 3090 has 24 GB VRAM, and has to fit a small, fast **router** model plus a larger **main** tool-calling model, with about 3–4 GB left for STT and TTS (STU-49, STU-50). Ollama serves them (`infra/docker-compose.yml`). Requirements:

- Native tool calling through Ollama's chat API (Agno and LiteLLM depend on it).
- Plain answers without reasoning text in the reply: latency matters for voice.
- Router and main resident together at a useful context (16K tokens, q8_0 KV cache, flash attention).

Candidates in the Ollama library at the time (Oct 2026) that fit 24 GB: `qwen3.8:27b` (18 GB), `nemotron-3.5-lightning:30b` (19–25 GB), `qwen3.6:27b` (17–19 GB), `muse-glimmer:30b` (18 GB), `gemma4:26b` (16–19 GB), `mistral-small3.2:24b` (15 GB), `gpt-oss:20b` (13 GB). For the router: `qwen3:4b`, `tev1:4b`, `nimble-v3:9b`, `granite4:3b`. Anything at 17 GB or more leaves no room for the router plus STT/TTS, so those were ruled out on size alone.

## Decision

| Role | Model (Ollama tag) | Disk | Resident in VRAM (16K ctx) |
| --- | --- | --- | --- |
| Router | `qwen3:4b-instruct-2507-q4_K_M` | 2.5 GB | 4.0 GB |
| Main (default) | `gpt-oss:20b` | 13 GB | 12.7 GB |
| Main (alternate candidate) | `mistral-small3.2:24b-instruct-2506-q4_K_M` | 15 GB | 15–16 GB |

Configured as `OLLAMA_MODELS_ROUTER` / `OLLAMA_MODELS_MAIN` in `infra/.env` (defaults in `infra/scripts/lib.sh`). The router ticket (STU-55) and evals (STU-60) make the final call between the two main candidates. This ADR records what fits and works.

### Measurements

RTX 3090, driver 610.88, Docker Desktop 29.8 (WSL 2), Ollama 0.40.10, `OLLAMA_CONTEXT_LENGTH=16384`, `OLLAMA_KV_CACHE_TYPE=q8_0`, flash attention on. VRAM is total `nvidia-smi` use. The Windows desktop itself uses about 1.1 GB on this box.

| Loaded | VRAM used | Free | Generation speed | Cold load |
| --- | --- | --- | --- | --- |
| nothing (desktop only) | 1.1 GB | 23.4 GB | – | – |
| router | 5.2 GB | 19.4 GB | ~160 tok/s (up to 270 on short replies) | ~2 s |
| router + `gpt-oss:20b` | **18.0 GB** | **6.6 GB** | gpt-oss ~120 tok/s | ~13 s |
| router + `mistral-small3.2` | 21.3 GB (when Ollama keeps both) | 3.3 GB | mistral ~48 tok/s | ~11–15 s |

All runs were `100% GPU` in `ollama ps`. All three models return a correct native `tool_calls` entry for a simple `light_on(room)` tool.

### Why these

- **`gpt-oss:20b`** is the default main model. It is the only strong tool-caller that leaves room for STT/TTS next to the router (6.6 GB free), and it is the fastest of the candidates (~120 tok/s, MoE with MXFP4 weights).
- **`mistral-small3.2:24b`** is kept as the alternate: a dense non-reasoning model tuned for function calling, and a useful comparison in evals. Next to the router it leaves only ~3.3 GB, and in one run Ollama evicted the router to load it. So it can't be the default while STT/TTS share the GPU, unless the context is cut.
- **`qwen3:4b-instruct-2507`** is the router: fast, native tool calls, and no reasoning text.

### Rejected along the way

- `qwen3:4b` (the default tag): the 2507 *thinking* build. Even with `think: false` it writes its reasoning into the reply ("Hmm, the user just asked me…"), which wastes 20 s per routing decision. Use the `-instruct-2507` tag.
- `tev1:4b`: listed as a tools model on ollama.com, but the local model only has the `decision` capability (classification API). `/api/generate` and `/api/chat` reject it, so LiteLLM can't use it. `infra/scripts/check-models.sh` now fails on any model without `completion` and `tools`.

### Eval comparison (STU-60, 2026-10-25)

`gpt-oss:20b` (`local-main`) against `mistral-small3.2:24b-instruct-2506-q4_K_M` (served as
`eval-candidate`) on the 16 runnable cases (`smoke`, the `general` echo agent; harness in
`0012-eval-harness.md`), router `qwen3:4b-instruct-2507` for both, two runs each. Latency is per
request (classifier + specialist, tool round trips included), after a warm-up call.

| Model | Passed (run 1 / run 2) | Tool checks | Median | p90 | Routing |
| --- | --- | --- | --- | --- | --- |
| `gpt-oss:20b` | 14/16 / 14/16 | 9/11 | 1.2 s | 2.5–3.4 s | ~370 ms |
| `mistral-small3.2:24b` | 13/16 / 14/16 | 10/11 | 0.8–1.0 s | 4.2–4.5 s | ~360 ms |

- Both picked the right specialist and called no forbidden tools on every case.
- `gpt-oss` once answered "what time is it here?" (New York context) without calling `get_time`
  and gave the wrong time; it passed the same case in the other run.
- `mistral` twice replied with the low-confidence marker to "What year did the Second World War
  end?" (said as "Sorry, I'm not sure about that"), and was unhelpful on a forced-local "ask
  Claude to write a poem" ("I don't have the capability to ask Claude…").
- Both models fail `refuse-impersonation` the same way: the *classifier* rates it `complex`, so
  the router would send a request it should refuse to cloud. That is a routing issue, not a
  main-model one.

**Result: keep `gpt-oss:20b` as `local-main`.** Accuracy is the same or slightly better, its
tail latency is lower, and it leaves ~3 GB more VRAM for STT/TTS. Mistral's lower median isn't
worth the VRAM. Only the `general` agent exists so far, so this is a small sample: rerun the
comparison once real specialists with several tools land (weather, Home Assistant, calendar).

## Consequences

- Router + main use ~17 GB, so STT + TTS must stay within ~6 GB (STU-49/STU-50 should check this against the numbers above).
- `OLLAMA_MAX_LOADED_MODELS=2` keeps router + main resident. Pulling a third model is fine, but loading it evicts one (Ollama's scheduler decides which).
- Raising `OLLAMA_CONTEXT_LENGTH` costs VRAM roughly linearly. Re-measure with `infra/scripts/check-models.sh` and update this table.
- gpt-oss emits reasoning in a separate `thinking` field (its `think` level can't be fully disabled). Callers must use the `content`/`tool_calls` fields only. LiteLLM (STU-52) should be checked for this.
- Revisit when new models land: rerun the pull and check scripts with the new tags and record the numbers here.
