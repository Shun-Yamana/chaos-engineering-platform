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

# ---------------------------------------------------------------------------
# 共有セキュリティヘッダーポリシー — frontend + service-b 両 distribution で使用 (ADR 017)
# ---------------------------------------------------------------------------

resource "aws_cloudfront_response_headers_policy" "security_headers" {
  name = "${var.project_name}-security-headers"

  security_headers_config {
    strict_transport_security {
      access_control_max_age_sec = 31536000
      include_subdomains         = true
      preload                    = true
      override                   = true
    }
    content_type_options {
      override = true
    }
    frame_options {
      frame_option = "DENY"
      override     = true
    }
    referrer_policy {
      referrer_policy = "strict-origin-when-cross-origin"
      override        = true
    }
  }
}

locals {
  # ALB は data.aws_lb.service_b で常に自動検出されるため常に有効
  cloudfront_enabled = true
}

resource "random_password" "cloudfront_origin_secret" {
  length  = 32
  special = false
}

resource "aws_cloudfront_distribution" "service_b" {
  count = 1

  enabled         = true
  is_ipv6_enabled = true
  http_version    = "http2and3"
  price_class     = "PriceClass_200"
  comment         = "${var.project_name} service-b"

  origin {
    domain_name = data.aws_lb.service_b.dns_name
    origin_id   = "alb-service-b"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
      origin_read_timeout    = 30
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
    viewer_protocol_policy     = "redirect-to-https"
    compress                   = true
    cache_policy_id            = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad" # CachingDisabled
    origin_request_policy_id   = "216adef6-5c7f-47e4-b989-5492eafa07d3" # AllViewer
    response_headers_policy_id = aws_cloudfront_response_headers_policy.security_headers.id
  }

  # http_error_inject 実験: origin 5xx をそのまま見せず branded 503 fallback を返す
  # 503 を維持するため CloudFront TotalErrorRate は下がらないが、raw 5xx の直接露出を防ぐ
  dynamic "custom_error_response" {
    for_each = [502, 503, 504]
    content {
      error_code            = custom_error_response.value
      response_code         = 503
      error_caching_min_ttl = 5
    }
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
  value       = aws_cloudfront_distribution.service_b[0].domain_name
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
