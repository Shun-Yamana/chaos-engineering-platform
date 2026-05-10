# ADR 020 - SNS トピック設計（暗号化・アクセス制御・可観測性・配信ポリシー）

- Status: Accepted
- Date: 2026-05-10

## Context

`chaos_alerts` SNS トピックは「CloudWatch Alarm → SNS → Slack webhook / auto_stopper Lambda」という経路でカオス実験アラートを配信する中核コンポーネントである。

当初の実装は `name` と `tags` のみで、以下の課題があった。

- **セキュリティ**: リソースポリシーが未設定で、同一アカウント内の任意のリソースが Publish できるデフォルト動作に依存していた
- **可観測性**: Lambda・HTTPS（Slack）両サブスクリプションの配信失敗が SNS レベルで無音で消えていた
- **配信信頼性**: Slack 一時ダウン時のリトライが SNS デフォルト（3回）のみだった
- **暗号化**: 保存時暗号化が未設定だった

`aws_sns_topic` の全設定項目をレビューし、このプロジェクトで設定すべき項目を選定した。

## Decision

以下 4 項目を `aws_sns_topic.chaos_alerts` に追加する。

1. `kms_master_key_id = "alias/aws/sns"` — 保存時暗号化（AWS 管理キー）
2. `aws_sns_topic_policy` — CloudWatch Alarm と auto_stopper Lambda のみに Publish を制限
3. `lambda_failure_feedback_role_arn` / `http_failure_feedback_role_arn` — 配信失敗を CloudWatch Logs に記録
4. `delivery_policy` — Slack への粘り強いリトライ（100 回、最長約 6 時間）

## Rationale

### FIFO トピックを外した理由

HTTPS サブスクリプション（Slack webhook）と非互換のため採用不可。

### `display_name` / `data_protection_policy` を外した理由

SMS 未使用のため `display_name` は不要。アラートペイロードに PII を含まないため `data_protection_policy` も不要。

### KMS 暗号化（`alias/aws/sns`）を選んだ理由

AWS 管理キーを使えば追加コストゼロ・KMS ポリシー管理不要でセキュリティポスチャを向上できる。`alias/aws/sns` は同一アカウントの AWS サービス（CloudWatch 等）からの Publish を自動許可するため、既存の動作を壊さない。

### トピックポリシーを選んだ理由

リソースポリシーなしはデフォルト動作（アカウントオーナーが全権限）に依存しており最小権限原則に反する。Publish を許可するプリンシパルを以下に明示的に絞る。

- `cloudwatch.amazonaws.com`（`aws:SourceArn` でアカウント内アラームのみに限定）
- `aws_iam_role.lambda_auto_stopper`（実験停止後の二次通知用）

### フィードバックログを選んだ理由

Slack や Lambda への配信失敗は SNS レベルでは CloudWatch Logs に記録されず、DLQ もないため障害時の原因調査が困難だった。`lambda_failure_feedback_role_arn` と `http_failure_feedback_role_arn` に共通の IAM ロール（`sns_feedback`）を割り当て、失敗イベントを CloudWatch Logs に記録する。成功ログはノイジーになるため有効化しない。

### 粘り強い delivery_policy を選んだ理由

カオス実験アラートは確実に届けることが最重要で、Slack の一時的なダウンやメンテナンスで通知が欠落することは許容できない。以下のリトライ設計で最長約 6 時間粘る。

| フェーズ | 回数 | 間隔 | 合計待機 |
|---|---|---|---|
| 固定（最小遅延） | 3 回 | 20 s | 60 s |
| 指数バックオフ | 27 回 | 20 s → 300 s | 〜数十分 |
| 固定（最大遅延） | 70 回 | 300 s | 〜5.8 時間 |

## Consequences

- SNS フィードバックログ用の CloudWatch Logs ロググループが自動生成される（ロググループ名は AWS が決定）
- `delivery_policy` はトピック全体に適用される。サブスクリプション単位で上書きしたい場合は `disableSubscriptionOverrides = false` のまま `aws_sns_topic_subscription` 側で設定する
- 70 回 × 300 s のリトライが Slack のレート制限（Tier 1: 1 req/s）に抵触しないか注意。アラーム数が増えた場合は `numMaxDelayRetries` を調整する
- `aws_sns_topic_policy` は `aws_sns_topic` の `policy` 引数と排他のため、将来インラインで書き直す場合はリソースを削除してから変更する
