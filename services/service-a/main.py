import os
import json
import time
import random
import asyncio
import logging
import threading
from time import monotonic
import psutil
import httpx
from fastapi import FastAPI, HTTPException, Request

from fastapi.middleware.cors import CORSMiddleware
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

app = FastAPI(title="service-a")

_CORS_ORIGINS = [o for o in os.getenv("CORS_ORIGINS", "").split(",") if o]
if not _CORS_ORIGINS:
    raise RuntimeError("CORS_ORIGINS environment variable is required (e.g. http://localhost:5173)")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_methods=["GET"],
    allow_headers=["*"],
)
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

SERVICE_B_INTERNAL_URL = os.getenv("SERVICE_B_INTERNAL_URL", "http://service-b:8000")
SERVICE_D_INTERNAL_URL = os.getenv("SERVICE_D_INTERNAL_URL", "http://service-d:8000")
_AGGREGATE_TIMEOUT_S = 0.3   # 300ms: slightly above Envoy's 200ms timeout
_STALE_CACHE_TTL_S = 60.0   # 60s: covers 30s traffic interval with margin

# 共有 HTTP クライアント — コネクションプールで再利用し接続レイテンシを排除する
# connect timeout を read timeout より長く設定して初回接続遅延に対応
_http_b: httpx.AsyncClient | None = None
_http_d: httpx.AsyncClient | None = None
_http_aggregate: httpx.AsyncClient | None = None

# {item_id: (response_dict, timestamp)}
_stale_cache: dict[int, tuple[dict, float]] = {}

# ---------------------------------------------------------------------------
# Product aggregate — circuit breaker + stale cache (service-b)
# ---------------------------------------------------------------------------
_PRODUCT_TIMEOUT_S = 0.2   # 200ms: tight enough to show latency injection effect
_PRODUCT_CACHE_TTL_S = 30.0

# ---------------------------------------------------------------------------
# Inventory aggregate — circuit breaker (service-d, 並行呼び出し)
# ---------------------------------------------------------------------------
_INVENTORY_TIMEOUT_S = 0.15  # 150ms: 在庫情報は非クリティカルなため短めに設定


class _CircuitBreaker:
    _FAIL_THRESH = 5
    _HALF_OPEN_S = 30.0

    def __init__(self):
        self._lock = threading.Lock()
        self._state = "closed"
        self._failures = 0
        self._opened_at = 0.0

    def allow_request(self) -> bool:
        with self._lock:
            if self._state == "closed":
                return True
            if self._state == "open":
                if monotonic() - self._opened_at >= self._HALF_OPEN_S:
                    self._state = "half_open"
                    return True
                return False
            return True  # half_open: allow one probe

    def record_success(self):
        with self._lock:
            self._failures = 0
            self._state = "closed"

    def record_failure(self):
        with self._lock:
            self._failures += 1
            if self._failures >= self._FAIL_THRESH:
                if self._state != "open":
                    self._opened_at = monotonic()
                self._state = "open"

    @property
    def state(self) -> str:
        with self._lock:
            if self._state == "open" and monotonic() - self._opened_at >= self._HALF_OPEN_S:
                return "half_open"
            return self._state


_product_cb = _CircuitBreaker()
_product_cache: dict[str, tuple[dict, float]] = {}

_aggregate_cb = _CircuitBreaker()

_inventory_cb = _CircuitBreaker()
_inventory_cb._FAIL_THRESH = 3   # service-d は非クリティカル: 3回失敗で即 OPEN


def _fallback_product(product_id: str) -> dict:
    return {
        "product_id": product_id,
        "name": "商品情報を一時的に取得できません",
        "price": None,
        "stock": None,
        "rating": None,
        "review_count": None,
        "review_summary": "現在、商品詳細サービスが混雑しています。",
        "recommendation_reason": "しばらくしてから再度お試しください。",
        "updated_at": None,
    }


