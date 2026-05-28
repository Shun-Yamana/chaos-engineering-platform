# terraform apply 後にフロントエンドを再ビルド・デプロイするスクリプト
# Usage: .\scripts\deploy-frontend.ps1

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$REPO_ROOT = Split-Path $PSScriptRoot -Parent
$TF_DIR    = Join-Path $REPO_ROOT "terraform"
$FE_DIR    = Join-Path $REPO_ROOT "frontend"

Write-Host "==> terraform output を取得中..."
Push-Location $TF_DIR
$apiEndpoint     = terraform output -raw api_endpoint
$cognitoDomain   = terraform output -raw cognito_hosted_ui_url
$cognitoClientId = terraform output -raw cognito_client_id
$frontendBucket  = terraform output -raw frontend_bucket
$cfDistId        = terraform output -raw frontend_distribution_id
$frontendUrl     = terraform output -raw frontend_url
Pop-Location

# api_endpoint は末尾スラッシュがつくので除去
$apiEndpoint = $apiEndpoint.TrimEnd("/")

Write-Host "==> .env.production を更新中..."
@"
VITE_API_ENDPOINT=$apiEndpoint
VITE_COGNITO_DOMAIN=$cognitoDomain
VITE_COGNITO_CLIENT_ID=$cognitoClientId
VITE_DEV_AUTH=
VITE_SERVICE_A_ENDPOINT=
"@ | Set-Content (Join-Path $FE_DIR ".env.production") -Encoding UTF8

Write-Host "==> npm run build..."
Push-Location $FE_DIR
npm run build
Pop-Location

Write-Host "==> S3 sync..."
aws s3 sync "$FE_DIR\dist" "s3://$frontendBucket/" --delete

Write-Host "==> CloudFront キャッシュ無効化..."
aws cloudfront create-invalidation --distribution-id $cfDistId --paths "/*" | Out-Null

Write-Host "==> 完了"
Write-Host "URL: $frontendUrl"
