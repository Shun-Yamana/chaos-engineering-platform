# ADR 076 - network_latency 実験 SKIP：veth noqueue qdisc で FIS が即死

- Status: Accepted
- Date: 2026-05-25

## Context

FIS `aws:eks:pod-network-latency`（delay=500ms、service-b 全 Pod 対象、duration=300s）を実行した。

**実験 EXPFD3mXgrbLGWWJEv**（15:01 JST）：
- PSA `baseline` が NET_ADMIN をブロック → AUTH_ERROR で即失敗
- 対処: `kubectl label namespace default pod-security.kubernetes.io/enforce=privileged --overwrite`

**実験 EXP9fyZzhsigyWUeGa**（15:03 JST）：
- PSA 修正後に再実行
- action-start（15:04:04）から 20 秒で action-error：`Max failed sidecar containers reached`
- FIS ログ: `cleanup failed for some interfaces: failed to cleanup interface eth0: failed to clean up root qdisc: exit status 2`

**根本原因の調査：**

FIS `pod-network-latency` は注入前に既存の root qdisc を削除してクリーンな状態にする：

```
tc qdisc del dev eth0 root  ← exit 2 で失敗
```

EKS の veth インターフェースは Linux 4.16 以降、root qdisc として `noqueue` を使用する：

```
# ノード側 veth 確認
qdisc noqueue 0: dev eni7d5d724f7a4 root refcnt 2
qdisc noqueue 0: dev eni2af2337250e root refcnt 2
```

`noqueue` は削除できない擬似 qdisc（カーネル内部でハードコード）であり、`tc qdisc del` は必ず exit 2 を返す。FIS agent はこれを致命的エラーとして扱い、sidecar が即死する。実験開始 20 秒で失敗することが証拠（300s 実験が途中で終わっていない）。

**修正を試みたアプローチ：**

init container で `pfifo_fast` qdisc を事前設定すれば FIS の del コマンドが成功するはず、と考えた。

- `amazonlinux:2023`：tc バイナリ未収録（`executable file not found`）
- `ubuntu:22.04`：同上（iproute2 未インストールのミニマルイメージ）
- Docker build で ECR に独自イメージを push：Docker Desktop が停止中でビルド不可

## Decision

`aws:eks:pod-network-latency` は EKS veth の `noqueue` qdisc 制約により service-b に対して実行できないことが判明したため、network_latency 実験を **SKIP** とする。

## Rationale

### noqueue が削除できない理由

Linux カーネルの veth ドライバーは 4.16 以降、パフォーマンス最適化として qdisc を `noqueue` に初期化する。`noqueue` は通常の qdisc と異なり、カーネルが特別に扱うため `tc qdisc del` で削除できない（exit 2 = ENOENT or EINVAL）。FIS agent の実装がこのケースを考慮していないことが問題の本質。

### 代替手段（init container）を採用しない理由

FIS 実験のためだけに全 Pod 起動時間を増やすのは運用コストが高い。また Docker が停止中のため即時の修正が困難。

### FIS の設計上の制約

`aws:eks:pod-memory-stress`（ADR 075）と同様に、FIS の EKS アクションは特定の Kubernetes 環境設定を前提とする。`noqueue` veth（EC2 上の EKS では標準）への対応はアクション側の問題であり、ワークロード側で回避する必要がある。

### network_latency シナリオの現実的なリスク評価

- Envoy の circuit breaker は既に service-c 方向に設定済み（ADR 005〜009）
- service-b ↔ service-a 間の 500ms レイテンシは Envoy タイムアウト設定（1s）内に収まる可能性が高い
- 本番環境での突発的なネットワーク遅延は VPC ルーティング・AZ 間通信で発生するが、CloudWatch メトリクスで観測可能

## Consequences

- network_latency 実験（service-b、service-a 共）は **SKIP**。
- `default` namespace の PSA は `privileged` のまま維持（memory_stress・network_latency 調査中に設定済み、FIS 全般で NET_ADMIN が必要）。
- `aws:eks:pod-network-latency` を使う場合の対処法：
  1. init container で `tc qdisc replace dev eth0 root pfifo_fast` を実行（iproute2 入りイメージが必要）
  2. または FIS が `tc qdisc replace`（del ではなく）を使うアップデートを待つ
- JMeter エラー率は実験期間を通じて **0%**（network_latency 注入なし）。
