# ADR 017 - CloudFront フロントエンド配信設計

- Status: Accepted
- Date: 2026-05-08

## Context

Chaos Engineering Platform には React + Vite の SPA フロントエンド（`frontend/`）が存在し、実験の起動・停止・一覧を GUI で操作できる。このフロントエンドを静的ファイルとして S3 + CloudFront で配信する構成を決定する。

既存の CloudFront distribution（ADR 012）は service-b（ALB 経由）への外部アクセス用であり、以下の点でフロントエンド配信とは根本的に異なる：

- **キャッシュポリシー**: service-b は API なので CachingDisabled。フロントエンドは静的ファイルなので CachingOptimized
- **エラーレスポンス**: service-b は API の HTTP エラーをそのまま返す。SPA は 404/403 を index.html に書き換えてクライアントサイドルーティングに委ねる
- **オリジン**: service-b は ALB。フロントエンドは S3

## Decision

service-b 用 distribution とは**別の distribution を作成**し、S3 + OAC でフロントエンドを配信する。
あわせて既存の service-b distribution に未設定だった共通設定（price_class・http_version・compress・response_headers_policy）を追加する。

## Rationale

### 同一 distribution にまとめなかった理由

path_pattern で `/` と `/api/*` を振り分ける構成も検討したが、以下の問題がある：
- service-b は API。CloudFront 経由でルーティングしても利点がない（既に別 distribution がある）
- キャッシュポリシーやエラーレスポンスの設定が混在して複雑になる
- 障害発生時の切り分けが難しくなる

用途が異なる 2 つのオリジンは distribution を分けて管理するのが合理的。

### OAC を選んだ理由

S3 オリジンへの CloudFront 認証は OAI（旧方式）と OAC（推奨）の 2 択。
OAC は SigV4 署名を使い、S3 バケットポリシーで `AWS:SourceArn` 条件による制御が可能。OAI は非推奨のため OAC を採用。

### CachingOptimized を選んだ理由

Vite ビルド出力は `assets/` 以下のファイルにコンテンツハッシュが付く（例: `main-abc123.js`）。
ファイル内容が変わればファイル名も変わるため、長期キャッシュが安全。CloudFront Invalidation で `/*` を無効化することでデプロイ時に確実に更新される。

### response_headers_policy を両 distribution に適用した理由

HSTS・X-Frame-Options・X-Content-Type-Options は、フロントエンドと service-b の両方に設定すべきセキュリティヘッダー。共有リソースとして 1 つ作成して両方から参照する。

## Consequences

- `terraform/frontend.tf` を新規作成する（S3・OAC・distribution・バケットポリシー）✅
- `terraform/cloudfront.tf` の service-b distribution に price_class・http_version・compress・origin_read_timeout・response_headers_policy を追加する ✅
- `aws_cloudfront_response_headers_policy` を共有リソースとして cloudfront.tf に作成する ✅
- `.github/workflows/deploy.yml` にフロントエンドビルド・S3 デプロイ・キャッシュ無効化を追加する ✅
- `terraform/outputs.tf` に frontend_url・frontend_bucket・frontend_distribution_id を追加する ✅
- デプロイ後に GitHub Secrets に `FRONTEND_BUCKET` と `FRONTEND_DISTRIBUTION_ID` を設定する必要がある
