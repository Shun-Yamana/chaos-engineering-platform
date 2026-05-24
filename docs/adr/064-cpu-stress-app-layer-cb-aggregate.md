# ADR 064 - cpu_stress アプリ層防御 — /aggregate/{item_id} に CB を追加

- Status: Accepted
- Date: 2026-05-24

## Context

② cpu_stress（service-b CPU 80%）実験において、インフラ層の防衛（HPA・outlier_detection）は整備済み（ADR 059・061）。

アプリ層を確認すると、`/aggregate/products/{product_id}` は `_product_cb`（サーキットブレーカー）+ stale cache + fallback が揃っているが、JMeter がトラフィックを流す主エンドポイント `/aggregate/{item_id}` は **stale cache のみで CB がない**。

cpu_stress の障害モードは「pod が生きているが遅い」であり：
- Envoy per_try_timeout(100ms) → route timeout(200ms) でエラーになる
- retry_on="connect-failure,reset" のためリトライは発動しない
- outlier_detection が eject するまでの最大5回（5 × 200ms = 1s）、毎回フルタイムアウト待ちが発生する

CB があれば5回失敗後は open になり、以降は stale cache を即座に返せる。

## Decision

**service-a の `/aggregate/{item_id}` に `_aggregate_cb`（`_CircuitBreaker` インスタンス）を追加し、
CB open 時は stale cache を即返す。**

## Rationale

### Envoy outlier_detection との役割分担

Envoy outlier_detection は endpoint（pod）単位で eject するインフラ層の防衛。
`_aggregate_cb` はサービス呼び出しパス全体をアプリ層で保護する。

| | outlier_detection | _aggregate_cb |
|---|---|---|
| 粒度 | pod 単位 | サービス全体 |
| eject 後の挙動 | Envoy が別 pod に誘導 | CB open → stale cache 即返し |
| 5回失敗までの挙動 | 毎回 Envoy 経由で試みる | 同左 |
| 5回失敗以降 | eject で高速失敗 | open で stale cache 即返し |

両者は補完関係にある。outlier_detection は Envoy レベルで pod を除外し、
`_aggregate_cb` はアプリレベルで stale cache への切り替えを即座に行う。

### retry を追加しない理由

cpu_stress は timeout が主要障害であり、`retry_on="connect-failure,reset"` のリトライは発動しない。
仮に timeout をリトライ対象にしても、全 pod が CPU ストレスを受けている場合は
リトライ先も同様に遅く、route timeout(200ms) を消費するだけで効果がない（ADR 063 参照）。

### _product_cb との一貫性

`/aggregate/products/{product_id}` の `_product_cb` と同一の `_CircuitBreaker` クラスを流用する。
閾値・ハーフオープン時間も同じ（FAIL_THRESH=5、HALF_OPEN=30s）。

## Consequences

- CB open 後の stale cache への切り替えが高速になる（最大 200ms の節約 × リクエスト数）
- stale cache が空の場合（実験開始直後の初回リクエスト）は CB が開いていても 502 になる点は変わらない
- EMF メトリクス（`CircuitBreakerState`）で `/aggregate/{item_id}` の CB 状態を CloudWatch に送信できる