def _emit_product_aggregate_emf(
    a_ms: float,
    b_ms: float | None,
    d_ms: float | None,
    product_source: str,
    inventory_source: str,
    circuit_state: str,
):
    emf = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": "ChaosExperiment",
                "Dimensions": [["Service"], ["Service", "ExperimentId"]],
                "Metrics": [
                    {"Name": "AggregateDurationMs",       "Unit": "Milliseconds"},
                    {"Name": "ServiceBCallDurationMs",     "Unit": "Milliseconds"},
                    {"Name": "ServiceDCallDurationMs",     "Unit": "Milliseconds"},
                    {"Name": "FallbackCount",              "Unit": "Count"},
                    {"Name": "StaleCacheHitCount",         "Unit": "Count"},
                    {"Name": "InventoryUnavailableCount",  "Unit": "Count"},
                    {"Name": "CircuitBreakerState",        "Unit": "None"},
                ],
            }],
        },
        "Service": "service-a",
        "ExperimentId": os.getenv("EXPERIMENT_ID", "none"),
        "AggregateDurationMs": round(a_ms, 2),
        "ServiceBCallDurationMs": round(b_ms, 2) if b_ms is not None else 0,
        "ServiceDCallDurationMs": round(d_ms, 2) if d_ms is not None else 0,
        "FallbackCount": 1 if product_source == "fallback" else 0,
        "StaleCacheHitCount": 1 if product_source == "stale_cache" else 0,
        "InventoryUnavailableCount": 1 if inventory_source != "fresh" else 0,
        "CircuitBreakerState": 0 if circuit_state == "closed" else 1,
    }
    print(json.dumps(emf), flush=True)


def _fault_rate() -> float:
    try:
        return float(os.getenv("FAULT_RATE", "0.0"))
    except ValueError:
        return 0.0


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
    for _i in range(0, len(_memory_buffer), 4096):
        _memory_buffer[_i] = 1
    logger.info(f"memory-stress: allocated and touched {_memory_stress_mb}MB")

# --- EMF metrics emitter ---
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
            "Service": "service-a",
            "ProcessCpuPercent": round(cpu_pct, 2),
            "ProcessMemoryMB": round(mem_mb, 2),
        }
        print(json.dumps(emf), flush=True)
        time.sleep(max(_METRICS_INTERVAL - 1, 1))


threading.Thread(target=_emf_metrics_emitter, daemon=True, name="emf-metrics").start()


def _emit_aggregate_emf(duration_ms: float, fallback: bool, circuit_open: bool):
    emf = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": "ChaosExperiment",
                "Dimensions": [["Service"], ["Service", "ExperimentId"]],
                "Metrics": [
                    {"Name": "AggregateDurationMs", "Unit": "Milliseconds"},
                    {"Name": "FallbackCount", "Unit": "Count"},
                    {"Name": "CircuitBreakerState", "Unit": "None"},
                ],
            }],
        },
        "Service": "service-a",
        "ExperimentId": os.getenv("EXPERIMENT_ID", "none"),
        "AggregateDurationMs": round(duration_ms, 2),
        "FallbackCount": 1 if fallback else 0,
        "CircuitBreakerState": 1 if circuit_open else 0,
    }
    print(json.dumps(emf), flush=True)


@app.on_event("startup")
async def _startup():
    global _http_b, _http_d, _http_aggregate
    _http_b = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=2.0, read=_PRODUCT_TIMEOUT_S, write=2.0, pool=5.0)
    )
    _http_d = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=2.0, read=_INVENTORY_TIMEOUT_S, write=2.0, pool=5.0)
    )
    _http_aggregate = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=2.0, read=_AGGREGATE_TIMEOUT_S, write=2.0, pool=5.0)
    )


@app.on_event("shutdown")
async def _shutdown():
    for c in (_http_b, _http_d, _http_aggregate):
        if c:
            await c.aclose()


@app.get("/health")
def health():
    return {"status": "ok", "service": "service-a"}


@app.get("/items/{item_id}")
async def get_item(item_id: int):
    ms = _latency_ms()
    if ms > 0:
        await asyncio.sleep(ms / 1000)

    if random.random() < _fault_rate():
        raise HTTPException(status_code=500, detail="simulated fault")

    time.sleep(random.uniform(0.01, 0.05))
    return {"item_id": item_id, "name": f"item-{item_id}", "service": "service-a"}


@app.get("/aggregate/{item_id}")
async def aggregate(item_id: int):
    """
    service-b の /data/{item_id} を Envoy sidecar (localhost:9001) 経由で呼び出す。
    Envoy が 200ms でタイムアウト + outlier_detection でエジェクト。
    _aggregate_cb が5回失敗で open → stale cache を即返す（cpu_stress 対策）。
    """
    start = monotonic()
    circuit_state = _aggregate_cb.state

    if _aggregate_cb.allow_request():
        try:
            resp = await _http_aggregate.get(f"{SERVICE_B_INTERNAL_URL}/data/{item_id}", headers=_xray_headers())
            resp.raise_for_status()
            data = resp.json()
            _stale_cache[item_id] = (data, monotonic())
            _aggregate_cb.record_success()
            duration_ms = (monotonic() - start) * 1000
            _emit_aggregate_emf(duration_ms, fallback=False, circuit_open=False)
            return data
        except Exception:
            _aggregate_cb.record_failure()
            circuit_state = _aggregate_cb.state

    # CB open or live 失敗 → stale cache（fault 期間中はキャッシュが唯一の防衛線）
    cached = _stale_cache.get(item_id)
    if cached:
        duration_ms = (monotonic() - start) * 1000
        _emit_aggregate_emf(duration_ms, fallback=True, circuit_open=circuit_state != "closed")
        return {**cached[0], "_stale": True, "_circuit_open": circuit_state != "closed"}

    duration_ms = (monotonic() - start) * 1000
    _emit_aggregate_emf(duration_ms, fallback=False, circuit_open=circuit_state != "closed")
    raise HTTPException(status_code=502, detail="service-b unavailable and no stale cache")


