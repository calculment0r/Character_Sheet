#!/usr/bin/env bash
# Démarre la Character Factory sur le DGX : l'API sert aussi la page,
# donc un seul port et un seul tunnel suffisent.
set -euo pipefail

cd "$(dirname "$0")"

PORT="${PORT:-8000}"

# Le serveur d'inférence local, en dialecte OpenAI (vLLM, llama.cpp…).
: "${FACTORY_LLM_URL:=http://localhost:8001}"
: "${FACTORY_LLM_MODEL:=local-model}"
export FACTORY_LLM_URL FACTORY_LLM_MODEL

# Laisser FACTORY_TOKENS vide pour un essai ; le poser dès que l'URL
# du tunnel circule ailleurs que sur ton écran.
export FACTORY_TOKENS="${FACTORY_TOKENS:-}"

if [ -z "$FACTORY_TOKENS" ]; then
  echo "ATTENTION : FACTORY_TOKENS est vide, l'API accepte tout le monde." >&2
fi

cd api
exec python3 -m uvicorn main:app --host 0.0.0.0 --port "$PORT"
