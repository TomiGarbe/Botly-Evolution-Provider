"""
Cliente HTTP mínimo para llamar al gateway desde el bot.
No es una librería — es un snippet de referencia para copiar/adaptar.

Dependencias: httpx (ya en requirements.txt del gateway, o pip install httpx)

Variables de entorno esperadas:
  GATEWAY_URL     = http://localhost:9000
  GATEWAY_API_KEY = tu_clave
"""

import os
from typing import Any

import httpx

GATEWAY_URL = os.environ["GATEWAY_URL"].rstrip("/")
GATEWAY_API_KEY = os.environ["GATEWAY_API_KEY"]

_HEADERS = {
    "X-API-Key": GATEWAY_API_KEY,
    "Content-Type": "application/json",
}


# ── Helpers internos ──────────────────────────────────────────────────────────

def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=GATEWAY_URL, headers=_HEADERS, timeout=30.0)


async def _post(path: str, body: dict) -> Any:
    async with _client() as c:
        r = await c.post(path, json=body)
        r.raise_for_status()
        return r.json()


async def _get(path: str) -> Any:
    async with _client() as c:
        r = await c.get(path)
        r.raise_for_status()
        return r.json()


async def _delete(path: str) -> Any:
    async with _client() as c:
        r = await c.delete(path)
        r.raise_for_status()
        return r.json()


# ── API pública ───────────────────────────────────────────────────────────────

async def create_instance(instance_name: str, qrcode: bool = True) -> dict:
    return await _post("/instances/", {"instance_name": instance_name, "qrcode": qrcode})


async def get_qr(instance_name: str) -> dict:
    """Devuelve {'base64': 'data:image/png;base64,...', 'code': '2@...'}"""
    return await _get(f"/instances/{instance_name}/qr")


async def get_state(instance_name: str) -> str:
    """Devuelve 'open' | 'close' | 'connecting'"""
    data = await _get(f"/instances/{instance_name}/state")
    return data["instance"]["state"]


async def list_instances() -> list[dict]:
    return await _get("/instances/")


async def send_text(instance_name: str, number: str, text: str) -> dict:
    return await _post(
        f"/messages/{instance_name}",
        {"number": number, "type": "text", "text": text},
    )


async def send_image(instance_name: str, number: str, url: str, caption: str = "") -> dict:
    return await _post(
        f"/messages/{instance_name}",
        {"number": number, "type": "image", "mediaUrl": url, "caption": caption},
    )


async def send_buttons(
    instance_name: str,
    number: str,
    title: str,
    description: str,
    buttons: list[dict],  # [{"display_text": "...", "id": "..."}]
    footer: str = "",
) -> dict:
    return await _post(
        f"/instances/{instance_name}/messages/buttons",
        {
            "number": number,
            "title": title,
            "description": description,
            "footer": footer,
            "buttons": buttons,
        },
    )


async def check_numbers(instance_name: str, numbers: list[str]) -> list[dict]:
    """Retorna lista con {exists: bool, jid: str|None, number: str}"""
    return await _post(
        f"/instances/{instance_name}/messages/check-numbers",
        {"numbers": numbers},
    )


async def logout_instance(instance_name: str) -> dict:
    return await _delete(f"/instances/{instance_name}/logout")


async def delete_instance(instance_name: str) -> dict:
    return await _delete(f"/instances/{instance_name}")


# ── Ejemplo de uso ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio

    async def demo():
        # 1. Crear instancia
        result = await create_instance("demo_support")
        print("Instancia creada:", result["instance"]["instanceName"])

        # 2. Obtener QR
        qr = await get_qr("demo_support")
        print("QR listo — escanear con WhatsApp")
        # qr["base64"] → imagen PNG en base64, mostrar en frontend

        # 3. Esperar conexión (en producción esto viene por webhook CONNECTION_UPDATE)
        import time
        for _ in range(30):
            state = await get_state("demo_support")
            print(f"Estado: {state}")
            if state == "open":
                break
            await asyncio.sleep(3)

        # 4. Enviar mensaje
        r = await send_text("demo_support", "5491112345678", "Hola desde el bot!")
        print("Mensaje enviado:", r)

    asyncio.run(demo())
