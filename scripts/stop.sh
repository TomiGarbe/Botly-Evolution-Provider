#!/usr/bin/env bash
# Detiene todos los servicios sin borrar volúmenes.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/docker/docker-compose.yml"
ENV_FILE="$REPO_ROOT/config/.env"

echo "■ Deteniendo servicios..."
docker compose -p evolution -f "$COMPOSE_FILE" --env-file "$ENV_FILE" down "$@"
echo "✓ Servicios detenidos. Los volúmenes de datos se mantienen."
