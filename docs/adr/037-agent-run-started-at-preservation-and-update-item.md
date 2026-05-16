# ADR 037 - chaos-agent run() の started_at 保持と _record_experiment update_item 化

- Status: Accepted
- Date: 2026-05-16

## Context

DynamoDB のキーは `experiment_id`（PK）と `started_at`（SK）の複合キー。
api_handler が `pending` アイテムを `started_at=T1` で作成し、chaos-agent の `_claim()` が
`update_item` で同じキーを `running` に変更する、という設計だった。

しかし `run()` の先頭で `experiment.started_at = datetime.now()` が無条件に T2 を代入していた。
その後 `_record_experiment` が `put_item` で `{experiment_id, T2}` という新しいアイテムを作成するため、
同一実験に対してキーが異なる行が 2 つ存在することになり、フロントエンドの一覧に実験カードが 2 枚表示された。

さらに auto_stopper は `update_item` で `emergency_stop=True` と `emergency_stop_at` を書き込むが、
実験終了時に `_record_experiment` が `put_item` でアイテムを上書きするため、これらのフィールドが消去された。
`experiment_evaluator.py` の `evaluate_safety_net` が `emergency_stop` を参照できず、
`http_error_inject` の Safety Net 評価が常に fail になっていた。

## Decision

1. `run()` での `started_at` 代入に `if not experiment.started_at:` ガードを追加し、
   api_handler が設定した値を保持する。
2. `_record_experiment` は `status == "running"`（初回作成）のみ `put_item` を使用し、
   それ以外の終端状態（`completed` / `stopped` / `failed`）は `update_item` に変更して
   auto_stopper が書いたフィールドを保持する。

## Rationale

### put_item のままにしない理由
DynamoDB の `put_item` はアイテム全体を置換するため、他のプロセスが追記したフィールドを消去する。
auto_stopper と _record_experiment が競合する現状では `update_item` が唯一安全な選択肢。

### update_item に統一しない理由
`running` への遷移は「アイテムの初回作成」に相当する。
この時点ではアイテムが存在しない可能性があるため（`_claim` が失敗した場合など）、
`put_item` で確実に作成する。

## Consequences

- カードが 2 枚表示されるバグが解消される。
- `emergency_stop` / `emergency_stop_at` フィールドが実験終了後も DynamoDB に残り、
  evaluator から参照可能になる（ADR 038 の前提）。
- `_record_experiment` の呼び出し箇所で `experiment.started_at` が常に正しい値を持つことが前提となる。
  `_claim()` 後に `started_at` が設定されていない場合は `update_item` の Key に `now` を使う fallback を残す。
