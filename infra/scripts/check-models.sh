#!/usr/bin/env bash
# Smoke-test every configured model: list tags over HTTP, send a short prompt to each,
# then show what is loaded and how much VRAM it uses.
# Usage (from anywhere, stack running): infra/scripts/check-models.sh
set -euo pipefail

infra_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$infra_dir/scripts/lib.sh"

load_env "$infra_dir"
base_url="http://${OLLAMA_BIND_ADDRESS:-127.0.0.1}:${OLLAMA_PORT:-11434}"

echo "==> $base_url/api/tags"
curl -fsS "$base_url/api/tags" | grep -o '"name":"[^"]*"' | sed 's/"name"://; s/"//g'

for model in $(all_models); do
  # Every candidate must chat and call tools (some library models are classify-only).
  caps=$(curl -fsS "$base_url/api/show" -d "{\"model\":\"$model\"}" | grep -o '"capabilities":\[[^]]*\]' || true)
  echo "==> prompting $model $caps"
  if [[ "$caps" != *'"completion"'* || "$caps" != *'"tools"'* ]]; then
    echo "    ERROR: $model lacks completion or tools capability" >&2
    exit 1
  fi
  started=$(date +%s)
  reply=$(curl -fsS "$base_url/api/generate" \
    -d "{\"model\":\"$model\",\"prompt\":\"Reply with the single word: ready\",\"stream\":false,\"think\":false}")
  echo "    $(echo "$reply" | grep -o '"response":"[^"]*"' | head -1) ($(( $(date +%s) - started ))s)"
done

echo "==> loaded models (ollama ps)"
compose "$infra_dir" exec -T ollama ollama ps

if command -v nvidia-smi >/dev/null 2>&1; then
  echo "==> nvidia-smi"
  nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu --format=csv
fi
