# ---------------------------------------------------------------------------
# CloudFront — service-b への外部アクセス (ADR 012: 外部アクセス設計)
#
# ALB は AWS Load Balancer Controller が k8s/ingress.yaml から自動作成するため
# Terraform では直接管理できない。ALB の DNS 名は kubectl get ingress で取得し
# var.alb_dns_name に渡す（2フェーズデプロイ）。
#
# 1st apply: EKS + LBC + K8s manifests で ALB 作成
# 2nd apply: alb_dns_name を指定して CloudFront を作成
# ---------------------------------------------------------------------------

variable "alb_dns_name" {
  description = "ALB DNS name created by AWS Load Balancer Controller (set after kubectl apply -f k8s/ingress.yaml)"
  type        = string
  default     = ""
}

locals {
  cloudfront_enabled = var.alb_dns_name != ""
}

resource "random_password" "cloudfront_origin_secret" {
  length  = 32
  special = false
}

resource "aws_cloudfront_distribution" "service_b" {
  count = local.cloudfront_enabled ? 1 : 0

  enabled         = true
  is_ipv6_enabled = true
  comment         = "${var.project_name} service-b"

  origin {
    domain_name = var.alb_dns_name
    origin_id   = "alb-service-b"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }

    # CloudFront → ALB 認証用カスタムヘッダー (ADR 012)
    custom_header {
      name  = "X-Origin-Verify"
      value = random_password.cloudfront_origin_secret.result
    }
  }

  default_cache_behavior {
    allowed_methods        = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods         = ["GET", "HEAD"]
    target_origin_id       = "alb-service-b"
    viewer_protocol_policy = "redirect-to-https"
    cache_policy_id        = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad" # CachingDisabled
    origin_request_policy_id = "216adef6-5c7f-47e4-b989-5492eafa07d3" # AllViewer
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }

  tags = local.common_tags
}

output "cloudfront_domain_name" {
  description = "CloudFront distribution domain name"
  value       = local.cloudfront_enabled ? aws_cloudfront_distribution.service_b[0].domain_name : "ALB not yet created — set alb_dns_name and re-apply"
}

output "alb_logs_bucket" {
  description = "S3 bucket name for ALB access logs (use in k8s/ingress.yaml)"
  value       = aws_s3_bucket.alb_logs.bucket
}

output "cloudfront_origin_secret" {
  description = "X-Origin-Verify header value (set in k8s/ingress.yaml CLOUDFRONT_ORIGIN_SECRET)"
  value       = random_password.cloudfront_origin_secret.result
  sensitive   = true
}
