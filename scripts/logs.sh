#!/usr/bin/env bash
# Muestra los logs de un servicio específico o de todos.
# Uso: ./scripts/logs.sh [servicio]
# Ejemplo: ./scripts/logs.sh evolution

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/docker/docker-compose.yml"
ENV_FILE="$REPO_ROOT/config/.env"
SERVICE="${1:-}"

docker compose -p evolution -f "$COMPOSE_FILE" --env-file "$ENV_FILE" logs -f --tail=100 $SERVICE
