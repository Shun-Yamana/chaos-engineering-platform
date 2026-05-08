# ADR 018 - Cognito 認証設計・API Gateway 認証変更

- Status: Accepted
- Date: 2026-05-09
- Amends: ADR 015

## Context

ADR 015 で API Gateway の認証を AWS_IAM（SigV4）に決定した。しかしその後、React SPA フロントエンド（`frontend/`）の存在が確認された。ブラウザは AWS SDK を持たないため SigV4 署名を実用的に行えず、前提が崩れた。

また、フロントエンドは `https://xxx.cloudfront.net` から `https://xxx.execute-api.amazonaws.com` を呼ぶため、CORS が必須になる（ADR 015 では「CLI のみ・CORS 不要」と判断していたが前提が変わった）。

認証方式の選択肢：

1. **IAM 認証（SigV4）**: ブラウザから使えない
2. **Lambda Authorizer（共有シークレット）**: シークレットが JS バンドルに埋め込まれ実質公開
3. **Cognito JWT**: ブラウザ標準の認証フロー。HTTP API に native サポートあり

## Decision

API Gateway の認証を **Cognito JWT** に変更し、CORS を追加する。
Cognito User Pool を新規作成し、管理者のみがユーザーを作成できる構成にする。

## Rationale

### IAM・Lambda Authorizer を外した理由

IAM はブラウザから使用不可。Lambda Authorizer で共有シークレットを使う方法はシークレットがフロントエンドのビルド成果物に含まれ、ブラウザの開発ツールで容易に確認できる。いずれもセキュアな選択肢ではない。

### Cognito を選んだ理由

HTTP API は `aws_apigatewayv2_authorizer` で JWT を native に検証できる（Lambda 不要）。SPA は Authorization Code + PKCE フローで安全にトークンを取得できる。CLI も `cognito-idp initiate-auth` でトークンを取得して同じエンドポイントを叩けるため、認証方式を統一できる。

### User Pool 設定の判断

| 設定 | 値 | 理由 |
|------|-----|------|
| `username_attributes` | `["email"]` | 標準的なログイン方式 |
| `admin_create_user_only` | `true` | カオス実験基盤への自己登録は不要 |
| `deletion_protection` | `"ACTIVE"` | 誤削除防止 |
| パスワードポリシー | min 12, 大小英数記号 | 標準的なポリシー |
| MFA | OFF | ポートフォリオ用途 |
| Lambda トリガー | スキップ | カスタムロジックなし |
| advanced_security_mode | スキップ | 有料機能 |
| Identity Pool | スキップ | JWT 認証のみで十分。IAM ロールへの変換不要 |

### Client 設定の判断

| 設定 | 値 | 理由 |
|------|-----|------|
| `generate_secret` | `false` | SPA はシークレット保持不可 |
| `explicit_auth_flows` | SRP + REFRESH のみ | SRP は安全。PASSWORD_AUTH は平文送信で非推奨 |
| OAuth flow | `code`（PKCE） | `implicit` は非推奨（トークンが URL に露出） |
| `prevent_user_existence_errors` | `ENABLED` | ユーザー存在の有無を攻撃者に漏らさない |
| `enable_token_revocation` | `true` | ログアウト時にリフレッシュトークンを無効化 |

## Consequences

- `terraform/cognito.tf` を新規作成する（User Pool・Client・Domain・テストユーザー）✅
- `terraform/api_gateway.tf` を更新する（JWT Authorizer・ルート変更・CORS 追加）✅
- `terraform/variables.tf` に `frontend_url`・`cognito_test_user_password` を追加する ✅
- `terraform/terraform.tfvars.example` を更新する ✅
- CLI の認証フローを `cognito-idp initiate-auth`（SRP）→ Bearer トークン送信に変更する必要がある
- Cognito Hosted UI の callback URL に `var.frontend_url` を設定するため、CloudFront 作成後に `terraform apply` が必要
