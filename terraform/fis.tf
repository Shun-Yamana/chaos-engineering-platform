# FIS 実験テンプレート — node_failure (ADR 079, ADR 080)
# Chaos Mesh 移行後、FIS は AWSインフラ層（EC2ノード終了）専用
# pod_kill / cpu_stress / memory_stress / network_latency は Chaos Mesh に移行済み

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
# FIS 実験テンプレート — node_failure (ADR 080)
# EKS ノード 1 台を EC2 レベルで終了させ、K8s セルフヒーリングを検証
# ---------------------------------------------------------------------------

resource "aws_fis_experiment_template" "node_failure" {
  description = "EC2 node termination to test K8s pod rescheduling (ADR 080)"
  role_arn    = aws_iam_role.fis_execution.arn

  stop_condition {
    source = "aws:cloudwatch:alarm"
    value  = aws_cloudwatch_metric_alarm.service_b_5xx_rate[0].arn
  }

  target {
    name           = "nodes"
    resource_type  = "aws:ec2:instance"
    selection_mode = "COUNT(1)"

    resource_tag {
      key   = "eks:cluster-name"
      value = module.eks.cluster_name
    }
  }

  action {
    name      = "terminate-node"
    action_id = "aws:ec2:terminate-instances"

    target {
      key   = "Instances"
      value = "nodes"
    }
  }

  experiment_options {
    empty_target_resolution_mode = "fail"
  }

  log_configuration {
    log_schema_version = 2
    cloudwatch_logs_configuration {
      log_group_arn = "${aws_cloudwatch_log_group.fis.arn}:*"
    }
  }

  experiment_report_configuration {
    data_sources {
      cloudwatch_dashboard {
        dashboard_arn = aws_cloudwatch_dashboard.chaos_experiment.dashboard_arn
      }
    }

    outputs {
      s3_configuration {
        bucket_name = aws_s3_bucket.fis_reports.bucket
        prefix      = "node_failure"
      }
    }

    pre_experiment_duration  = "PT5M"
    post_experiment_duration = "PT5M"
  }

  tags = merge(local.common_tags, {
    Project = "chaos-platform"
  })
}

output "fis_template_node_failure_id" {
  description = "FIS experiment template ID for node_failure (EC2 node termination)"
  value       = aws_fis_experiment_template.node_failure.id
}
