# ADR 091 - service-a NetworkPolicy の service-d egress 欠落と product ID 不一致

- Status: Accepted
- Date: 2026-05-29

## Context

traffic-generator を動かしてサービスチェーンを確認したところ、
service-a のメトリクスが以下の状態だった。

```
ServiceBCallDurationMs: 0
ServiceDCallDurationMs: 0
FallbackCount: 1
InventoryUnavailableCount: 1
CircuitBreakerState: 1
```

2 つのバグが同時に存在していた。

**バグ①: product ID 不一致**
traffic-generator が `random.randint(1, 20)` で数値 ID（例: `12`）を生成していたが、
service-b が持つ商品は `p-001`〜`p-003` のみ。
全リクエストが 404 になり `_product_cb` (circuit breaker) が 5 回失敗でトリップしていた。

**バグ②: NetworkPolicy egress 欠落**
service-a の NetworkPolicy egress には service-b しか記載されておらず、
service-d (`/inventory/{id}`) への egress ルールがなかった。
VPC CNI が全パケットをドロップするため `_inventory_cb` が 3 回失敗でトリップし、
以降 30s ごとの half-open probe も失敗し続けた。

## Decision

- traffic-generator の product ID を `random.choice(["p-001", "p-002", "p-003"])` に変更
- service-a NetworkPolicy egress に `podSelector: {app: service-d}` (port 8000) を追加

## Rationale

NetworkPolicy はサービスを追加するたびに ingress/egress 双方を更新する必要があるが、
片方だけ書くと無音で通信断になるため発見が遅れやすい。
ADR 086 の CI チェックがあれば PR 段階で検出できたはずで、
今後は NetworkPolicy coverage CI を活用する。

## Consequences

修正後: `FallbackCount: 0`, `InventoryUnavailableCount: 0`, `CircuitBreakerState: 0`
`ServiceBCallDurationMs: ~17ms`, `ServiceDCallDurationMs: ~6ms` で正常疎通を確認。

新サービスを追加する際は必ず呼び出し元・呼び出し先の双方の NetworkPolicy を確認すること。
