# ADR 048 - evaluator: fault_type 欠損アイテムを KeyError ではなく graceful skip に変更

- Status: Accepted
- Date: 2026-05-17

## Context

chaos-agent が再起動されると、それ以前に `running` 状態だった実験は誰も完了させないまま残る（zombie 実験）。
これらを DynamoDB で強制的に `stopped` に更新した際、`update_item` に誤った `started_at` キー形式を渡してしまい、
`fault_type` などのフィールドを持たない部分的なアイテムが生成された。

DynamoDB Streams がこれらのアイテムの変更イベントを Lambda に配信すると、`evaluate()` 内の
```python
fault_type = item["fault_type"]  # KeyError
```
が例外を送出した。Lambda が失敗すると DynamoDB Streams はバッチをリトライするため、
同じアイテムに対する呼び出しが 5 分ごとに繰り返され（300s sleep + KeyError）、
Lambda の同時実行枠が占有され続けた。その結果、新しい実験の評価イベントが処理されなくなった。

## Decision

`evaluate()` の冒頭で `item.get("fault_type")` を使い、`None` の場合は `{}` を返して graceful skip する。

```python
fault_type = item.get("fault_type")
if not fault_type:
    logger.warning(f"Missing fault_type for {item.get('experiment_id')}, skipping")
    return {}
```

handler 側の `if not result: continue` により Lambda は正常終了し、
DynamoDB Streams はリトライを停止して次のイベントに進む。

## Rationale

- `item["fault_type"]` は KeyError を送出する → Lambda 失敗 → Streams リトライ → 無限ループ
- `item.get("fault_type")` + 早期 return は Lambda を正常終了させ、リトライを防ぐ
- `fault_type` のない評価不能アイテムはスキップすることが正しい動作であり、情報欠損を警告ログに残す

## Consequences

- `fault_type` 欠損アイテムは評価なし（`evaluation_result` 未書込み）のまま残る
- Streams のリトライループが解消され、新しい実験の評価が正常に処理されるようになる
- 根本原因（zombie 実験発生と誤ったキー形式での update_item）は運用手順の問題であり、コードでの防止は [[049-http-error-inject-phase-b-offset-180s]] と別に対処
