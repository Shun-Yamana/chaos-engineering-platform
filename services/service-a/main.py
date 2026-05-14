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
from fastapi import FastAPI, HTTPException

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="service-a")

SERVICE_B_INTERNAL_URL = os.getenv("SERVICE_B_INTERNAL_URL", "http://service-b:8000")
_AGGREGATE_TIMEOUT_S = 0.3   # 300ms: slightly above Envoy's 200ms timeout
_STALE_CACHE_TTL_S = 30.0

# {item_id: (response_dict, timestamp)}
_stale_cache: dict[int, tuple[dict, float]] = {}

# ---------------------------------------------------------------------------
# Product aggregate — circuit breaker + stale cache
# ---------------------------------------------------------------------------
_PRODUCT_TIMEOUT_S = 0.2   # 200ms: tight enough to show latency injection effect
_PRODUCT_CACHE_TTL_S = 30.0


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
    source: str,
    circuit_state: str,
):
    emf = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": "ChaosExperiment",
                "Dimensions": [["Service"]],
                "Metrics": [
                    {"Name": "AggregateDurationMs",   "Unit": "Milliseconds"},
                    {"Name": "ServiceBCallDurationMs", "Unit": "Milliseconds"},
                    {"Name": "FallbackCount",          "Unit": "Count"},
                    {"Name": "StaleCacheHitCount",     "Unit": "Count"},
                    {"Name": "CircuitBreakerState",    "Unit": "None"},
                ],
            }],
        },
        "Service": "service-a",
        "AggregateDurationMs": round(a_ms, 2),
        "ServiceBCallDurationMs": round(b_ms, 2) if b_ms is not None else 0,
        "FallbackCount": 1 if source == "fallback" else 0,
        "StaleCacheHitCount": 1 if source == "stale_cache" else 0,
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
                "Dimensions": [["Service"]],
                "Metrics": [
                    {"Name": "AggregateDurationMs", "Unit": "Milliseconds"},
                    {"Name": "FallbackCount", "Unit": "Count"},
                    {"Name": "CircuitBreakerState", "Unit": "None"},
                ],
            }],
        },
        "Service": "service-a",
        "AggregateDurationMs": round(duration_ms, 2),
        "FallbackCount": 1 if fallback else 0,
        "CircuitBreakerState": 1 if circuit_open else 0,
    }
    print(json.dumps(emf), flush=True)


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
    Envoy が 200ms でタイムアウト + outlier detection でエジェクト。
    失敗時は stale cache（TTL=30s）にフォールバック。
    最大 2 リトライ（指数バックオフ + jitter）。
    """
    start = monotonic()
    circuit_open = False

    for attempt in range(3):  # attempt 0, 1, 2 (= 1 initial + 2 retries)
        if attempt > 0:
            # 指数バックオフ + jitter: 50ms, 100ms + ±20ms
            delay = 0.05 * (2 ** (attempt - 1)) + random.uniform(0, 0.02)
            await asyncio.sleep(delay)

        try:
            async with httpx.AsyncClient(timeout=_AGGREGATE_TIMEOUT_S) as client:
                resp = await client.get(
                    f"{SERVICE_B_INTERNAL_URL}/data/{item_id}"
                )
                resp.raise_for_status()
                data = resp.json()

            # Success: update stale cache
            _stale_cache[item_id] = (data, monotonic())
            duration_ms = (monotonic() - start) * 1000
            _emit_aggregate_emf(duration_ms, fallback=False, circuit_open=False)
            return data

        except httpx.TimeoutException:
            logger.debug(f"[aggregate/{item_id}] attempt {attempt}: timeout")

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 503:
                # Envoy が upstream を eject 済み (outlier detection 発動)
                circuit_open = True
                logger.debug(f"[aggregate/{item_id}] Envoy upstream ejected (circuit open)")
                break  # リトライ不要: Envoy が eject している間は全試行が 503
            logger.debug(f"[aggregate/{item_id}] attempt {attempt}: HTTP {e.response.status_code}")

        except httpx.RequestError as e:
            logger.debug(f"[aggregate/{item_id}] attempt {attempt}: request error: {e}")

    # リトライ全滅 → stale cache フォールバック
    cached = _stale_cache.get(item_id)
    if cached and (monotonic() - cached[1]) < _STALE_CACHE_TTL_S:
        duration_ms = (monotonic() - start) * 1000
        _emit_aggregate_emf(duration_ms, fallback=True, circuit_open=circuit_open)
        return {**cached[0], "_stale": True, "_circuit_open": circuit_open}

    duration_ms = (monotonic() - start) * 1000
    _emit_aggregate_emf(duration_ms, fallback=False, circuit_open=circuit_open)
    raise HTTPException(status_code=502, detail="service-b unavailable and no stale cache")


@app.get("/aggregate/products/{product_id}")
async def aggregate_product(product_id: str):
    """service-b の /products/{product_id} を呼び出し、resilience metadata を付与して返す。"""
    t0 = monotonic()
    circuit_state = _product_cb.state
    b_latency_ms: float | None = None

    if _product_cb.allow_request():
        b_t0 = monotonic()
        try:
            async with httpx.AsyncClient(timeout=_PRODUCT_TIMEOUT_S) as client:
                resp = await client.get(f"{SERVICE_B_INTERNAL_URL}/products/{product_id}")
                resp.raise_for_status()
                product = resp.json()

            b_latency_ms = (monotonic() - b_t0) * 1000
            _product_cb.record_success()
            circuit_state = _product_cb.state
            _product_cache[product_id] = (product, monotonic())

            a_ms = (monotonic() - t0) * 1000
            _emit_product_aggregate_emf(a_ms, b_latency_ms, "fresh", circuit_state)
            return {
                "product": product,
                "resilience": {
                    "source": "fresh",
                    "stale": False,
                    "fallback": False,
                    "cache_age_seconds": 0,
                    "service_a_latency_ms": round(a_ms),
                    "service_b_latency_ms": round(b_latency_ms),
                    "circuit_state": circuit_state,
                },
            }

        except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.RequestError):
            _product_cb.record_failure()
            circuit_state = _product_cb.state

    # stale cache
    cached = _product_cache.get(product_id)
    if cached and (monotonic() - cached[1]) < _PRODUCT_CACHE_TTL_S:
        product, cached_at = cached
        a_ms = (monotonic() - t0) * 1000
        _emit_product_aggregate_emf(a_ms, None, "stale_cache", circuit_state)
        return {
            "product": product,
            "resilience": {
                "source": "stale_cache",
                "stale": True,
                "fallback": False,
                "cache_age_seconds": round(monotonic() - cached_at),
                "service_a_latency_ms": round(a_ms),
                "service_b_latency_ms": None,
                "circuit_state": circuit_state,
            },
        }

    # fallback
    a_ms = (monotonic() - t0) * 1000
    _emit_product_aggregate_emf(a_ms, None, "fallback", circuit_state)
    return {
        "product": _fallback_product(product_id),
        "resilience": {
            "source": "fallback",
            "stale": False,
            "fallback": True,
            "cache_age_seconds": None,
            "service_a_latency_ms": round(a_ms),
            "service_b_latency_ms": None,
            "circuit_state": circuit_state,
        },
    }


@app.get("/")
def root():
    return {"service": "service-a", "status": "running"}
