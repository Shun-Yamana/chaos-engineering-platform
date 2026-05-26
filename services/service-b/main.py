import os
import json
import time
import random
import asyncio
import logging
import threading
import httpx
import psutil
from fastapi import FastAPI, HTTPException, Request

from aws_xray_sdk.core import xray_recorder, patch_all
from aws_xray_sdk.core.async_context import AsyncContext
from aws_xray_sdk.ext.fastapi import XRayMiddleware

xray_recorder.configure(
    context_missing="LOG_ERROR",
    context=AsyncContext(),
    daemon_address="xray-service:2000",
)
patch_all()


def _xray_headers() -> dict[str, str]:
    try:
        entity = xray_recorder.get_trace_entity()
        if entity:
            return {"X-Amzn-Trace-Id": f"Root={entity.trace_id};Parent={entity.id};Sampled=1"}
    except Exception:
        pass
    return {}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="service-b")
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

SERVICE_A_URL = os.getenv("SERVICE_A_URL", "http://service-a/")
SERVICE_C_INTERNAL_URL = os.getenv("SERVICE_C_INTERNAL_URL", "http://service-c:8000")

# service-c (レビュー要約) のタイムアウト: 非クリティカルなため短めに設定
_REVIEW_TIMEOUT_S = 0.1  # 100ms: 超過時は reviews=null でレスポンスを返す

# 共有 HTTP クライアント
_http_c: httpx.AsyncClient | None = None
_http_a: httpx.AsyncClient | None = None

PRODUCTS = {
    "p-001": {
        "product_id": "p-001",
        "name": "Pro Mechanical Keyboard",
        "price": 12980,
        "stock": 12,
        "rating": 4.6,
        "review_count": 1284,
        "review_summary": "打鍵感がよく、長時間作業でも疲れにくいというレビューが多いです。特にタイピング量の多い開発者やライターに支持されています。",
        "recommendation_reason": "開発者・ライター向けの高評価モデルです。",
        "updated_at": "2026-05-15T10:00:00Z",
    },
    "p-002": {
        "product_id": "p-002",
        "name": "Ergonomic Office Chair",
        "price": 49800,
        "stock": 5,
        "rating": 4.4,
        "review_count": 876,
        "review_summary": "腰への負担が大幅に軽減されたという声が多く、長時間のデスクワークに最適です。組み立ても簡単とのレビューが目立ちます。",
        "recommendation_reason": "長時間デスクワーク向けの人気モデルです。",
        "updated_at": "2026-05-15T10:00:00Z",
    },
    "p-003": {
        "product_id": "p-003",
        "name": "4K Ultra-Wide Monitor",
        "price": 68000,
        "stock": 3,
        "rating": 4.8,
        "review_count": 542,
        "review_summary": "発色が良く、広い作業領域で生産性が大幅に向上したというレビューが多いです。クリエイター用途にも十分な色域とのことです。",
        "recommendation_reason": "クリエイター・エンジニア向けの高解像度モデルです。",
        "updated_at": "2026-05-15T10:00:00Z",
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


def _emit_product_emf(response_ms: float, injected_ms: int, fault: bool):
    emf = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": "ChaosExperiment",
                "Dimensions": [["Service"], ["Service", "ExperimentId"]],
                "Metrics": [
                    {"Name": "ResponseTimeMs",     "Unit": "Milliseconds"},
                    {"Name": "InjectedLatencyMs",  "Unit": "Milliseconds"},
                    {"Name": "FaultInjectedCount", "Unit": "Count"},
                ],
            }],
        },
        "Service": "service-b",
        "ExperimentId": os.getenv("EXPERIMENT_ID", "none"),
        "ResponseTimeMs": round(response_ms, 2),
        "InjectedLatencyMs": injected_ms,
        "FaultInjectedCount": 1 if fault else 0,
    }
    print(json.dumps(emf), flush=True)


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
    # Touch every page to commit physical memory (Linux lazy allocation workaround)
    for _i in range(0, len(_memory_buffer), 4096):
        _memory_buffer[_i] = 1
    logger.info(f"memory-stress: allocated and touched {_memory_stress_mb}MB")

