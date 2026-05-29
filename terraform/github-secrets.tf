# ---------------------------------------------------------------------------
# terraform apply 後に GitHub Secrets を自動同期
# リソース ID が変わる値（CloudFront distribution ID / API endpoint / Cognito client）を
# null_resource の triggers で検知し、gh CLI で Secrets を上書きする
# ---------------------------------------------------------------------------

resource "null_resource" "sync_github_secrets" {
  triggers = {
    frontend_distribution_id = aws_cloudfront_distribution.frontend.id
    api_endpoint             = aws_apigatewayv2_stage.default.invoke_url
    cognito_client_id        = aws_cognito_user_pool_client.this.id
  }

  provisioner "local-exec" {
    interpreter = ["PowerShell", "-Command"]
    command     = <<-EOT
      gh secret set FRONTEND_DISTRIBUTION_ID --body "${aws_cloudfront_distribution.frontend.id}" --repo ${var.github_repo}
      gh secret set API_ENDPOINT --body "${aws_apigatewayv2_stage.default.invoke_url}" --repo ${var.github_repo}
      gh secret set COGNITO_CLIENT_ID --body "${aws_cognito_user_pool_client.this.id}" --repo ${var.github_repo}
      Write-Host "GitHub Secrets synced."
    EOT
  }

  depends_on = [
    aws_cloudfront_distribution.frontend,
    aws_apigatewayv2_stage.default,
    aws_cognito_user_pool_client.this,
  ]
}
