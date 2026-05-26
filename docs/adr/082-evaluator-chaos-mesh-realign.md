# ADR 082 - experiment_evaluator を Chaos Mesh 移行後の実態に合わせて再調整

- Status: Accepted
- Date: 2026-05-26

## Context

Chaos Mesh 移行（ADR 079〜081）により、障害注入・回復のメカニズムが根本的に変わった。
`experiment_evaluator.py` の Phase B 計測オフセットと TTR 上限は FIS + Envoy 時代の rolling update 前提で設計されており、Chaos Mesh 環境では過大な余裕を与えている。

また `memory_stress` は Chaos Mesh StressChaos が service-b の cgroup に正しく届いて OOMKill が発生したかどうかを評価していなかった。`node_failure` は新規実験のため評価関数が存在しない。

## Decision

以下の方針で evaluator を再設計する。

### 変更なし
- **pod_kill**: PDB + replicas 保護の評価。Phase B オフセットは元々 0s。現行維持。
- **cpu_stress**: `+60s` オフセットは stress-ng 停止後の CPU スケジューラ再調整に由来。Chaos Mesh StressChaos も stress-ng を使うため維持。

### Phase B オフセット・TTR 短縮（rolling update なし）

| 実験 | 旧 Phase B 開始 | 新 Phase B 開始 | 旧 TTR | 新 TTR | 理由 |
|---|---|---|---|---|---|
| `memory_stress` | `fault_end + 90s` | `fault_end + 0s` | 150s | 30s | OOMKill 後のコンテナ再起動は 10〜20s |
| `http_error_inject` | `fault_end + 180s` | `fault_end + 0s` | 240s | 30s | HTTPChaos CR 削除で即時回復 |
| `network_latency` | `fault_end + 90s` | `fault_end + 0s` | 150s | 30s | NetworkChaos CR 削除で tc netem 即時除去 |

### memory_stress — OOMKill 確認を Phase A に追加

Chaos Mesh StressChaos が cgroup に正しく届いたかどうかを Container Insights メトリクスで確認する。

```
Phase A 追加基準:
  oomkill_confirmed:
    pod_number_of_container_restarts (ContainerInsights)
    ClusterName=<cluster>, Namespace=default, PodName=service-b-*
    fault_end - fault_start 区間での delta ≥ 1
```

Lambda 環境変数に `EKS_CLUSTER_NAME` を追加する。

### node_failure — 評価関数を新規追加

```
Phase A（障害吸収）:
  error_rate_during_fault  ≤ 10%   # PDB 保護で大半のリクエストは通る

Phase B（回復）:
  error_rate_recovery      ≤ 1%    # fault_end 後 300 秒以内
  TTR limit                300s    # Pod 再スケジュール + ASG ノード補充を含む
```

TTR（ALB エラーレートが 1% 以下に回復した時間）を Pod 再スケジュール完了の近似値として使う。

## Rationale

### オフセット除去の根拠

FIS + Envoy 時代の余裕は以下の処理に必要だった：

- `memory_stress`: `MEMORY_STRESS_MB` env var 削除 → rolling update (~90s)
- `http_error_inject`: scale=0 → http_error_remove → scale back → rolling update (~180s)
- `network_latency`: Envoy delay 除去 → rollout restart (~90s)

Chaos Mesh は CR 削除で障害が即時解除されるため、これらの待機はすべて不要になる。

### TTR 30s の根拠

- OOMKill 後のコンテナ再起動: ~10s（ADR 075 実測値）
- NetworkChaos / HTTPChaos CR 削除後の回復: ~数秒（kind クラスターで確認済み）
- 30s は実態より余裕を持たせた上限

### OOMKill 確認に Container Insights を選んだ理由

- Lambda から boto3 で直接クエリ可能（Prometheus は EKS 外への公開が必要）
- `amazon-cloudwatch-observability` addon が ADR 011 で設定済み
- `pod_number_of_container_restarts` の fault 区間 delta ≥ 1 で OOMKill 発生を確認できる

## Consequences

- Lambda 環境変数に `EKS_CLUSTER_NAME` が必要
- Container Insights が有効でない環境では `oomkill_confirmed` が `no data` になる（pass: null として扱い全体 PASS/FAIL には影響しない）
- 各実験の TTR が短縮されるため、回復が遅い場合に FAIL になりやすくなる。実験後に閾値を調整する（ADR 030 の方針）