# --- EMF metrics emitter (stdout → Fargate Fluent Bit → CloudWatch Logs → metric extraction) ---
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
                        {"Name": "ProcessMemoryMB", "Unit": "Megabytes"},
                    ],
                }],
            },
            "Service": "service-b",
            "ProcessCpuPercent": round(cpu_pct, 2),
            "ProcessMemoryMB": round(mem_mb, 2),
        }
        print(json.dumps(emf), flush=True)
        time.sleep(max(_METRICS_INTERVAL - 1, 1))


threading.Thread(target=_emf_metrics_emitter, daemon=True, name="emf-metrics").start()


@app.on_event("startup")
async def _startup():
    global _http_c, _http_a
    _http_c = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=2.0, read=_REVIEW_TIMEOUT_S, write=2.0, pool=5.0)
    )
    _http_a = httpx.AsyncClient(timeout=3.0)


@app.on_event("shutdown")
async def _shutdown():
    for c in (_http_c, _http_a):
        if c:
            await c.aclose()


@app.get("/health")
def health():
    return {"status": "ok", "service": "service-b"}


@app.get("/items/{item_id}")
async def get_item(item_id: int):
    if random.random() < _fault_rate():
        raise HTTPException(status_code=500, detail="simulated fault")

    ms = _latency_ms()
    if ms > 0:
        await asyncio.sleep(ms / 1000)

    try:
        response = await _http_a.get(f"{SERVICE_A_URL}items/{item_id}")
        response.raise_for_status()
        data = response.json()
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="service-a timeout")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"service-a error: {e.response.status_code}")
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="service-a unreachable")

    return {"item_id": item_id, "upstream": data, "service": "service-b"}


@app.get("/data/{item_id}")
async def get_data(item_id: int):
    """service-a /aggregate から呼ばれる専用エンドポイント。service-a を再呼び出しせず循環を回避する。"""
    ms = _latency_ms()
    if ms > 0:
        await asyncio.sleep(ms / 1000)

    start_t = time.monotonic()
    result = {"item_id": item_id, "data": f"data-{item_id}", "service": "service-b"}
    duration_ms = (time.monotonic() - start_t) * 1000 + ms

    # EMF: service-b のレイテンシを CloudWatch に送る（Envoy upstream_rq_time の代替）
    emf = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": "ChaosExperiment",
                "Dimensions": [["Service"], ["Service", "ExperimentId"]],
                "Metrics": [{"Name": "ResponseTimeMs", "Unit": "Milliseconds"}],
            }],
        },
        "Service": "service-b",
        "ExperimentId": os.getenv("EXPERIMENT_ID", "none"),
        "ResponseTimeMs": round(duration_ms + ms, 2),
    }
    print(json.dumps(emf), flush=True)
    return result


async def _fetch_reviews(product_id: str) -> dict | None:
    """service-c からレビュー要約・レコメンドを取得する。失敗時は None を返す（非クリティカル）。"""
    try:
        resp = await _http_c.get(f"{SERVICE_C_INTERNAL_URL}/reviews/{product_id}", headers=_xray_headers())
        resp.raise_for_status()
        return {**resp.json(), "_source": "fresh"}
    except Exception:
        return None


@app.get("/products/{product_id}")
async def get_product(product_id: str):
    """商品詳細プロバイダ。service-c を呼び出してレビュー要約を付与して返す。
    service-c が応答しない場合は reviews=null で 200 を返す（部分劣化）。
    """
    t0 = time.monotonic()

    if random.random() < _fault_rate():
        _emit_product_emf(0.0, 0, fault=True)
        raise HTTPException(status_code=500, detail="simulated fault")

    ms = _latency_ms()
    if ms > 0:
        await asyncio.sleep(ms / 1000)

    product = PRODUCTS.get(product_id)
    if not product:
        raise HTTPException(status_code=404, detail=f"product {product_id} not found")

    # service-c (レビュー要約) を並行取得 — タイムアウト or 障害時は None
    reviews = await _fetch_reviews(product_id)

    response_ms = (time.monotonic() - t0) * 1000
    _emit_product_emf(response_ms, injected_ms=ms, fault=False)
    return {**product, "reviews": reviews}


@app.get("/")
def root():
    return {"service": "service-b", "status": "running"}
