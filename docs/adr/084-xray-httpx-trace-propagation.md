# ADR 084 - X-Ray httpx トレースヘッダー伝播によるサービスマップ完成

- Status: Accepted
- Date: 2026-05-26

## Context

ADR 052 で X-Ray SDK を service-a/b に実装したが、2 つの未解決問題があった。

### 問題 1 — AsyncContext 未設定（service-a/b のみ）

service-c / service-d は `AsyncContext` + `XRayMiddleware` で正しく実装されている。
一方 service-a / service-b は `context_missing="LOG_ERROR"` のみで `AsyncContext` も `XRayMiddleware` も未設定。

FastAPI は asyncio ベースの ASGI アプリであり、デフォルトの `ThreadLocalContext` では
並行リクエスト間でセグメントが混在する。また `XRayMiddleware` がないため
リクエストごとのセグメントが作成されず、`put_annotation` が常に空振りしていた。

### 問題 2 — httpx が `patch_all()` 対象外

`patch_all()` は boto3/botocore/requests 等を自動計装するが httpx は対象外。
service-a → service-b、service-a → service-d、service-b → service-c への
httpx 呼び出しに `X-Amzn-Trace-Id` ヘッダーが付かない。

結果: X-Ray コンソールのサービスマップで各サービスが孤立したノードとして表示され、
「分散トレーシング」として機能していなかった。

## Decision

**service-a / service-b を service-c / service-d と同じ実装パターンに統一し、
httpx 呼び出しにトレースヘッダーを手動注入する。**

### 変更内容

1. `AsyncContext` を追加して async リクエスト間のコンテキスト分離を保証
2. `XRayMiddleware` をアプリに追加して各リクエストのセグメントを自動管理
3. `_xray_headers()` ヘルパーで httpx 呼び出し時にトレースヘッダーを注入

```python
def _xray_headers() -> dict[str, str]:
    try:
        entity = xray_recorder.get_trace_entity()
        if entity:
            return {"X-Amzn-Trace-Id": f"Root={entity.trace_id};Parent={entity.id};Sampled=1"}
    except Exception:
        pass
    return {}
```

注入箇所（service-a）:
- `_http_aggregate.get(SERVICE_B.../data/...)` — `/aggregate/{item_id}`
- `_http_b.get(SERVICE_B.../products/...)` — `_fetch_product_resilient()`
- `_http_d.get(SERVICE_D.../inventory/...)` — `_fetch_inventory()`

注入箇所（service-b）:
- `_http_c.get(SERVICE_C.../reviews/...)` — `_fetch_reviews()`

## Rationale

### service-c/d の実装パターンを採用した理由

同一リポジトリ内で参照実装が存在する。一貫性を保つことで
「なぜ service-a/b だけ違うのか」という疑問を排除できる。

### `_xray_headers()` を手動実装した理由

aws_xray_sdk の httpx 向け自動計装は存在しない。
TraceHeader クラスを使う方法もあるが、`entity.trace_id` と `entity.id` から
ヘッダー文字列を直接生成する方がシンプルで依存が少ない。

### ヘルスチェックエンドポイントもトレースされる点について

`/health` エンドポイントも `XRayMiddleware` でトレースされるが、
ポートフォリオ規模では sampling 設定で対処するより許容するほうがシンプル。

## Consequences

- ✅ service-a → service-b → service-c がサービスマップで1本の線として繋がる
- ✅ service-a → service-d も同様に繋がる
- ✅ `annotation.experiment_id = "xxx"` フィルタで実験中のトレースを絞り込める
- ✅ 「network_latency 実験中に service-b で 500ms スパイクが発生した」をトレースで確認できる
- ✅ circuit breaker が開いた瞬間のトレース（stale cache パス）も可視化できる
- ⚠️ service-b → service-a (`/items/{item_id}` の upstream 呼び出し) はヘッダー注入しない。循環参照の追跡は不要なため省略
