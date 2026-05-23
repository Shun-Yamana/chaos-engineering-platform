# ADR 059 - cpu_stress 実験の防衛策として HPA を活用する

- Status: Accepted
- Date: 2026-05-23

## Context

② cpu_stress（service-b CPU 80% 固定）実験において、FIS が Pod の CPU を 80% に固定すると
Envoy の route timeout（200ms）を超えるレスポンス遅延が発生し、service-a の SLO を違反する。

service-b の HPA はすでに `k8s/hpa.yaml` に定義済み（CPU 60% でスケールアウト、min 2 / max 5）だが、
この HPA が cpu_stress 実験に対してどのように機能するかが未整理だった。

service-c についても cpu_stress（⑦）の攻撃対象となったが、HPA が未定義だった。

## Decision

service-b / service-c ともに HPA（CPU 60% トリガー、min 2 / max 5）を定義する。
service-b はすでに存在するためそのまま活用し、service-c の HPA を `k8s/hpa.yaml` に追加した。

## Rationale

### FIS のターゲット選択タイミングと HPA の組み合わせ

FIS は実験開始時点のラベルセレクタ一致 Pod をターゲットとして確定する（動的に追加しない）。

```
実験開始時:  Pod-b-aaa (CPU 80%) ← FIS 対象
             Pod-b-bbb (CPU 80%) ← FIS 対象

HPA スケールアウト後:
             Pod-b-aaa (CPU 80%) ← FIS 対象（遅い）
             Pod-b-bbb (CPU 80%) ← FIS 対象（遅い）
             Pod-b-ccc (正常)    ← FIS 未選択（後から追加）
```

HPA が出した新 Pod はストレスがかかっておらず正常稼働する。Envoy の ROUND_ROBIN により
既存の遅い 2 Pod と新しいクリーンな Pod の間でトラフィックが分散されるため、
スケールアウト完了後は service-a への影響が段階的に緩和される。

### cpu_stress と pod_kill の防衛策の違い

| 実験 | 主な脅威 | 有効な防衛策 |
|---|---|---|
| pod_kill | Pod が消える（容量欠如） | replicas 数・PDB・Envoy outlier_detection |
| cpu_stress | Pod は生きているが遅い（処理スロットル） | HPA スケールアウト（新 Pod が FIS 対象外になる） |

pod_kill に対して HPA は無力（削除 → 再スケジュールは HPA ではなく Deployment が担う）だが、
cpu_stress に対しては HPA が実質的な逃げ場を作る点で有効。

## Consequences

- service-c HPA を `k8s/hpa.yaml` に追加（`minReplicas: 2 / maxReplicas: 5 / CPU 60%`）
- HPA スケールアウトのラグ（デフォルト 15s）の間は SLO 違反が継続する
- Envoy の `circuit_breaker max_requests: 100` が遅い Pod へのリクエスト積み上がりを防ぐため、
  HPA スケールアウト前の緊急防衛ラインとして機能する
- ⑦ cpu_stress（service-c）に対しても同じ仕組みが働くが、service-c はフォールバックがあるため
  HPA スケールアウト前でも service-a の SLO は維持される
