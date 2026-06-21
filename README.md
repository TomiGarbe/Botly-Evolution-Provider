# Botly Evolution Provider

Gateway propio sobre Evolution API v2 para gestión multi-tenant de instancias WhatsApp.

**Stack:** Evolution API v2 · PostgreSQL 17 · Redis 7 · FastAPI · Docker Compose

## Estructura del proyecto

```
.
├── config/
│   └── .env.example      # Plantilla de variables de entorno
├── docker/
│   └── docker-compose.yml
├── docs/
│   ├── api-reference.md  # Endpoints de Evolution v2
│   ├── architecture.md   # Decisiones de diseño multi-tenant
│   ├── integration.md    # Cómo conectar el bot al gateway
│   └── webhooks.md       # Payloads y eventos
├── gateway/              # FastAPI gateway (Etapa 3)
└── scripts/
    ├── logs.sh           # Ver logs de un servicio
    ├── reset.sh          # Reset completo (destructivo)
    ├── start.sh          # Levantar servicios
    ├── status.sh         # Estado y health check
    └── stop.sh           # Detener servicios
```

## Setup rápido

```bash
# 1. Crear config/.env a partir del ejemplo
cp config/.env.example config/.env

# 2. Editar las variables obligatorias (ver abajo)
nano config/.env

# 3. Levantar
./scripts/start.sh

# 4. Verificar
./scripts/status.sh
```

La API queda disponible en `http://localhost:8080` (o el puerto que configuraste en `EVOLUTION_PORT`).

## Variables obligatorias

| Variable | Descripción |
|---|---|
| `EVOLUTION_API_KEY` | Clave global de Evolution API. Generá con `openssl rand -hex 32` |
| `POSTGRES_PASSWORD` | Contraseña de PostgreSQL. Generá con `openssl rand -hex 16` |
| `REDIS_PASSWORD` | Contraseña de Redis. Generá con `openssl rand -hex 16` |
| `GATEWAY_API_KEY` | Clave que usa el bot para autenticarse al gateway |
| `MAX_EVENT_AGE_SECONDS` | Edad máxima aceptable de eventos entrantes antes de descartarlos (`0` deshabilita, recomendado `600`) |

Las demás variables tienen valores por defecto funcionales para desarrollo local.

## Scripts disponibles

| Script | Descripción |
|---|---|
| `./scripts/start.sh` | Levanta todos los servicios en background |
| `./scripts/stop.sh` | Detiene los servicios (conserva datos) |
| `./scripts/status.sh` | Estado de contenedores + health check de Evolution |
| `./scripts/logs.sh [servicio]` | Logs en tiempo real. Ej: `./scripts/logs.sh evolution` |
| `./scripts/reset.sh` | **Destructivo.** Borra todos los volúmenes de datos |

## Documentación

- [Referencia de API](docs/api-reference.md)
- [Webhooks](docs/webhooks.md)
- [Arquitectura multi-tenant](docs/architecture.md)
- [Integración con el bot](docs/integration.md)

## Integraciones externas (UX en Instancias)

Cada instancia ahora muestra una seccion `Integration` con:
- `Webhook URL`
- `API Base URL`
- endpoints utiles (`messages`, `events`)
- estado de API key por instancia
- botones `Copy` y snippets rapidos (`curl` y `fetch`)

### Como se generan las URLs

Orden de prioridad para construir URLs visibles en frontend:
1. `Public Base URL` guardada en Settings.
2. `VITE_PUBLIC_BASE_URL` (si existe en build del frontend).
3. `Gateway URL` configurada en Settings.

Se normalizan quitando slash final para evitar rutas inconsistentes.

### PUBLIC_BASE_URL (backend)

Se agrega `PUBLIC_BASE_URL` en `config/.env` y en Docker Compose para dejar explicita la URL publica real del gateway.
Esta variable es de configuracion operativa para evitar usar `localhost` o hosts internos en integraciones.

### Uso desde bots externos

Para enviar mensajes desde un bot externo:
1. Copiar `API Base URL` y `Unified Messages endpoint` desde la card de la instancia.
2. Copiar `Bearer token` de la instancia (reveal + copy).
3. Enviar `Authorization: Bearer <token>` al endpoint recomendado.
