# ADR 015 - API Gateway HTTP API 設計

- Status: Accepted
- Date: 2026-05-06

## Context

Chaos Engineering Platform の API Gateway HTTP API（aws_apigatewayv2）を Terraform で管理するにあたり、設定項目を精査してこのプロジェクトに適用すべき項目を決定する。

現状の実装は最小構成（ルーティングと Lambda 統合のみ）で、以下の問題がある：

- **認証なし**：エンドポイント URL を知っていれば誰でも実験を起動・停止できる
- **スロットリングなし**：連続投入による実験の誤爆を防ぐ手段がない
- **アクセスログなし**：誰がいつ実験を操作したかのトレーサビリティがない
- **timeout 未設定**：デフォルト依存でありバックエンドとの整合性が不明

## Decision

以下の 4 項目を追加し、その他の設定項目は適用しない。

### 適用する設定

| 設定 | 値 | 対象 |
|------|-----|------|
| `authorization_type` | `AWS_IAM` | 全ルート |
| `throttling_rate_limit` / `throttling_burst_limit` | 100 / 200 | default_route_settings |
| `access_log_settings` | CloudWatch JSON | $default ステージ |
| `timeout_milliseconds` | 29000 | Lambda 統合 |

### 適用しない設定

| 設定項目 | 外した理由 |
|---------|-----------|
| `cors_configuration` | CLI のみが呼ぶ。ブラウザクライアントなし |
| JWT 認証（Cognito） | ポートフォリオ規模では不要 |
| カスタムドメイン | execute-api URL で十分 |
| `mutual_tls_authentication` | CLI ↔ API 間に mTLS は過剰 |
| VPC link | Lambda は AWS マネージドサービスのみ呼ぶ。VPC 内リソース不要 |
| `request_models` / バリデーター | api_handler.py 側でバリデーションを行う |
| `stage_variables` | 環境は prod のみ |

## Rationale

### 認証に IAM を選んだ理由

選択肢は「なし・API キー・JWT（Cognito）・IAM」の 4 つ。

- **なし**：URL 漏洩で誰でも実験を操作できる。カオスエンジニアリング基盤として論外
- **API キー**：シンプルだが固定文字列の管理が雑になりやすく、rotate の仕組みが必要
- **JWT（Cognito）**：ユーザー管理が不要なのに Cognito を立てるのはオーバースペック
- **IAM**：CLI は AWS 認証情報（OIDC ロールまたは IAM ユーザー）を持つため SigV4 署名でリクエストを送れる。追加リソース不要で最も tight

### スロットリングに 100 RPS を選んだ理由

カオス実験は人間が意図的に起動するオペレーション。1秒間に 100 回以上の呼び出しは誤爆か攻撃であり、正常なユースケースではない。burst=200 は一時的なリトライを許容しつつ爆発的な呼び出しを防ぐ。

### timeout を 29000ms に設定した理由

Lambda の timeout=30s より 1s 短く設定する。これにより Lambda がタイムアウトするより先に API Gateway が接続を切ることを防ぎ、Lambda 側のエラーレスポンスをクライアントが受け取れる。デフォルト（29s）と同値だが意図を明示するために明記する。

## Consequences

- 全ルートに `authorization_type = "AWS_IAM"` を追加する ✅
- `aws_apigatewayv2_stage` に `default_route_settings`（throttling）を追加する ✅
- `aws_apigatewayv2_stage` に `access_log_settings` を追加する ✅
- `aws_apigatewayv2_integration` に `timeout_milliseconds = 29000` を明示する ✅
- CloudWatch ロググループ `/aws/apigateway/${project_name}` を追加する（保持 30 日）✅
- CLI 側で SigV4 署名が必要になる（`requests-aws4auth` または `boto3` 経由）
- IAM ロールに `execute-api:Invoke` 権限が必要（CLI 実行者のロール/ユーザーに付与）
