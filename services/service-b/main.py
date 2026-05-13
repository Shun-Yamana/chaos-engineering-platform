import os
import asyncio
import logging
import threading
import httpx
from fastapi import FastAPI, HTTPException

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="service-b")

SERVICE_A_URL = os.getenv("SERVICE_A_URL", "http://service-a/")


def _latency_ms() -> int:
    try:
        return int(os.getenv("LATENCY_MS", "0"))
    except ValueError:
        return 0


# --- CPU stress ---
if os.getenv("CPU_STRESS", "").lower() == "true":
    def _cpu_burner():
        logger.info("cpu-stress thread started")
        while True:
            _ = sum(i * i for i in range(50000))
    threading.Thread(target=_cpu_burner, daemon=True, name="cpu-stress").start()

# --- Memory stress ---
_memory_buffer: bytearray | None = None
_memory_stress_mb = int(os.getenv("MEMORY_STRESS_MB", "0") or "0")
if _memory_stress_mb > 0:
    _memory_buffer = bytearray(_memory_stress_mb * 1024 * 1024)
    logger.info(f"memory-stress: allocated {_memory_stress_mb}MB")


@app.get("/health")
def health():
    return {"status": "ok", "service": "service-b"}


@app.get("/items/{item_id}")
async def get_item(item_id: int):
    ms = _latency_ms()
    if ms > 0:
        await asyncio.sleep(ms / 1000)

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{SERVICE_A_URL}items/{item_id}")
            response.raise_for_status()
            data = response.json()
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="service-a timeout")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"service-a error: {e.response.status_code}")
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="service-a unreachable")

    return {"item_id": item_id, "upstream": data, "service": "service-b"}


@app.get("/")
def root():
    return {"service": "service-b", "status": "running"}
