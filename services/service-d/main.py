import os
import json
import time
import random
import asyncio
import logging
import threading
import psutil
from fastapi import FastAPI, HTTPException, Request

from aws_xray_sdk.core import xray_recorder, patch_all
from aws_xray_sdk.core.async_context import AsyncContext
from aws_xray_sdk.ext.fastapi import XRayMiddleware

xray_recorder.configure(context_missing="LOG_ERROR", context=AsyncContext())
patch_all()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="service-d")
app.add_middleware(XRayMiddleware, recorder=xray_recorder)


@app.middleware("http")
async def _annotate_experiment_id(request: Request, call_next):
    exp_id = os.getenv("EXPERIMENT_ID", "")
    if exp_id:
        try:
            xray_recorder.put_annotation("experiment_id", exp_id)
        except Exception:
            pass
    return await call_next(request)


# リアルタイム在庫・価格データ（service-b の商品カタログとは独立して管理）
INVENTORY = {
    "p-001": {
        "product_id": "p-001",
        "stock": 12,
        "price": 12980,
        "sale_price": None,
        "available": True,
        "warehouse": "TYO-1",
        "restocked_at": "2026-05-20T09:00:00Z",
    },
    "p-002": {
        "product_id": "p-002",
        "stock": 5,
        "price": 49800,
        "sale_price": 44820,
        "available": True,
        "warehouse": "TYO-2",
        "restocked_at": "2026-05-18T14:00:00Z",
    },
    "p-003": {
        "product_id": "p-003",
        "stock": 3,
        "price": 68000,
        "sale_price": None,
        "available": True,
        "warehouse": "TYO-1",
        "restocked_at": "2026-05-19T11:00:00Z",
    },
}


def _latency_ms() -> int:
    try:
        return int(os.getenv("LATENCY_MS", "0"))
    except ValueError:
        return 0


def _fault_rate() -> float:
    try:
        return float(os.getenv("FAULT_RATE", "0.0"))
    except ValueError:
        return 0.0


def _emit_inventory_emf(response_ms: float, fault: bool):
    emf = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": "ChaosExperiment",
                "Dimensions": [["Service"], ["Service", "ExperimentId"]],
                "Metrics": [
                    {"Name": "ResponseTimeMs",     "Unit": "Milliseconds"},
                    {"Name": "FaultInjectedCount", "Unit": "Count"},
                ],
            }],
        },
        "Service": "service-d",
        "ExperimentId": os.getenv("EXPERIMENT_ID", "none"),
        "ResponseTimeMs": round(response_ms, 2),
        "FaultInjectedCount": 1 if fault else 0,
    }
    print(json.dumps(emf), flush=True)


_METRICS_INTERVAL = int(os.getenv("METRICS_INTERVAL", "30"))


def _emf_metrics_emitter():
    proc = psutil.Process()
    while True:
        cpu_pct = proc.cpu_percent(interval=1)
        mem_mb = proc.memory_info().rss / (1024 * 1024)
        emf = {
            "_aws": {
                "Timestamp": int(time.time() * 1000),
                "CloudWatchMetrics": [{
                    "Namespace": "ChaosExperiment",
                    "Dimensions": [["Service"]],
                    "Metrics": [
                        {"Name": "ProcessCpuPercent", "Unit": "Percent"},
                        {"Name": "ProcessMemoryMB",   "Unit": "Megabytes"},
                    ],
                }],
            },
            "Service": "service-d",
            "ProcessCpuPercent": round(cpu_pct, 2),
            "ProcessMemoryMB": round(mem_mb, 2),
        }
        print(json.dumps(emf), flush=True)
        time.sleep(max(_METRICS_INTERVAL - 1, 1))


threading.Thread(target=_emf_metrics_emitter, daemon=True, name="emf-metrics").start()


@app.get("/health")
def health():
    return {"status": "ok", "service": "service-d"}


@app.get("/inventory/{product_id}")
async def get_inventory(product_id: str):
    """service-a から並行呼び出しされるリアルタイム在庫・価格エンドポイント。"""
    t0 = time.monotonic()

    if random.random() < _fault_rate():
        _emit_inventory_emf(0.0, fault=True)
        raise HTTPException(status_code=500, detail="simulated fault")

    ms = _latency_ms()
    if ms > 0:
        await asyncio.sleep(ms / 1000)

    item = INVENTORY.get(product_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"inventory for {product_id} not found")

    response_ms = (time.monotonic() - t0) * 1000
    _emit_inventory_emf(response_ms, fault=False)
    return item


@app.get("/")
def root():
    return {"service": "service-d", "status": "running"}
