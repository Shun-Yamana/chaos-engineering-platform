import os
import logging
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="service-b")

SERVICE_A_URL = os.getenv("SERVICE_A_URL", "http://service-a/")


@app.get("/health")
def health():
    return {"status": "ok", "service": "service-b"}


@app.get("/items/{item_id}")
async def get_item(item_id: int):
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
