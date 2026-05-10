# ADR 022 - FIS 実験テンプレート設計（ログ・停止条件・レポート）

- Status: Accepted
- Date: 2026-05-11

## Context

`aws_fis_experiment_template` の全設定項目をレビューした結果、当初の `network_latency` テンプレートは最小構成（description・role_arn・stop_condition/none・target・action）のみで、以下の課題があった。

- **ログ未記録**: `/aws/fis/${project_name}` ロググループは `cloudwatch.tf` に作成済みだが、FIS テンプレートに `log_configuration` が接続されておらず、実験ログがどこにも残らなかった
- **空ターゲット誤成功**: `empty_target_resolution_mode` がデフォルト `skip` のため、Pod が存在しない状態でも実験が「成功」として完了してしまう
- **停止条件の単一障害点**: `stop_condition = none` のため、実験停止は auto_stopper Lambda のみに依存していた。Lambda チェーン障害時のフェイルセーフがなかった
- **実験結果の可視化不足**: 実験前後のメトリクス変化を手動で CloudWatch を確認する必要があり、ポートフォリオのデモ・説明コストが高かった

## Decision

以下 4 項目を追加する。あわせて FIS レポート用リソース（S3 バケット・CloudWatch ダッシュボード）を新設する。

| 追加項目 | 内容 |
|---|---|
| `log_configuration` | 既存の `/aws/fis/${project_name}` ロググループに接続 |
| `experiment_options` | `empty_target_resolution_mode = "fail"` |
| `stop_condition` | `alb_arn_suffix` 設定済みなら CloudWatch Alarm で直接停止、未設定なら `none` |
| `experiment_report_configuration` | 実験前後 5 分の CloudWatch ダッシュボードを S3 に自動出力 |

## Rationale

### `log_configuration` を選んだ理由

ロググループはすでに存在していた。接続コストがゼロで、実験ログ（アクション開始・完了・エラー）を CloudWatch Logs に残すことができる。ARN 末尾の `:*` は FIS のログ配信 API の仕様上必須。

### `empty_target_resolution_mode = "fail"` を選んだ理由

デフォルト `skip` では Namespace や Label のタイプミスで実験がゼロ Pod に適用され、「障害注入なし」で合格判定が出てしまう。`fail` にすることで空ターゲットを即座にエラーにし、設定ミスを実験前に検出できる。

### `stop_condition` を alarm ベースに切り替えた理由

現行の停止フロー「CloudWatch Alarm → SNS → Lambda → FIS StopExperiment」は Lambda の失敗やタイムアウトで停止が遅延するリスクがある。`stop_condition = aws:cloudwatch:alarm` で FIS が直接アラームを監視することで、Lambda チェーンに依存しない即時停止が実現できる。`alb_arn_suffix` 未設定時（ALB 未作成）は条件が成立しないため、`none` にフォールバックする dynamic ブロックで対応する。

### `experiment_report_configuration` を選んだ理由

FIS が実験前後の CloudWatch メトリクスを自動収集し S3 に PDF/JSON レポートとして出力する機能。手動でメトリクス確認する作業が不要になり、実験の「before/after が1ファイルで見える」状態になる。ポートフォリオのデモや面接説明での訴求力が高い。

#### `pre/post_experiment_duration = "PT5M"` にした理由

デフォルトの PT20M（20分）は実験時間（数分）に対して過剰。実験前後 5 分あれば安定状態と障害状態の差分を十分に捉えられる。ストレージコストも抑えられる。

### `account_targeting` / `selection_mode` を変更しない理由

- `account_targeting`: シングルアカウント構成のためデフォルト（single-account）で十分
- `selection_mode = "ALL"`: 全 Pod に注入して最悪ケースを検証するのが目的のため変更不要

## Consequences

- FIS 実行ロールに `logs:*`（Delivery 系）・`cloudwatch:GetDashboard`・`s3:PutObject` の追加権限が必要。`CloudWatchLogsDelivery` Sid はアカウントレベル API のため `Resource = "*"` が必要（AWS 仕様上避けられない）
- `stop_condition` は `alb_arn_suffix` の有無で動的に切り替わるため、ALB プロビジョニング前後で `terraform apply` を2回実行するとテンプレートが再作成される
- FIS レポート S3 バケットは 90 日ライフサイクルを設定。実験頻度が増えた場合はコストを再評価する
- CloudWatch ダッシュボードのウィジェットは `var.alb_arn_suffix` を使用するため、ALB 未作成時は "Insufficient data" が表示されるが、レポート生成自体には影響しない
