# ADR 023 - フロントエンド・バックエンド統合設計（認証・API接続・ローカル開発）

- Status: Accepted
- Date: 2026-05-12

## Context

React SPA（CloudFront/S3）と API Gateway HTTP API（v2）を接続するにあたり、以下の3つの課題があった。

1. **Lambda イベント形式の不一致**: Terraform で `payload_format_version = "2.0"` を設定しているにも関わらず、Lambda ハンドラが API Gateway v1 形式のフィールド（`httpMethod`, `path`）を参照していた。v2 では `requestContext.http.method` / `rawPath` を使うため、全リクエストが 404 になる。
2. **認証トークン未送信**: API Gateway の全ルートに Cognito JWT Authorizer を設定しているが、フロントエンドが `Authorization` ヘッダーを送っていなかった（全リクエストが 401 になる）。
3. **ローカル開発環境**: `VITE_API_ENDPOINT` が未設定の場合、`api.ts` は相対パスにリクエストを投げるが、Vite dev server はそれを処理できない。加えて API Gateway の CORS 許可オリジンは CloudFront URL のみのため、ブラウザから直接 API Gateway を叩くと CORS エラーになる。

## Decision

1. **Lambda**: v2 形式（`requestContext.http.method` / `rawPath`）を優先しつつ v1 フィールドにフォールバックする形でハンドラを修正する。
2. **認証**: Cognito PKCE フロー（OAuth2 Authorization Code + PKCE）をライブラリなしで実装し、取得した `access_token` を `Authorization: Bearer` ヘッダーで送信する。
3. **ローカル開発**: Vite の `server.proxy` で `/experiments` を API Gateway に転送する。`VITE_DEV_API_URL` 環境変数で転送先を制御する。

## Rationale

### Lambda v1 フォールバックを残した理由

完全に v2 専用にしてもよいが、将来的に REST API（v1）へ移行した際の保険として両対応にした。コストはゼロで可読性も損なわない。

### PKCE を SRP（`amazon-cognito-identity-js`）より選んだ理由

SRP 認証は `amazon-cognito-identity-js`（≈200KB）が必要で、フロントエンドでパスワードを扱う。PKCE は Web Crypto API（ブラウザ標準）のみで実装でき、パスワードはフロントエンドを通らず Cognito Hosted UI で処理される。ポートフォリオとして「業界標準の認証フロー」を示せる点でも優れる。

### Cognito Hosted UI を使った理由（SPA 組み込みログインフォームを外した理由）

パスワード処理をフロントエンドコードから完全に排除できる。Hosted UI は Cognito が管理するため、MFA 追加や IdP 連携も将来的にフロントエンドを変更せずに対応できる。

### Vite proxy を選んだ理由（`VITE_API_ENDPOINT` に直接 URL を書く方法を外した理由）

`VITE_API_ENDPOINT` に API Gateway URL を書く方法だと、CORS（API Gateway が許可するオリジンは CloudFront のみ）の問題がブラウザ側で発生する。Vite proxy はサーバーサイドで転送するため CORS をバイパスできる。また本番ビルドとローカル開発で `api.ts` のコードを変えずに済む。

## Consequences

- ローカル開発には `.env.local` に `VITE_COGNITO_DOMAIN`, `VITE_COGNITO_CLIENT_ID`, `VITE_DEV_API_URL` の3変数が必要。`terraform output` から取得する。
- Cognito の `callback_urls` / `logout_urls` に `http://localhost:5173` を追加したため、`terraform apply` が必要。
- `access_token` は `sessionStorage` に保存するためタブを閉じるとログアウトされる（意図的: ポートフォリオ用のシンプルな設計）。永続化が必要な場合は `localStorage` + トークンリフレッシュの実装が必要。
- DynamoDB resource API の `FilterExpression` は文字列形式ではなく `Attr` オブジェクトを使う必要があるため、全 scan 操作を修正した。
