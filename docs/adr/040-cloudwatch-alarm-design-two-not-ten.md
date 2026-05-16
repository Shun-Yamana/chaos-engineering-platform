# ADR 040 - CloudWatch アラーム設計：実験タイプ×サービスの 10 個ではなく観測可能メトリクスで 2 個追加

- Status: Accepted
- Date: 2026-05-16

## Context

既存 CloudWatch アラームは 7 個。「5 実験タイプ × 2 サービス = 10 個作るべきでは」という検討をした。

## Decision

10 個は作らず、既存 7 個に 2 個を追加して計 9 個とする。

追加する 2 個:
1. **service-b-no-healthy-hosts**: `AWS/ApplicationELB` の `HealthyHostCount < 1`（1 評価期間）
2. **service-a-aggregate-latency-p95**: `ChaosExperiment` の `AggregateDurationMs p95 > 450ms`（2 評価期間）

## Rationale

### 10 個にしない理由

**auto_stopper はアラームを見ない**。auto_stopper は DynamoDB に書かれた SLI データ
（error_rate / burn_rate）を直接クエリして判断する。アラームの数を増やしても
auto_stopper の検知精度には影響しない。

**fault_type はメトリクスのディメンションに存在しない**。「http_error_inject 専用アラーム」を
作ろうとしても、`HTTP5xxCount` のディメンションは `LoadBalancer` や `TargetGroup` であり
fault_type を区別できない。結局、同じメトリクスに同じ閾値のアラームが重複するだけになる。

### 2 個を追加する理由

**HealthyHostCount < 1**: pod_kill でサービスが完全停止した状態を検知できる唯一のアラーム。
既存アラームの `HTTP5xxCount` や error_rate は「リクエストが来ているが失敗している」状態を検知するが、
ALB のヘルシーホストが 0 になった場合（pod が全滅）は検知できない。

**AggregateDurationMs p95**: service-a の `network_latency` 実験で生じたレイテンシ伝播を
CloudWatch で観測できる唯一のメトリクス。chaos-agent が EMF で書き込む `ChaosExperiment` 名前空間の
カスタムメトリクスであり、ALB / ECS 標準メトリクスでは観測できない。

## Consequences

- 10 個案と比べてアラーム費用を最小限に抑えられる。
- auto_stopper の動作は変わらない（SLI データを直接見るため）。
- CloudWatch ダッシュボードで pod_kill の完全停止と service-a レイテンシ伝播を可視化できるようになる。
