#!/usr/bin/env bash
# Muestra el estado de los servicios y verifica que Evolution responda.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/docker/docker-compose.yml"
ENV_FILE="$REPO_ROOT/config/.env"

echo "── Estado de contenedores ─────────────────"
docker compose -p evolution -f "$COMPOSE_FILE" --env-file "$ENV_FILE" ps

echo ""
echo "── Health check Evolution API ─────────────"
PORT=$(grep -E '^EVOLUTION_PORT=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 || echo "8080")
API_KEY=$(grep -E '^EVOLUTION_API_KEY=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 || echo "")

if curl -sf -o /dev/null -w "%{http_code}" \
    -H "apikey: $API_KEY" \
    "http://localhost:$PORT/instance/fetchInstances" | grep -q "200"; then
  echo "✓ Evolution responde correctamente en el puerto $PORT"
else
  echo "✗ Evolution no responde en el puerto $PORT (puede estar iniciando)"
fi
