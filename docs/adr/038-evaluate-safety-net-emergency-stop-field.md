# ADR 038 - evaluate_safety_net の auto_stopper 判定を emergency_stop フィールドで行う

- Status: Accepted
- Date: 2026-05-16

## Context

auto_stopper は実験を止めない。フローは次の通り。

1. auto_stopper が SLO 違反を検知 → DynamoDB に `emergency_stop=True` / `emergency_stop_at` を `update_item`
2. chaos-agent の `_interruptible_sleep` が `emergency_stop` を検知 → `_emergency_recover` スレッドを起動
3. `_emergency_recover`: scale-to-0 → fault 除去 → 30 秒待機 → scale-back。実験の status は変更しない。
4. 実験は `duration_seconds` 経過後に `status="completed"` で終了する。

旧 `evaluate_safety_net` の実装は以下の 2 つの誤りを含んでいた。

1. **dead code**: `auto_stopper_fired` 変数を `"auto_stopper" not in stop_reason`（論理が逆）で計算していたが、
   直後に `fired = bool(item.get("stop_reason") == "emergency_stop")` で上書きされ、
   `auto_stopper_fired` は返却 dict にも使われない dead code だった。
2. **フィールド誤り**: `fired` 変数は `stop_reason` フィールドを見ていたが、
   auto_stopper は実験を stop させないため `stop_reason` は設定されない。
   `http_error_inject` の Safety Net 期待値は「auto_stopper が発動すること」だが、
   `fired` が常に False になるため Safety Net が常に fail だった。

## Decision

`evaluate_safety_net` を `item.get("emergency_stop")` フィールドで auto_stopper 発動を判定するよう修正する。
dead code（`auto_stopper_fired` 変数の旧計算式）を削除し、正しいロジックを 1 箇所に集約する。

```python
def evaluate_safety_net(fault_type: str, item: dict) -> dict:
    auto_stopper_fired = bool(item.get("emergency_stop"))
    if fault_type == "http_error_inject":
        expected = True
        passed = auto_stopper_fired
    else:
        expected = False
        passed = not auto_stopper_fired
    return {"auto_stopper_fired": auto_stopper_fired, "expected": expected, "pass": passed}
```

## Rationale

### stop_reason を使わない理由
auto_stopper は実験を止めない（status を変えない）。`stop_reason` は手動停止（DELETE API）時に
"manual" が設定されるフィールドであり、auto_stopper の発動とは無関係。

### emergency_stop フィールドを使う理由
auto_stopper が `update_item` で書き込む唯一の専用フィールド。
ADR 037 の `_record_experiment` update_item 化により実験終了後も保持される。

## Consequences

- `http_error_inject` の Safety Net 評価が正しく機能するようになる。
- ADR 037 の `_record_experiment` update_item 化が前提条件となる（両方がないと動作しない）。
- `emergency_stop` フィールドが未設定の場合（auto_stopper が発動しなかった場合）は
  `bool(None)` → `False` として正しく扱われる。
