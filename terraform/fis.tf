# FIS 実験テンプレート — network_latency (ADR 009, ADR 011 item 4, ADR 022)
# chaos/agent.py が FIS_TEMPLATE_SERVICE_A / FIS_TEMPLATE_SERVICE_B 環境変数で参照する

locals {
  fis_services = toset(["service-a", "service-b"])
}

# ---------------------------------------------------------------------------
# FIS 実験レポート出力先 S3 バケット (ADR 022)
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "fis_reports" {
  bucket = "${var.project_name}-fis-reports-${data.aws_caller_identity.current.account_id}"
  tags   = local.common_tags
}

resource "aws_s3_bucket_lifecycle_configuration" "fis_reports" {
  bucket = aws_s3_bucket.fis_reports.id

  rule {
    id     = "expire-90-days"
    status = "Enabled"
    filter {}
    expiration {
      days = 90
    }
  }
}

resource "aws_s3_bucket_public_access_block" "fis_reports" {
  bucket = aws_s3_bucket.fis_reports.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ---------------------------------------------------------------------------
# FIS 実験テンプレート
# ---------------------------------------------------------------------------

resource "aws_fis_experiment_template" "network_latency" {
  for_each    = local.fis_services
  description = "Network latency injection for ${each.key} (aws:eks:pod-network-latency)"
  role_arn    = aws_iam_role.fis_execution.arn

  stop_condition {
    source = "aws:cloudwatch:alarm"
    value  = aws_cloudwatch_metric_alarm.service_b_5xx_rate[0].arn
  }

  target {
    name           = "pods"
    resource_type  = "aws:eks:pod"
    selection_mode = "ALL"

    parameters = {
      clusterIdentifier = module.eks.cluster_name
      namespace         = "default"
      selectorType      = "labelSelector"
      selectorValue     = "app=${each.key}"
    }
  }

  action {
    name      = "inject-network-latency"
    action_id = "aws:eks:pod-network-latency"

    parameter {
      key   = "duration"
      value = "PT300S"
    }

    parameter {
      key   = "delayMilliseconds"
      value = "500"
    }

    parameter {
      key   = "kubernetesServiceAccount"
      value = "default"
    }

    target {
      key   = "Pods"
      value = "pods"
    }
  }

  experiment_options {
    empty_target_resolution_mode = "fail"
  }

  # 既存の /aws/fis/${var.project_name} ロググループに接続（ARN 末尾の :* は FIS の要件）
  log_configuration {
    log_schema_version = 2
    cloudwatch_logs_configuration {
      log_group_arn = "${aws_cloudwatch_log_group.fis.arn}:*"
    }
  }

  # 実験前後 5 分の CloudWatch メトリクスを S3 へ自動レポート出力
  experiment_report_configuration {
    data_sources {
      cloudwatch_dashboard {
        dashboard_arn = aws_cloudwatch_dashboard.chaos_experiment.dashboard_arn
      }
    }

    outputs {
      s3_configuration {
        bucket_name = aws_s3_bucket.fis_reports.bucket
        prefix      = each.key
      }
    }

    pre_experiment_duration  = "PT5M"
    post_experiment_duration = "PT5M"
  }

  tags = merge(local.common_tags, {
    Project = "chaos-platform"
  })
}

output "fis_template_service_a_id" {
  description = "FIS experiment template ID for service-a network_latency"
  value       = aws_fis_experiment_template.network_latency["service-a"].id
}

output "fis_template_service_b_id" {
  description = "FIS experiment template ID for service-b network_latency"
  value       = aws_fis_experiment_template.network_latency["service-b"].id
}
