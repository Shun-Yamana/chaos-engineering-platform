# ---------------------------------------------------------------------------
# Cognito — フロントエンド・CLI 認証 (ADR 018)
# ---------------------------------------------------------------------------

resource "aws_cognito_user_pool" "this" {
  name = "${var.project_name}-user-pool"

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  username_configuration {
    case_sensitive = false
  }

  password_policy {
    minimum_length                   = 12
    require_uppercase                = true
    require_lowercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 7
  }

  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  deletion_protection = "ACTIVE"

  tags = local.common_tags
}

resource "aws_cognito_user_pool_client" "this" {
  name         = "${var.project_name}-client"
  user_pool_id = aws_cognito_user_pool.this.id

  generate_secret = false

  explicit_auth_flows = [
    "ALLOW_USER_SRP_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
  ]

  allowed_oauth_flows                  = ["code"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = ["openid", "email"]
  supported_identity_providers         = ["COGNITO"]

  callback_urls = [
    "http://localhost:5173",
    "https://${aws_cloudfront_distribution.frontend.domain_name}",
  ]
  logout_urls = [
    "http://localhost:5173",
    "https://${aws_cloudfront_distribution.frontend.domain_name}",
  ]

  access_token_validity  = 1
  id_token_validity      = 1
  refresh_token_validity = 30

  token_validity_units {
    access_token  = "hours"
    id_token      = "hours"
    refresh_token = "days"
  }

  prevent_user_existence_errors = "ENABLED"
  enable_token_revocation       = true
}

# Hosted UI ドメイン（xxx.auth.ap-northeast-1.amazoncognito.com）
resource "aws_cognito_user_pool_domain" "this" {
  domain       = "${var.project_name}-${data.aws_caller_identity.current.account_id}"
  user_pool_id = aws_cognito_user_pool.this.id
}

# デモ用テストユーザー（招待メール送信なし）
resource "aws_cognito_user" "demo" {
  user_pool_id = aws_cognito_user_pool.this.id
  username     = "demo@chaos-platform.local"
  password     = var.cognito_test_user_password

  attributes = {
    email          = "demo@chaos-platform.local"
    email_verified = "true"
  }

  message_action = "SUPPRESS"
}

output "cognito_user_pool_id" {
  description = "Cognito User Pool ID"
  value       = aws_cognito_user_pool.this.id
}

output "cognito_client_id" {
  description = "Cognito User Pool Client ID (set as VITE_COGNITO_CLIENT_ID in frontend)"
  value       = aws_cognito_user_pool_client.this.id
}

output "cognito_hosted_ui_url" {
  description = "Cognito Hosted UI login URL"
  value       = "https://${aws_cognito_user_pool_domain.this.domain}.auth.${var.aws_region}.amazoncognito.com"
}
