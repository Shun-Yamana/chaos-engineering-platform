# ADR 092 - cpu_stress Phase B 評価を ALB メトリクスから EMF に切替

- Status: Accepted
- Date: 2026-05-29

## Context

cpu_stress 実験の評価結果で Phase B (Recovery/TTR) が常に `no data` になっていた。

```
Phase B — Recovery / TTR
–  p95 latency ms recovery  no data ≤ 500
```

`evaluate_cpu_stress` の Phase B は `get_alb_p95_ms(fault_end+60s, fault_end+180s)` で
ALB の `TargetResponseTime` を CloudWatch から取得していた。
しかし traffic-generator は ADR 090 のヘッドレスサービス経由でポッド IP に直接接続するため
ALB を通らず、ALB メトリクスにはデータが存在しない。

Phase A でデータが取れていた（224.91ms）のは、実験中にフロントエンドの Demo ビュー等から
ALB 経由でリクエストが来ていたため。fault_end 後の 60〜180s 窓は手動操作がなく無データ。

## Decision

Phase B の p95 取得を `get_emf_p95_ms("AggregateDurationMs", "service-a", ...)` に切替える。
EMF メトリクスは service-a が全リクエストに対して出力するため、
traffic-generator の 20 req/s が常に記録される。

`_first_below_threshold` の metric_fn も同様に EMF 関数に置き換える。

## Rationale

### EMF を選んだ理由
traffic-generator が ALB をバイパスする設計を変えるよりも、
すでに存在する EMF メトリクス (`ChaosExperiment` namespace) を参照する方が
インフラ変更なしに即対応できる。
Phase A の ALB メトリクスはそのまま残す（ALB 経由の外部トラフィックを代表するため）。

### ALB ベースに統一しない理由
traffic-generator を ALB 経由に変更すると、
ALB の Listener/Target Group を経由するレイテンシが加わり
Pod 間の実レイテンシが計測できなくなる。

## Consequences

- Phase B に traffic-generator のリクエストが反映され、no data が解消される
- Phase A (ALB) と Phase B (EMF) でメトリクスソースが異なる点に注意
- service-a の EMF 出力が止まると Phase B も no data になるため、
  service-a の CloudWatch エージェント設定が前提条件となる
