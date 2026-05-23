# ADR 057 - JMeter + Envoy + FIS による 10 実験マトリクス設計

- Status: Accepted
- Date: 2026-05-23

## Context

4 サービストポロジー（a→{b→c, d}）に対してカオス実験を設計するにあたり、以下の課題があった。

1. 既存の `TrafficGenerator`（30 秒おき 1 req）は負荷がほぼゼロで、Pod Kill しても回復が容易すぎる
2. 障害注入ツールが FIS と Envoy に分散しており、それぞれの適切な役割分担が未定義だった
3. `network_latency` を FIS（`aws:eks:pod-network-latency`）で実装していたが、これは Pod の全通信を遅くする粗い注入であり、実務で最も頻出する「特定依存サービスの応答劣化」を再現できていなかった

## Decision

JMeter・Envoy・FIS の役割を明確に分離し、通常トラフィック × 高負荷トラフィック × 5 障害種別 の 10 実験マトリクスを設計する。フェーズ 1 として通常トラフィック下の 5 実験から着手する。

### ツール役割分担

| ツール | 役割 |
|---|---|
| JMeter | ベーストラフィック確立（通常: ~20 req/s、高負荷: 100+ req/s）+ レイテンシ・429 の限界測定 |
| FIS | インフラ層の障害（Pod Kill / CPU 枯渇 / Memory 枯渇） |
| Envoy | アプリ層の障害（特定サービス間の遅延注入 / HTTP エラー注入） |

### 10 実験マトリクス

| | pod_kill | cpu_stress | memory_stress | network_latency | http_error_inject |
|---|---|---|---|---|---|
| **通常トラフィック** | ① FIS | ② FIS | ③ FIS | ④ Envoy | ⑤ Envoy |
| **高負荷トラフィック** | ⑥ FIS | ⑦ FIS | ⑧ FIS | ⑨ Envoy | ⑩ Envoy |

フェーズ 1: ①〜⑤（通常トラフィック下の 5 実験）

## Rationale

### FIS network_latency を外した理由

`aws:eks:pod-network-latency` は Pod の全送受信通信に遅延を注入する。これは b→c だけでなく b→d や外部 API への通信も同時に遅くするため、「c だけ遅い状態で b がどう振る舞うか」という仮説を単独では検証できない。実務で最も発生しやすい障害は特定依存サービスの応答劣化であり、FIS の粒度では再現が困難。

### Envoy delay filter を採用した理由

Envoy の delay fault filter は `envoy-service-b-egress` 等のルート単位で遅延を注入できるため、b→c パスのみに絞った遅延が可能。既存の abort filter（`http_error_inject`）と同一 ConfigMap に共存でき、実装追加コストは小さい。

### JMeter をベーストラフィックに採用した理由

`TrafficGenerator` は 1 req/30s であり、Pod Kill 時に in-flight リクエストがほぼ存在しない。実本番では Pod 障害は常時トラフィックがある状態で発生するため、JMeter で事前にトラフィックを確立してから FIS・Envoy 実験を実行することで、実践的な耐障害性検証が可能になる。

## Consequences

- Envoy ConfigMap に delay filter を追加する実装が必要（`envoy-service-b-egress` の拡張）
- `agent.py` に `_patch_envoy_delay` / `_remove_envoy_delay` メソッドを追加する
- `_NETWORK_LATENCY_TEMPLATE_ENV`（FIS ベース）は network_latency では使用しなくなるが、将来の全通信遅延テストのために残置する
- フェーズ 2（高負荷 × 5 実験）は通常トラフィック実験の結果を見て着手判断する
- 緊急停止機構（CloudWatch アラーム → DynamoDB emergency_stop）はアプリ層障害にも有効であり、Envoy 障害の stop 手段として追加実装は不要
