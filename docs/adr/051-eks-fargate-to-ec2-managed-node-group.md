# ADR 051 - EKS Fargate から EC2 Managed Node Group への移行

- Status: Accepted
- Date: 2026-05-22

## Context

本プラットフォームはこれまで EKS Fargate（サーバーレス）でワークロードを動かしていた。
X-Ray DaemonSet・Prometheus Node Exporter の追加と、FIS によるカーネルレベル障害注入を実現するにあたり、Fargate の制約が複数の阻害要因になった。

**Fargate の制約**

| 制約 | 影響 |
|------|------|
| DaemonSet 使用不可 | X-Ray daemon・Prometheus Node Exporter を全ノードに配置できない |
| `tc netem` 使用不可 | FIS `aws:eks:pod-network-latency` が動作しない（カーネルレベル遅延注入の前提） |
| ephemeral container 使用不可 | FIS `aws:eks:pod-cpu-stress` / `aws:eks:pod-memory-stress` が動作しない |
| CoreDNS 起動 patch が必要 | `null_resource.patch_coredns_fargate` / `restart_coredns_after_addon` で毎回 apply 時間が +10 分かかっていた |

加えて、`LATENCY_MS` / `CPU_STRESS` / `MEMORY_STRESS_MB` 環境変数によるアプリレベルの障害注入は、OS カーネルを経由しないため Chaos Engineering として本物の負荷を再現できていなかった。

## Decision

EKS ノードを Fargate から **EC2 Managed Node Group（t3.medium × 2〜4）** に移行する。
Fargate プロファイルは移行完了確認まで並存させ、切り戻しできる状態を維持する。

## Rationale

### Fargate 継続を外した理由

- DaemonSet が使えない限り X-Ray / Prometheus のノード単位収集が実現できない
- FIS のネイティブアクション（`pod-network-latency` / `pod-cpu-stress` / `pod-memory-stress`）は EC2 前提であり、Fargate のままでは FIS 統一化ができない
- CoreDNS patch workaround を抱え続けることは運用負荷と terraform apply の不安定要因になる

### EC2 Managed Node Group を選んだ理由

- **DaemonSet 対応**: X-Ray DaemonSet・Prometheus Node Exporter を全ノードに自動配置できる
- **tc netem 有効**: FIS `aws:eks:pod-network-latency` がカーネルレベルで動作し、アプリ非依存の本物の遅延を注入できる
- **FIS cpu/memory stress 対応**: ephemeral container（stress-ng）が使用可能になり、FIS への完全移行ができる
- **CoreDNS patch 排除**: EC2 では addon が自動的に Running になり、`null_resource` 2 つを削除できる（terraform apply が安定・高速化）
- **コスト**: t3.medium は Fargate の同スペック比で安価であり、スケーリングも柔軟

### Fargate + EC2 混在を外した理由

2 種類のコンピューティングを並走させると IAM ロール・スケジューリング設定・ログ収集の複雑性が増す。
移行期間のみ並存を許容し、確認後に Fargate プロファイルを削除する方針とした。

## Consequences

- ✅ DaemonSet（X-Ray / Prometheus）が利用可能になる
- ✅ FIS ネイティブアクション 4 種すべてに対応できる
- ✅ `null_resource.patch_coredns_fargate` / `restart_coredns_after_addon` を削除し terraform apply が安定する
- ✅ ADR 047 の memory_stress 設計（service-b limits: 256Mi / 注入: 150MB）は Pod spec が変わらないため引き続き有効
- ⚠️ 移行中に ALB が一時切断するリスクがある → メンテナンスウィンドウ内で実施すること
- ⚠️ EC2 ノードのセキュリティパッチ管理が発生する（Managed Node Group のため AWS がマイナーパッチを自動適用）
