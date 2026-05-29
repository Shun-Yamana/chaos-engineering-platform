# ADR 093 - chaos-agent 再起動時の孤立実験クリーンアップ

- Status: Accepted
- Date: 2026-05-29

## Context

chaos-agent Pod がデプロイ（ローリングアップデート）で入れ替わったとき、
実行中だった memory_stress 実験が `status: running` のまま DynamoDB に残り続ける問題が発生した。

`ChaosAgentPoller._poll()` は `pending` 実験のみをスキャンするため、
新しい Pod は `running` 状態の実験を認識せず、永久に完了しない。

加えて、Chaos Mesh の StressChaos CR も残存し続け、
デプロイで入れ替わった後の古いコンテナ ID（`container not found`）を参照し続けた。

## Decision

`ChaosAgentPoller.run_forever()` の起動直後に `_startup_cleanup()` を実行し、
DynamoDB に `running` で残っている実験を `stopped (agent_restart)` に遷移させ、
対応する Chaos Mesh CR があれば削除する。

## Rationale

### 孤立した running 実験を「再開」する選択肢を外した理由

実験スレッドの状態（`_interruptible_sleep` の残り時間、注入確認の結果）は
メモリ上にのみ存在し、再起動後に復元できない。
中途半端な状態で再開するとリカバリ確認やタイムアウト処理が壊れる。

### stopped に遷移させる選択肢を選んだ理由

- 実装がシンプルで副作用がない
- `stop_reason: agent_restart` を記録するためエバリュエーターが停止理由を識別できる
- Chaos Mesh CR を同時に削除することで障害注入の残留もクリアされる
- 同じ実験を再実行すればよく、ユーザーへの影響が最小

## Consequences

**対策できた問題**
- chaos-agent 再起動後に running 実験が永久に完了しない問題 → 解消
- デプロイで入れ替わった古い Pod を StressChaos が参照し続ける問題 → 起動時 CR 削除で解消

**残存リスク**
- 実験実行中にデプロイが走ると、実験は中断され次回起動時に `stopped` になる。
  実験中はデプロイを避ける運用が望ましい（CI/CD の `workflow_dispatch` を使って
  実験完了後にデプロイする手順を徹底する）。
- `_startup_cleanup()` の DynamoDB 更新は ConditionalExpression で `status == running`
  のみ対象とするため、二重起動による競合は発生しない。