async def _fetch_product_resilient(
    product_id: str,
) -> tuple[dict, str, str, float | None, float | None]:
    """service-b → /products/{product_id} を CB + stale cache + fallback で取得する。
    戻り値: (product, product_source, circuit_state, b_latency_ms, cache_age_s)
    """
    circuit_state = _product_cb.state
    b_latency_ms: float | None = None

    if _product_cb.allow_request():
        b_t0 = monotonic()
        try:
            resp = await _http_b.get(f"{SERVICE_B_INTERNAL_URL}/products/{product_id}", headers=_xray_headers())
            resp.raise_for_status()
            product = resp.json()
            b_latency_ms = (monotonic() - b_t0) * 1000
            _product_cb.record_success()
            circuit_state = _product_cb.state
            _product_cache[product_id] = (product, monotonic())
            return product, "fresh", circuit_state, b_latency_ms, None
        except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.RequestError):
            _product_cb.record_failure()
            circuit_state = _product_cb.state

    cached = _product_cache.get(product_id)
    if cached and (monotonic() - cached[1]) < _PRODUCT_CACHE_TTL_S:
        product, cached_at = cached
        age_s = monotonic() - cached_at
        return product, "stale_cache", circuit_state, None, age_s

    return _fallback_product(product_id), "fallback", circuit_state, None, None


async def _fetch_inventory(
    product_id: str,
) -> tuple[dict | None, str, float | None]:
    """service-d → /inventory/{product_id} を CB で取得する（非クリティカル）。
    戻り値: (inventory_or_None, source, d_latency_ms)
    CB が OPEN の場合は即座に (None, "circuit_open", None) を返す。
    """
    if not _inventory_cb.allow_request():
        return None, "circuit_open", None

    d_t0 = monotonic()
    try:
        resp = await _http_d.get(f"{SERVICE_D_INTERNAL_URL}/inventory/{product_id}", headers=_xray_headers())
        resp.raise_for_status()
        data = resp.json()
        d_latency_ms = (monotonic() - d_t0) * 1000
        _inventory_cb.record_success()
        return data, "fresh", d_latency_ms
    except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.RequestError):
        _inventory_cb.record_failure()
        return None, "fallback", None


@app.get("/aggregate/products/{product_id}")
async def aggregate_product(product_id: str):
    """service-b (商品詳細+レビュー) と service-d (在庫・価格) を並行呼び出しして集約する。
    トポロジー: a → { b → c, d }
      - b が失敗 → stale cache → fallback 商品名で 200 を返す
      - d が失敗 → inventory=null で 200 を返す（非クリティカル）
    """
    t0 = monotonic()

    (product, product_source, circuit_state, b_latency_ms, cache_age_s), \
    (inventory, inventory_source, d_latency_ms) = await asyncio.gather(
        _fetch_product_resilient(product_id),
        _fetch_inventory(product_id),
    )

    a_ms = (monotonic() - t0) * 1000
    _emit_product_aggregate_emf(
        a_ms, b_latency_ms, d_latency_ms,
        product_source, inventory_source, circuit_state,
    )

    return {
        "product": product,
        "inventory": inventory,
        "resilience": {
            "product_source": product_source,
            "inventory_source": inventory_source,
            "stale": product_source == "stale_cache",
            "fallback": product_source == "fallback",
            "inventory_available": inventory is not None,
            "cache_age_seconds": round(cache_age_s) if cache_age_s is not None else None,
            "service_a_latency_ms": round(a_ms),
            "service_b_latency_ms": round(b_latency_ms) if b_latency_ms is not None else None,
            "service_d_latency_ms": round(d_latency_ms) if d_latency_ms is not None else None,
            "circuit_state": circuit_state,
            "inventory_circuit_state": _inventory_cb.state,
        },
    }


@app.get("/")
def root():
    return {"service": "service-a", "status": "running"}
