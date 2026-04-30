# ADR 005 - 実験合格基準：pod_kill

- Status: Accepted
- Date: 2026-04-30

## Context

pod_kill 実験の「望ましい結果」を定義する必要があった。
単に実験が完了したことを記録するだけでなく、「システムが期待通りに耐えられたか」を判断する基準が必要。

現在のインフラ設定（k8s マニフェスト確認済み）：
- replicas: 2
- readinessProbe: initialDelaySeconds=5, periodSeconds=10, successThreshold=1（デフォルト）
- FastAPI 起動時間: 約 2 秒

## Decision

pod_kill 実験の合格基準を以下の通り定める。

| 指標 | 合格ライン |
|------|-----------|
| エラーレート（最大値） | SLO閾値（5%）以内 |
| 回復時間 | 17秒以内 |
| service-b エラーレート | SLO閾値内 |
| auto-stopper | 発動しない |

## Rationale

### 回復時間 17秒の根拠

readinessProbe の設定から導出した理論値：

```
回復時間 = Pod起動時間 + initialDelaySeconds + periodSeconds × successThreshold
         = 2s + 5s + 10s × 1
         = 17秒
```

17秒を超えた場合は、`initialDelaySeconds` の短縮またはアプリ起動の高速化が必要というアクションに直結する。

### エラーレート 5% 以内の根拠

replicas=2 の環境で 1 台が死んだとき、残り 1 台がトラフィックを処理する。
低負荷環境であれば残り 1 台で捌けるため、エラーレートは SLO 閾値（5%）を超えないはず。
これを超えた場合は replicas 不足または readinessProbe の設定が緩すぎることを示す。

### auto-stopper を発動させないことを合格とする理由

auto-stopper は SLO 違反（バーンレート閾値超過）を検知して実験を強制停止する。
発動しないということは、障害中もシステムが SLO の範囲内で自己回復できたことを意味する。

### 負荷条件をスコープ外とした理由

ピーク負荷時に pod_kill を行うと、残り 1 台が過負荷になりカスケード障害に発展するリスクがある。
このシナリオは network_latency 実験でカスケード障害として別途カバーする。
pod_kill 実験のスコープは「低負荷時の基本的な回復確認」に限定する。

## Consequences

- 合格基準が数値で定義されたことで、実験評価 Lambda（未実装）が判定ロジックを実装する際の仕様になる
- ピーク負荷 + pod_kill の組み合わせシナリオは今後の拡張として残る
- PodDisruptionBudget は現時点で未設定。複数台同時削除への耐性は未検証
