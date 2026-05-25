#!/usr/bin/env bash
# DESTRUCTIVO: Baja todos los servicios y elimina los volúmenes de datos.
# Útil para resetear el entorno a cero en desarrollo.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/docker/docker-compose.yml"
ENV_FILE="$REPO_ROOT/config/.env"

echo "⚠ ADVERTENCIA: Esto eliminará TODOS los datos (DB, Redis, instancias de WhatsApp)."
read -r -p "¿Confirmar? (escribí 'si' para continuar): " confirm

if [[ "$confirm" != "si" ]]; then
  echo "Cancelado."
  exit 0
fi

echo "▼ Bajando servicios y borrando volúmenes..."
docker compose -p evolution -f "$COMPOSE_FILE" --env-file "$ENV_FILE" down -v
echo "✓ Entorno reseteado."
