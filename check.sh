#!/usr/bin/env bash
# Regarde ce qui tourne déjà sur cette machine et dit quoi lancer.
# Ne modifie rien, n'installe rien.

say()  { printf '%s\n' "$*"; }
ok()   { printf '  [ok]      %s\n' "$*"; }
no()   { printf '  [absent]  %s\n' "$*"; }
warn() { printf '  [voir]    %s\n' "$*"; }

PORTS="${FACTORY_SCAN_PORTS:-8000 8001 8080 8888 5000 1234 11434 30000 40000}"

# Le seul test qui compte : est-ce que quelque chose accepte une
# connexion. Pas de ss ni de netstat, ils manquent sur bien des images.
listening() {
  if command -v timeout >/dev/null 2>&1; then
    timeout 1 bash -c "exec 3<>/dev/tcp/127.0.0.1/$1" 2>/dev/null
  else
    bash -c "exec 3<>/dev/tcp/127.0.0.1/$1" 2>/dev/null
  fi
}

models_at() {   # rend la liste des modèles si le port parle le dialecte OpenAI
  curl -fsS --max-time 2 "http://127.0.0.1:$1/v1/models" 2>/dev/null \
    | python3 -c 'import sys,json
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(1)
ids = [m.get("id") for m in d.get("data", []) if m.get("id")]
print(",".join(ids)) if ids else sys.exit(1)' 2>/dev/null
}

say ""
say "=== SERVEUR D'INFÉRENCE ==============================="
LLM_URL=""; LLM_MODEL=""
for p in $PORTS; do
  listening "$p" || continue
  if ms=$(models_at "$p"); then
    ok "port $p — dialecte OpenAI, modèles : $ms"
    [ -z "$LLM_URL" ] && { LLM_URL="http://localhost:$p"; LLM_MODEL="${ms%%,*}"; }
  else
    warn "port $p — occupé, mais ne répond pas sur /v1/models"
  fi
done
[ -z "$LLM_URL" ] && no "aucun serveur OpenAI trouvé sur : $PORTS"

say ""
say "=== CLOUDFLARE ========================================"
if command -v cloudflared >/dev/null 2>&1; then
  ok "cloudflared $(cloudflared --version 2>/dev/null | head -1)"
  if pgrep -a cloudflared >/dev/null 2>&1; then
    ok "un tunnel tourne déjà :"
    pgrep -a cloudflared | sed 's/^/            /'
  else
    no "aucun tunnel en cours"
  fi
  for cfg in /etc/cloudflared/config.yml "$HOME/.cloudflared/config.yml"; do
    [ -f "$cfg" ] && { ok "config : $cfg"; grep -E 'hostname|service' "$cfg" 2>/dev/null | sed 's/^/            /'; }
  done
  command -v systemctl >/dev/null 2>&1 && \
    warn "service systemd : $(systemctl is-active cloudflared 2>/dev/null || echo 'non installé')"
else
  no "cloudflared n'est pas installé"
fi

say ""
say "=== DÉPENDANCES PYTHON ================================"
ok "python $(python3 -V 2>&1 | cut -d' ' -f2)"
for m in fastapi uvicorn httpx redis rq minio; do
  if v=$(python3 -c "import $m,sys; print(getattr($m,'__version__','présent'))" 2>/dev/null); then
    ok "$m $v"
  else
    [ "$m" = fastapi ] || [ "$m" = uvicorn ] || [ "$m" = httpx ] \
      && no "$m — requis" || no "$m — facultatif"
  fi
done

say ""
say "=== FILE ET STOCKAGE =================================="
listening 6379 && ok "Redis écoute sur 6379" || no "pas de Redis (file en mémoire)"
listening 9000 && ok "quelque chose écoute sur 9000 (MinIO ?)" || no "pas de MinIO (disque local)"

say ""
say "=== PORT DE LA FACTORY ================================"
PORT="${PORT:-8000}"
if listening "$PORT"; then
  warn "le port $PORT est déjà pris — lance avec PORT=8010 ./start.sh"
  PORT=8010
else
  ok "port $PORT libre"
fi

say ""
say "=== À LANCER =========================================="
if [ -n "$LLM_URL" ]; then
  say "  export FACTORY_LLM_URL=$LLM_URL"
  say "  export FACTORY_LLM_MODEL=$LLM_MODEL"
  [ "$PORT" != "8000" ] && say "  export PORT=$PORT"
  say "  ./start.sh"
  say ""
  say "  puis, dans un autre terminal :"
  say "  cloudflared tunnel --url http://localhost:$PORT"
else
  say "  Aucun serveur d'inférence détecté sur les ports usuels."
  say "  S'il tourne ailleurs, relance en le disant :"
  say "    FACTORY_SCAN_PORTS=\"8000 9999 12345\" ./check.sh"
  say "  S'il est sur une autre machine, pose directement :"
  say "    export FACTORY_LLM_URL=http://<machine>:<port>"
fi
say ""
