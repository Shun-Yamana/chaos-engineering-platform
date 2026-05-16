# ADR 039 - experiment_evaluator get_emf_max の CloudWatch period 丸め修正

- Status: Accepted
- Date: 2026-05-16

## Context

CloudWatch `get_metric_statistics` API は `Period` パラメータが 60 の倍数であることを要求する。
`_query_sum` と `_query_p95` はすでに `((raw + 59) // 60) * 60` で丸めていたが、
`get_emf_max` のみ `max(int((end - start).total_seconds()), 60)` のままで丸めなしだった。

実験が 60 の倍数でない秒数で停止した場合（auto_stopper による `_emergency_recover` 後に
`duration_seconds` が端数になるケースなど）に period が 60 の倍数にならず、
CloudWatch API エラーが発生する。`get_emf_max` は `memory_stress` の `peak_memory_mb`
評価に使われるため、memory_stress 実験の評価が失敗する。

## Decision

`get_emf_max` の period 計算に `_query_sum` と同じ丸め式を適用する。

```python
raw = max(int((end - start).total_seconds()), 60)
period = ((raw + 59) // 60) * 60
```

## Rationale

### 統一すべき理由
3 つのクエリ関数で period 計算ロジックが異なるのは保守上の危険。
同じ CloudWatch API を呼ぶ以上、同じ丸め式を使う。

## Consequences

- `memory_stress` 実験の `peak_memory_mb` 評価で CloudWatch API エラーが発生しなくなる。
- period が増える方向に丸められるため、取得するデータ範囲がわずかに広がることがある（許容範囲）。
