# ADR 043 - 実験評価結果の Slack レポート通知

- Status: Accepted
- Date: 2026-05-16

## Context

実験が完了し evaluator が PASS/FAIL を確定した時点で、結果をオペレーターに通知したい。
既存の SNS トピックには Slack webhook サブスクリプションが設定されているが、
SNS の HTTPS サブスクリプションは SNS エンベロープ（Type/MessageId/Message など）をそのまま送るため、
Slack には生 JSON が届き可読性が低い。

## Decision

`experiment_evaluator` が `write_evaluation` 完了後に Slack webhook へ直接 POST する。
メッセージフォーマットは Slack Block Kit（attachments + blocks）を使い、
PASS は緑・FAIL は赤のサイドバーカラーで視覚的に判別できるようにする。

### 表示内容
- ヘッダー: ✅ PASS / ❌ FAIL + 実験名
- フィールド: Fault タイプ・対象サービス・実験時間・実験 ID
- Phase A — Absorb/Contain: 各 criterion の値・閾値・✅❌
- Phase B — Recovery/TTR: 各 criterion の値・閾値・TTR 実績/上限
- Safety Net: auto_stopper 発動状況と期待値
- フッター: 評価完了時刻

## Rationale

### SNS 経由にしない理由
SNS HTTPS サブスクリプションは SNS 独自のエンベロープを送る。
Slack webhook はそのフォーマットを解析しないため、メッセージが生 JSON テキストとして表示される。
evaluator から直接 POST することで Block Kit の書式を完全に制御できる。

### 既存 SNS トピックを使わない理由
CloudWatch アラーム → SNS → Slack の経路は引き続き使用する（インフラ異常の通知用）。
実験評価レポートは別のチャンネルやフォーマットで受け取りたい場合に備え、
URL を変数で差し替えられる設計とする。現時点では同じ webhook URL を使う。

### urllib.request を使う理由
Lambda レイヤーなし・依存ゼロで HTTP POST できる。`requests` を追加する必要がない。

## Consequences

- 実験完了から約 5 分後（CloudWatch バッファ待ち完了後）に Slack 通知が届く。
- `SLACK_WEBHOOK_URL` が未設定の場合は通知をスキップし、Lambda の失敗扱いにはしない。
- Slack 通知が失敗しても評価結果は DynamoDB に書き込み済みのため、データロストは発生しない。
- evaluator Lambda の Terraform 再適用が必要（env var 追加）。
