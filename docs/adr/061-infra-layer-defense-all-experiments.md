# ADR 061 - 10 実験に対するインフラ層防衛策の整理

- Status: Accepted
- Date: 2026-05-23

## Context

10 実験（b×5 / c×5）に対してインフラ層（Kubernetes / Envoy proxy）で取れる防衛策を網羅的に整理する。
アプリ層（フォールバック・キャッシュ・retry）は別 ADR で扱う。

## Decision

インフラ層の防衛策を実験ごとに確定し、不足していた service-c の PDB を追加する。

## 実験別インフラ層防衛策

### b×5（service-b 攻撃）

| 実験 | インフラ防衛策 | 実装状態 |
|---|---|---|
| ① pod_kill | replicas=2 / PDB minAvailable=1 / multi-AZ spread / readinessProbe / preStop sleep 5 | 実装済 |
| ② cpu_stress | HPA（CPU 60% でスケールアウト）/ Envoy timeout 200ms / circuit_breaker | 実装済 |
| ③ memory_stress | memoryPercentage=95 で OOMKill 即発動 / replicas=2 + PDB / readinessProbe | 実装済（ADR 060） |
| ④ network_latency | Envoy timeout 200ms のみ（Pod レベルの防衛策は効果なし） | 実装済 |
| ⑤ http_error_inject | **なし**（Envoy abort filter は Pod 手前で発火するためインフラ対応不可） | N/A |

### c×5（service-c 攻撃）

| 実験 | インフラ防衛策 | 実装状態 |
|---|---|---|
| ⑥ pod_kill | replicas=2 / **PDB minAvailable=1（本 ADR で追加）** / multi-AZ spread / readinessProbe | 追加（本 ADR） |
| ⑦ cpu_stress | HPA（CPU 60%）/ Envoy timeout 500ms（b→c）/ circuit_breaker | 実装済（ADR 059） |
| ⑧ memory_stress | memoryPercentage=95 で OOMKill 即発動 / replicas=2 + **PDB（本 ADR で追加）** | 追加（本 ADR） |
| ⑨ network_latency | Envoy timeout 500ms（b→c）のみ（Pod レベルの防衛策は効果なし） | 実装済 |
| ⑩ http_error_inject | **なし**（同⑤と同理由） | N/A |

## Rationale

### FIS 実験（①②③⑥⑦⑧）はインフラ層で対処できる

Pod 自体が障害の対象（削除・CPU 枯渇・メモリ枯渇）のため、「Pod を早く復旧させる」または「障害 Pod を迂回する」インフラ対策が有効。

| 障害種別 | インフラ防衛の核心 |
|---|---|
| pod_kill | PDB で同時削除数を制限 → replicas 数で容量を確保 |
| cpu_stress | HPA で新 Pod を追加（FIS は実験開始時の Pod のみ標的にするため新 Pod はクリーン） |
| memory_stress | memoryPercentage を上げて即 OOMKill → Kubernetes 自動再起動で素早く健全な状態に戻す |

### Envoy 実験（④⑤⑨⑩）はインフラ層では対処できない

遅延・エラーは Envoy の egress proxy（サービス間通信の出口）で注入される。Pod をいくら増やしても・再起動しても、Envoy の fault filter が有効な間は全リクエストに障害がかかり続ける。

唯一有効なインフラ要素は **Envoy timeout**（fail fast への変換）だけであり、これは実装済み。

- a→b: timeout 200ms
- b→c: timeout 500ms

Envoy 実験に対する本質的な防衛はアプリ層（フォールバック・キャッシュ）でしか実現できない。

### service-c PDB の欠落

service-b には PDB が設定されていたが service-c には未設定だった。⑥ pod_kill・⑧ memory_stress（OOMKill）で FIS が service-c の全 Pod を同時に削除できる状態だったため、本 ADR で追加した。

## 変更内容

- `k8s/pdb.yaml`: service-c の PodDisruptionBudget（minAvailable=1）を追加

## Consequences

- ⑤ ⑩（http_error_inject）はインフラ層の防衛策がゼロ。emergency_stop（CloudWatch アラーム → DynamoDB）が唯一の自動停止手段。
- ④ ⑨（network_latency）は Envoy timeout のみ。タイムアウト値の妥当性（200ms / 500ms）は実験結果を見て調整する。
- アプリ層防衛策（フォールバック / キャッシュ / Envoy retry）は次の ADR で設計する。
