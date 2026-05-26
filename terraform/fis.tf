# FIS 実験テンプレート — network_latency (ADR 009, ADR 011 item 4, ADR 022)
# chaos/agent.py が FIS_TEMPLATE_SERVICE_A / FIS_TEMPLATE_SERVICE_B 環境変数で参照する

locals {
  fis_services       = toset(["service-a", "service-b"])          # network_latency 用（将来予約）
  fis_fault_services = toset(["service-b", "service-c"])          # pod_kill / cpu_stress / memory_stress
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

# ---------------------------------------------------------------------------
# FIS 実験テンプレート — pod_kill (ADR 054)
# ---------------------------------------------------------------------------

resource "aws_fis_experiment_template" "pod_kill" {
  for_each    = local.fis_fault_services
  description = "Pod kill injection for ${each.key} (aws:eks:pod-delete)"
  role_arn    = aws_iam_role.fis_execution.arn

  stop_condition {
    source = "aws:cloudwatch:alarm"
    value  = aws_cloudwatch_metric_alarm.service_b_5xx_rate[0].arn
  }

  target {
    name           = "pods"
    resource_type  = "aws:eks:pod"
    selection_mode = "COUNT(1)"

    parameters = {
      clusterIdentifier = module.eks.cluster_name
      namespace         = "default"
      selectorType      = "labelSelector"
      selectorValue     = "app=${each.key}"
    }
  }

  action {
    name      = "kill-pod"
    action_id = "aws:eks:pod-delete"

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
        prefix      = "pod_kill/${each.key}"
      }
    }

    pre_experiment_duration  = "PT5M"
    post_experiment_duration = "PT5M"
  }

  tags = merge(local.common_tags, {
    Project = "chaos-platform"
  })
}

# ---------------------------------------------------------------------------
# FIS 実験テンプレート — cpu_stress (ADR 054)
# ---------------------------------------------------------------------------

resource "aws_fis_experiment_template" "cpu_stress" {
  for_each    = local.fis_fault_services
  description = "CPU stress injection for ${each.key} (aws:eks:pod-cpu-stress)"
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
    name      = "inject-cpu-stress"
    action_id = "aws:eks:pod-cpu-stress"

    parameter {
      key   = "percent"
      value = "80"
    }

    parameter {
      key   = "duration"
      value = "PT300S"
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
        prefix      = "cpu_stress/${each.key}"
      }
    }

    pre_experiment_duration  = "PT5M"
    post_experiment_duration = "PT5M"
  }

  tags = merge(local.common_tags, {
    Project = "chaos-platform"
  })
}

# ---------------------------------------------------------------------------
# SSM ドキュメント — memory_stress (ADR 078)
# EC2 ノード上で service-b の cgroup に直接メモリストレスを注入する
# ---------------------------------------------------------------------------

resource "aws_ssm_document" "memory_stress" {
  name          = "${var.project_name}-memory-stress"
  document_type = "Command"

  content = jsonencode({
    schemaVersion = "2.2"
    description   = "Inject memory stress into a Kubernetes service container cgroup (ADR 078)"
    parameters = {
      ServiceName = {
        type        = "String"
        default     = "service-b"
        description = "Target Kubernetes service (pod label app=<ServiceName>)"
      }
    }
    mainSteps = [{
      action = "aws:runShellScript"
      name   = "memoryStress"
      inputs = {
        timeoutSeconds = "360"
        runCommand = [
          "#!/bin/bash",
          "set -euo pipefail",
          "SERVICE='{{ ServiceName }}'",
          "CONTAINER_ID=$(crictl ps --name \"$SERVICE\" -q 2>/dev/null | head -1)",
          "if [ -z \"$CONTAINER_ID\" ]; then echo \"$SERVICE not on this node, skipping\"; exit 0; fi",
          "PID=$(crictl inspect \"$CONTAINER_ID\" | jq -r '.info.pid')",
          "CGROUP=$(grep '^[0-9]*:memory:' /proc/$PID/cgroup | cut -d: -f3)",
          "echo \"Injecting into /sys/fs/cgroup/memory$CGROUP\"",
          "printf 'import time\\ndata = []\\nwhile True:\\n    data.append(bytearray(1024*1024))\\n    time.sleep(0.05)\\n' > /tmp/memstress.py",
          "echo $$ > /sys/fs/cgroup/memory$${CGROUP}/cgroup.procs",
          "exec python3 /tmp/memstress.py",
        ]
      }
    }]
  })

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# FIS 実験テンプレート — memory_stress (ADR 078)
# aws:eks:pod-memory-stress は cgroup 分離により service-b に届かないため
# aws:ssm:send-command で EC2 ノードから直接 cgroup に注入する
# ---------------------------------------------------------------------------

resource "aws_fis_experiment_template" "memory_stress" {
  for_each    = local.fis_fault_services
  description = "Memory stress injection for ${each.key} via SSM cgroup injection (ADR 078)"
  role_arn    = aws_iam_role.fis_execution.arn

  stop_condition {
    source = "aws:cloudwatch:alarm"
    value  = aws_cloudwatch_metric_alarm.service_b_5xx_rate[0].arn
  }

  target {
    name           = "nodes"
    resource_type  = "aws:ec2:instance"
    selection_mode = "ALL"

    resource_tag {
      key   = "eks:cluster-name"
      value = module.eks.cluster_name
    }
  }

  action {
    name      = "inject-memory-stress-via-ssm"
    action_id = "aws:ssm:send-command"

    parameter {
      key   = "documentArn"
      value = aws_ssm_document.memory_stress.arn
    }

    parameter {
      key   = "documentParameters"
      value = jsonencode({ ServiceName = each.key })
    }

    parameter {
      key   = "duration"
      value = "PT300S"
    }

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
        prefix      = "memory_stress/${each.key}"
      }
    }

    pre_experiment_duration  = "PT5M"
    post_experiment_duration = "PT5M"
  }

  tags = merge(local.common_tags, {
    Project = "chaos-platform"
  })
}

output "fis_template_pod_kill_ids" {
  description = "FIS experiment template IDs for pod_kill (keyed by service)"
  value       = { for k, v in aws_fis_experiment_template.pod_kill : k => v.id }
}

output "fis_template_cpu_stress_ids" {
  description = "FIS experiment template IDs for cpu_stress (keyed by service)"
  value       = { for k, v in aws_fis_experiment_template.cpu_stress : k => v.id }
}

output "fis_template_memory_stress_ids" {
  description = "FIS experiment template IDs for memory_stress (keyed by service)"
  value       = { for k, v in aws_fis_experiment_template.memory_stress : k => v.id }
}
