# ADR 072 - pod_kill 実験 PASS：2 replicas + PDB で 0% エラー

- Status: Accepted
- Date: 2026-05-24

## Context

FIS `aws:eks:pod-delete` アクション（実験 ID: EXP5iCAoYTBnwBVGNe）で service-b の Pod 1 台を強制削除した。JMeter が 15 rps で `/aggregate/1` にトラフィックを流し続けた状態で実験を実施し、ユーザー影響を計測した。

実験直後の 30 秒インターバル（22:44:00）でエラー率 0%・平均レイテンシ 338ms を記録し、サービス断は発生しなかった。

## Decision

pod_kill 実験の合格基準（エラー率 0%）を満たしたことを確認し、現在の防衛策（replicas: 2 + PDB + preStop sleep）をそのまま維持する。

## Rationale

### なぜ無影響だったか
- service-b は `replicas: 2` で常時 2 Pod が稼働し、ALB は 2 Pod に分散していた。
- 1 Pod が削除されても残りの 1 Pod がトラフィックを引き受け、Deployment Controller が即座に代替 Pod を起動した（確認: 削除 77 秒後に `2/2 Running`）。
- `preStop: sleep 5` により Pod は graceful に接続を閉じてから終了するため、inflight リクエストのタイムアウトも発生しなかった。
- service-a の Circuit Breaker は `CircuitBreakerState: 0`・`FallbackCount: 0` を維持し、fallback に切り替わることなく正常応答を継続した。

### 防衛策を変えない理由
今回の実験で 2 replicas + PDB で十分なことが実証された。過剰な replica 数はコストと AZ 分散の複雑さを増すだけなので現状を維持する。

## Consequences

- pod_kill シナリオにおいてサービス継続性が確認された。
- 今後 replicas を 1 に減らすと防衛策が崩れる。この ADR を根拠に 1 replica への削減を拒否する。
- AZ 障害で 1 AZ のノードが丸ごと失われた場合は 2 Pod が同一ノードにいると単点になる可能性があるが、現在 `topologySpreadConstraints` で AZ 分散を強制しているため許容範囲内。
