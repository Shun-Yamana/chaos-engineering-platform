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
from aws_xray_sdk.ext.fastapi import XRayMiddleware

patch_all()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="service-c")
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


REVIEWS = {
    "p-001": {
        "product_id": "p-001",
        "rating_distribution": {"5": 823, "4": 301, "3": 98, "2": 41, "1": 21},
        "top_keywords": ["打鍵感", "疲れにくい", "静音", "耐久性", "コスパ"],
        "recommendations": ["p-002", "p-003"],
        "recommendation_reason": "キーボードと合わせて購入されることが多い周辺機器です。",
    },
    "p-002": {
        "product_id": "p-002",
        "rating_distribution": {"5": 512, "4": 234, "3": 87, "2": 31, "1": 12},
        "top_keywords": ["腰痛改善", "長時間作業", "組み立て簡単", "クッション", "高さ調整"],
        "recommendations": ["p-001", "p-003"],
        "recommendation_reason": "デスクワーク環境を整えるためによく一緒に購入されます。",
    },
    "p-003": {
        "product_id": "p-003",
        "rating_distribution": {"5": 381, "4": 112, "3": 34, "2": 10, "1": 5},
        "top_keywords": ["発色", "広い画面", "解像度", "目が疲れない", "VESA対応"],
        "recommendations": ["p-001", "p-002"],
        "recommendation_reason": "広い作業領域に合わせた入力デバイスとしてよく購入されます。",
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


def _emit_review_emf(response_ms: float, fault: bool):
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
        "Service": "service-c",
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
            "Service": "service-c",
            "ProcessCpuPercent": round(cpu_pct, 2),
            "ProcessMemoryMB": round(mem_mb, 2),
        }
        print(json.dumps(emf), flush=True)
        time.sleep(max(_METRICS_INTERVAL - 1, 1))


threading.Thread(target=_emf_metrics_emitter, daemon=True, name="emf-metrics").start()


@app.get("/health")
def health():
    return {"status": "ok", "service": "service-c"}


@app.get("/reviews/{product_id}")
async def get_reviews(product_id: str):
    """service-b から呼ばれるレビュー要約・レコメンドエンドポイント。"""
    t0 = time.monotonic()

    if random.random() < _fault_rate():
        _emit_review_emf(0.0, fault=True)
        raise HTTPException(status_code=500, detail="simulated fault")

    ms = _latency_ms()
    if ms > 0:
        await asyncio.sleep(ms / 1000)

    reviews = REVIEWS.get(product_id)
    if not reviews:
        raise HTTPException(status_code=404, detail=f"reviews for {product_id} not found")

    response_ms = (time.monotonic() - t0) * 1000
    _emit_review_emf(response_ms, fault=False)
    return reviews


@app.get("/")
def root():
    return {"service": "service-c", "status": "running"}
