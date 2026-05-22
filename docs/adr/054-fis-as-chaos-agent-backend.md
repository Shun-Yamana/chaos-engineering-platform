# ADR 054 - FIS を chaos-agent の障害注入バックエンドとして採用

- Status: Accepted
- Date: 2026-05-22

## Context

chaos-agent はこれまで Kubernetes API を直接操作して障害を注入していた。

| fault_type | Before |
|-----------|--------|
| pod_kill | `CoreV1Api.delete_namespaced_pod` |
| cpu_stress | `CPU_STRESS` env var → Pod 再起動 → アプリ内 busy-loop thread |
| memory_stress | `MEMORY_STRESS_MB` env var → Pod 再起動 → アプリ内 bytearray 確保 |
| network_latency | `LATENCY_MS` env var → Pod 再起動 → アプリ内 `asyncio.sleep` |

この方式には3つの課題があった。

1. **アプリレベルの擬似障害**: `LATENCY_MS` は `asyncio.sleep` で実装しているため、OS / カーネルレベルの遅延ではない。実際のネットワーク障害を再現できていない
2. **Pod 再起動コスト**: env var を変更するたびに Kubernetes がローリングアップデートを実行し、障害注入開始まで時間がかかる
3. **chaos-agent のコード量**: 各障害タイプごとに inject/remove/cleanup メソッドが存在し、保守コストが高い

EC2 Managed Node Group への移行（ADR 051）により、Fargate の制約（DaemonSet 不可・tc netem 不可・ephemeral container 不可）が解消された。FIS のネイティブ EKS Pod アクションが使用可能になった。

## Decision

pod_kill / cpu_stress / memory_stress / network_latency の4障害タイプを AWS FIS に委譲し、chaos-agent を FIS Orchestrator として再実装する。

## Rationale

### chaos-agent による直接 K8s API 操作を外した理由

- `CPU_STRESS` / `MEMORY_STRESS_MB` / `LATENCY_MS` はアプリコードに障害ロジックが混入するため、アプリの変更が必要
- アプリ内 busy-loop は OS スケジューラに左右され、CPU% が不安定
- `asyncio.sleep` はアプリレベルの遅延であり、Envoy や TCP スタックを経由しない

### FIS を選んだ理由

- **`aws:eks:pod-network-latency`**: `tc netem` によるカーネルレベル遅延注入。アプリ非依存で本物のネットワーク遅延を再現できる
- **`aws:eks:pod-cpu-stress`**: `stress-ng` を ephemeral container として注入。OS レベルで CPU を消費し、アプリコードを変更しない
- **`aws:eks:pod-memory-stress`**: `stress-ng` による OS レベルのメモリ確保。ADR 047 の OOMKill 設計（limits 256Mi / 注入 150MB = 58%）を `memoryPercentage=58` で踏襲できる
- **`aws:eks:pod-delete`**: Kubernetes API の pod delete を FIS が代行。chaos-agent のコード削減
- **StopCondition**: CloudWatch Alarm ベースの自動停止が FIS に組み込まれ、auto_stopper との二重防護になる
- **chaos-agent のコード削減**: inject/remove/cleanup ×4 の実装が `_fis_start_experiment` と `_fis_wait_and_monitor` の2メソッドに集約

### http_error_inject を外した理由

FIS には HTTP ステータスコードを注入するネイティブアクションがない。そのため http_error_inject だけ別方式（ADR 055 参照）とした。

## Consequences

- ✅ カーネルレベルの本物の障害注入が可能になる（network_latency は特に重要）
- ✅ chaos-agent から inject/remove ×4 メソッドが削除され保守コストが下がる
- ✅ FIS の StopCondition が auto_stopper Lambda と二重の安全網になる
- ✅ FIS 実験ログ・レポートが S3 に自動出力される（既存 fis.tf の設定が新テンプレートにも適用）
- ⚠️ FIS テンプレート ID を CI/CD で `kubectl set env` により chaos-agent に渡す必要がある（`FIS_TEMPLATE_POD_KILL` / `FIS_TEMPLATE_CPU_STRESS` / `FIS_TEMPLATE_MEMORY_STRESS`）
- ⚠️ FIS StopCondition と auto_stopper が同時に発火した場合、DynamoDB の status 更新が競合する可能性がある。`_fis_wait_and_monitor` が FIS 終端状態を検知して適切に return するため実験は二重実行されないが、DynamoDB レコードの最終 status は auto_stopper 側が書く前提
