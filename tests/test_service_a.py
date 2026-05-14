"""
service-a の /aggregate エンドポイントのロジックテスト。
respx で httpx をモックし、stale cache・retry・circuit_open 挙動を確認する。
"""
import pytest
import httpx
import respx
import sys
import os
import importlib.util

# service-a の module level コードが走る前に env を設定
os.environ.setdefault("SERVICE_B_INTERNAL_URL", "http://fake-service-b:9001")

ROOT = os.path.dirname(os.path.dirname(__file__))


def _load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


service_a = _load_module("service_a_main", os.path.join(ROOT, "services", "service-a", "main.py"))
service_b = _load_module("service_b_main", os.path.join(ROOT, "services", "service-b", "main.py"))

app = service_a.app
_stale_cache = service_a._stale_cache

from httpx import AsyncClient


@pytest.fixture(autouse=True)
def clear_stale_cache():
    """各テスト前にキャッシュをクリアする。"""
    _stale_cache.clear()
    yield
    _stale_cache.clear()


@pytest.mark.asyncio
async def test_aggregate_success():
    """service-b が正常応答 → データを返しキャッシュに保存する。"""
    with respx.mock:
        respx.get("http://fake-service-b:9001/data/1").mock(
            return_value=httpx.Response(200, json={"item_id": 1, "data": "data-1", "service": "service-b"})
        )
        async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/aggregate/1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["item_id"] == 1
        assert body["data"] == "data-1"
        assert "_stale" not in body
    # キャッシュに保存されているか確認
    assert 1 in _stale_cache, "stale cache should be populated after success"


@pytest.mark.asyncio
async def test_aggregate_stale_cache_fallback():
    """service-b がタイムアウト → stale cache から返す。"""
    # まずキャッシュを手動で投入
    from time import monotonic
    _stale_cache[1] = ({"item_id": 1, "data": "cached-data", "service": "service-b"}, monotonic())

    with respx.mock:
        respx.get("http://fake-service-b:9001/data/1").mock(
            side_effect=httpx.TimeoutException("timeout")
        )
        async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/aggregate/1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["_stale"] is True
        assert body["data"] == "cached-data"


@pytest.mark.asyncio
async def test_aggregate_no_cache_returns_502():
    """service-b がタイムアウト & キャッシュなし → 502。"""
    with respx.mock:
        respx.get("http://fake-service-b:9001/data/1").mock(
            side_effect=httpx.TimeoutException("timeout")
        )
        async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/aggregate/1")
        assert resp.status_code == 502


@pytest.mark.asyncio
async def test_aggregate_circuit_open_immediate_fallback():
    """Envoy が 503 を返す (upstream ejected) → 即座に stale cache フォールバック。"""
    from time import monotonic
    _stale_cache[1] = ({"item_id": 1, "data": "stale-data", "service": "service-b"}, monotonic())

    with respx.mock:
        respx.get("http://fake-service-b:9001/data/1").mock(
            return_value=httpx.Response(503, json={"detail": "upstream ejected"})
        )
        async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/aggregate/1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["_stale"] is True
        assert body["_circuit_open"] is True


@pytest.mark.asyncio
async def test_aggregate_retry_succeeds_on_second_attempt():
    """1回失敗 → 2回目成功 → _stale なしで返す。"""
    call_count = 0

    def side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.TimeoutException("timeout on first attempt")
        return httpx.Response(200, json={"item_id": 1, "data": "retry-success", "service": "service-b"})

    with respx.mock:
        respx.get("http://fake-service-b:9001/data/1").mock(side_effect=side_effect)
        async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/aggregate/1")
        assert resp.status_code == 200
        assert resp.json()["data"] == "retry-success"
        assert "_stale" not in resp.json()
    assert call_count == 2, f"Expected 2 attempts, got {call_count}"


@pytest.mark.asyncio
async def test_aggregate_stale_cache_ttl_expired():
    """TTL 切れのキャッシュは使わない → 502。"""
    from time import monotonic
    # TTL より古いタイムスタンプを手動設定 (31秒前)
    _stale_cache[1] = ({"item_id": 1, "data": "expired"}, monotonic() - 31.0)

    with respx.mock:
        respx.get("http://fake-service-b:9001/data/1").mock(
            side_effect=httpx.TimeoutException("timeout")
        )
        async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/aggregate/1")
        assert resp.status_code == 502, "Expired cache should not be used"


@pytest.mark.asyncio
async def test_service_b_data_endpoint():
    """service-b /data/{id} が service-a を呼ばないことを確認（循環回避）。"""
    # service-b の /data/{id} は SERVICE_A_URL を呼ばない
    # httpx モックなしで呼んでも 200 が返るはず
    service_b_app = service_b.app
    async with AsyncClient(transport=httpx.ASGITransport(app=service_b_app), base_url="http://test") as client:
        resp = await client.get("/data/42")
    assert resp.status_code == 200
    body = resp.json()
    assert body["item_id"] == 42
    assert body["service"] == "service-b"
    assert "data" in body


@pytest.mark.asyncio
async def test_items_endpoint_still_works():
    """既存の /items/{id} エンドポイントが壊れていないことを確認。"""
    async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/items/1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["item_id"] == 1
    assert body["service"] == "service-a"
