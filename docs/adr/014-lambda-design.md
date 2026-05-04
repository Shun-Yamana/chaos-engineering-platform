# ADR 014 - Lambda 関数設計（sli_calculator / auto_stopper / api_handler）

- Status: Accepted
- Date: 2026-05-04

## Context

Chaos Engineering Platform の Lambda 関数（sli_calculator・auto_stopper・api_handler）を Terraform で管理するにあたり、`aws_lambda_function` リソースの全設定項目を精査し、このプロジェクトに適用すべき項目を決定する。

各 Lambda の役割：

| 関数 | トリガー | 主な処理 |
|------|---------|---------|
| sli_calculator | EventBridge（毎分） | CloudWatch から SLI を計算し DynamoDB に保存 |
| auto_stopper | EventBridge（毎分）+ SNS（Alarm 即時） | SLO 違反を検知して実験を停止 |
| api_handler | API Gateway HTTP API | 実験の CRUD を DynamoDB に書く |

また、api_gateway.tf に `CHAOS_AGENT_FUNCTION` の誤設定バグが存在する。chaos-agent は DynamoDB ポーリング方式に変更済み（ADR 012）であるが、古い Lambda 直接呼び出しのコードが残っていた。

## Decision

**以下の設定項目を全 Lambda に適用し、適用しない項目はその理由を記録する。**

### 適用する設定

| 設定項目 | sli_calculator | auto_stopper | api_handler | 理由 |
|---------|---------------|-------------|-------------|------|
| `memory_size` | 256 MB | 128 MB | 256 MB | 関数の処理量に応じて設定 |
| `architectures` | arm64 | arm64 | arm64 | x86_64 比 約 20% コスト削減 |
| `tracing_config` | Active | Active | Active | X-Ray によるリクエストトレース |
| `dead_letter_config` | 共有 DLQ | 共有 DLQ | 共有 DLQ | 処理失敗イベントの退避 |
| `logging_config` | JSON / 専用 LG | JSON / 専用 LG | JSON / 専用 LG | Logs Insights クエリ対応 |

### 適用しない設定と理由

| 設定項目 | 外した理由 |
|---------|-----------|
| `vpc_config` | Lambda は DynamoDB・SNS・CloudWatch のみ呼ぶ。VPC 内リソースへのアクセス不要 |
| `ephemeral_storage` | デフォルト 512 MB で十分。ファイル I/O なし |
| `reserved_concurrent_executions` | sli_calculator は毎分 1 回で競合しない。api_handler はスケール制限不要 |
| `kms_key_arn` | 環境変数に機密情報なし（テーブル名・プロジェクト名のみ） |
| `layers` | 標準ライブラリ（boto3）のみ使用 |
| `file_system_config` | EFS 不要 |
| `snap_start` | Python 非対応（Java のみ） |
| `code_signing_config_arn` | ポートフォリオ用途では不要 |

### バグ修正

api_gateway.tf から以下を削除する：
- `CHAOS_AGENT_FUNCTION` 環境変数（sli_calculator の ARN が誤って設定されていた）
- IAM ポリシーの `lambda:InvokeFunction` 権限（chaos-agent への直接呼び出しは廃止済み）

## Rationale

### memory_size を関数ごとに変えた理由

sli_calculator は CloudWatch API（GetMetricStatistics）を複数サービス × 複数メトリクス分呼ぶため、256 MB でスロットリングのマージンを確保する。auto_stopper は DynamoDB Scan と SNS Publish のみで処理が軽いため 128 MB で十分。api_handler はユーザー向けのため低レイテンシを優先し 256 MB を割り当てる。

### arm64 を選んだ理由

Python ランタイムは arm64（Graviton）と x86_64 でパフォーマンス差がほぼない。Graviton は同メモリ構成で約 20% 安く、このプロジェクトのような高頻度（毎分）実行関数ではコスト差が累積する。

### DLQ に SQS を選んだ理由

Lambda の失敗イベントを退避するための選択肢は SQS と SNS。SQS は可視性タイムアウトによる再処理制御が可能で、失敗したイベントを後から調査・再投入できる。特に auto_stopper の SNS トリガーで失敗すると実験が停止されないため、DLQ による捕捉が重要。3 関数で共有 DLQ 1 つを使いまわすことでポートフォリオ規模のコストを最小化する。

### vpc_config を外した理由

Lambda を VPC 内に置くと Cold Start が増加し、かつ NAT Gateway 経由でないと AWS API に到達できない。今回の Lambda は VPC 内リソース（RDS 等）へのアクセスが不要であり、VPC 外から直接 DynamoDB・SNS・CloudWatch を呼ぶ構成が合理的。

### sli_calculator のメトリクスソースを ALB に切り替えた理由

当初 `ContainerInsights` の `pod_network_rx_bytes` でリクエスト数を代替し、カスタム名前空間 `{PROJECT_NAME}/SLI` から 5xx カウントを取得する設計だった。しかしカスタムメトリクスを書き込む仕組みが存在せず、実際には機能しない実装だった。

ALB は `AWS/ApplicationELB` 名前空間に `HTTPCode_Target_5XX_Count` と `RequestCount` を標準で出力するため、追加実装なしでエラーレートを計算できる。service-a は ALB を持たないが、service-a の障害は service-b のエラーレート上昇として観測できるため、service-b の ALB メトリクスのみで E2E の健全性を監視する。

### auto_stopper の SNS イベント対応を追加した理由

当初 EventBridge（毎分ポーリング）のみを想定していたが、SNS（CloudWatch Alarm 発火）からも呼ばれるようになった（ADR 012）。SNS イベントの `NewStateValue` が `"ALARM"` でない場合（OK 回復通知など）は早期リターンし、ALARM 時のみ既存の停止ロジックを実行する。

## Consequences

- SQS DLQ リソースを lambda.tf に追加する ✅
- 全 Lambda ロールに `AWSXRayDaemonWriteAccess` ポリシーアタッチメントを追加する ✅
- 3 つの CloudWatch ロググループ（保持期間 30 日）を cloudwatch.tf に追加する ✅
- api_gateway.tf の `CHAOS_AGENT_FUNCTION` 環境変数と対応 IAM を削除する ✅
- DLQ への `sqs:SendMessage` 権限を全 Lambda ロールに追加する ✅
- sli_calculator のメトリクスソースを `AWS/ApplicationELB` に切り替え、`ALB_ARN_SUFFIX` 環境変数を追加する ✅
- auto_stopper に SNS イベントハンドリングを追加する ✅
- `ALB_ARN_SUFFIX` は ALB 作成後（kubectl apply ingress 後）に `terraform apply` で反映する
