#!/usr/bin/env bash
# Pull the local model candidates into the compose `ollama` service.
#
# Models come from OLLAMA_MODELS_ROUTER and OLLAMA_MODELS_MAIN (space-separated) in
# infra/.env, falling back to the defaults recorded in docs/decisions/0003-local-models.md.
# Usage (from anywhere): infra/scripts/pull-models.sh
set -euo pipefail

infra_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$infra_dir/scripts/lib.sh"

load_env "$infra_dir"

for model in $(all_models); do
  echo "==> pulling $model"
  compose "$infra_dir" exec -T ollama ollama pull "$model"
done

echo "==> installed models"
compose "$infra_dir" exec -T ollama ollama list
