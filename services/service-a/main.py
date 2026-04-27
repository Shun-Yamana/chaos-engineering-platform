import os
import time
import random
import logging
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="service-a")

FAULT_RATE = float(os.getenv("FAULT_RATE", "0.0"))


@app.get("/health")
def health():
    return {"status": "ok", "service": "service-a"}


@app.get("/items/{item_id}")
def get_item(item_id: int):
    if random.random() < FAULT_RATE:
        raise HTTPException(status_code=500, detail="simulated fault")

    time.sleep(random.uniform(0.01, 0.05))
    return {"item_id": item_id, "name": f"item-{item_id}", "service": "service-a"}


@app.get("/")
def root():
    return {"service": "service-a", "status": "running"}
