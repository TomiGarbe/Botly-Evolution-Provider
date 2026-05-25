#!/usr/bin/env bash
# Levanta todos los servicios. Verifica que config/.env exista antes de arrancar.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_ROOT/config/.env"
COMPOSE_FILE="$REPO_ROOT/docker/docker-compose.yml"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: No se encontró config/.env"
  echo "  Copiá el ejemplo y completá los valores:"
  echo "  cp config/.env.example config/.env"
  exit 1
fi

echo "▶ Levantando servicios..."
docker compose -p evolution -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d "$@"
echo "✓ Servicios arriba. Evolution disponible en http://localhost:$(grep EVOLUTION_PORT "$ENV_FILE" | cut -d= -f2 || echo 8080)"
