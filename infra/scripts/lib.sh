# Shared helpers for infra/scripts. Source, don't run.

# Defaults; keep in sync with infra/.env.example and docs/decisions/0003-local-models.md.
DEFAULT_OLLAMA_MODELS_ROUTER="qwen3:4b-instruct-2507-q4_K_M"
DEFAULT_OLLAMA_MODELS_MAIN="gpt-oss:20b mistral-small3.2:24b-instruct-2506-q4_K_M"

# Export the variables from infra/.env (if present) without overriding the shell.
load_env() {
  local env_file="$1/.env"
  if [[ -f "$env_file" ]]; then
    local line key value
    while IFS= read -r line || [[ -n "$line" ]]; do
      line="${line%$'\r'}"
      [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
      key="${line%%=*}"
      [[ -n "${!key+x}" ]] && continue
      value="${line#*=}"
      # Strip one pair of surrounding quotes, as compose does.
      [[ "$value" =~ ^\"(.*)\"$ || "$value" =~ ^\'(.*)\'$ ]] && value="${BASH_REMATCH[1]}"
      export "$key=$value"
    done <"$env_file"
  fi
  OLLAMA_MODELS_ROUTER="${OLLAMA_MODELS_ROUTER:-$DEFAULT_OLLAMA_MODELS_ROUTER}"
  OLLAMA_MODELS_MAIN="${OLLAMA_MODELS_MAIN:-$DEFAULT_OLLAMA_MODELS_MAIN}"
}

all_models() {
  echo "$OLLAMA_MODELS_ROUTER $OLLAMA_MODELS_MAIN"
}

compose() {
  local dir="$1"
  shift
  docker compose --project-directory "$dir" -f "$dir/docker-compose.yml" "$@"
}
