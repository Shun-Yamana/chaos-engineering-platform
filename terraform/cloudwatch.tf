# EKS コントロールプレーンログ (ADR 011 item 9)
resource "aws_cloudwatch_log_group" "eks_control_plane" {
  name              = "/aws/eks/${var.project_name}-cluster/cluster"
  retention_in_days = 30
  tags              = local.common_tags
}

# FIS 実験ログ (ADR 011 item 9)
resource "aws_cloudwatch_log_group" "fis" {
  name              = "/aws/fis/${var.project_name}"
  retention_in_days = 30
  tags              = local.common_tags
}

# ---------------------------------------------------------------------------
# Lambda ロググループ (ADR 014: logging_config JSON 形式)
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "lambda_sli_calculator" {
  name              = "/aws/lambda/${var.project_name}-sli-calculator"
  retention_in_days = 30
  tags              = local.common_tags
}

resource "aws_cloudwatch_log_group" "lambda_auto_stopper" {
  name              = "/aws/lambda/${var.project_name}-auto-stopper"
  retention_in_days = 30
  tags              = local.common_tags
}

resource "aws_cloudwatch_log_group" "lambda_api_handler" {
  name              = "/aws/lambda/${var.project_name}-api-handler"
  retention_in_days = 30
  tags              = local.common_tags
}

# API Gateway アクセスログ (ADR 015)
resource "aws_cloudwatch_log_group" "api_gateway" {
  name              = "/aws/apigateway/${var.project_name}"
  retention_in_days = 30
  tags              = local.common_tags
}

# ---------------------------------------------------------------------------
# CloudWatch ダッシュボード — FIS 実験レポートのデータソース (ADR 022)
# experiment_report_configuration.data_sources から参照される
# ---------------------------------------------------------------------------

# Fargate Fluent Bit アプリログ (EMF メトリクス抽出もここから行われる)
resource "aws_cloudwatch_log_group" "eks_workloads" {
  name              = "/aws/eks/${var.project_name}-cluster/workloads"
  retention_in_days = 30
  tags              = local.common_tags
}

locals {
  _alb_widgets = local.alb_alarms_enabled ? [
    {
      type   = "metric"
      x      = 0
      y      = 0
      width  = 12
      height = 6
      properties = {
        title   = "service-b 5xx Error Count"
        view    = "timeSeries"
        region  = var.aws_region
        metrics = [["AWS/ApplicationELB", "HTTPCode_Target_5XX_Count", "LoadBalancer", var.alb_arn_suffix, { stat = "Sum", period = 60 }]]
        yAxis   = { left = { min = 0 } }
      }
    },
    {
      type   = "metric"
      x      = 12
      y      = 0
      width  = 12
      height = 6
      properties = {
        title   = "service-b P95 Latency (s)"
        view    = "timeSeries"
        region  = var.aws_region
        metrics = [["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", var.alb_arn_suffix, { stat = "p95", period = 60 }]]
        yAxis   = { left = { min = 0 } }
      }
    },
    {
      type   = "metric"
      x      = 0
      y      = 6
      width  = 12
      height = 6
      properties = {
        title   = "Healthy Hosts"
        view    = "timeSeries"
        region  = var.aws_region
        metrics = [["AWS/ApplicationELB", "HealthyHostCount", "LoadBalancer", var.alb_arn_suffix, { stat = "Average", period = 60 }]]
        yAxis   = { left = { min = 0 } }
      }
    },
  ] : []

  dashboard_widgets = concat(local._alb_widgets, [
    {
      type   = "metric"
      x      = 0
      y      = 12
      width  = 12
      height = 6
      properties = {
        title   = "service-b Process CPU (%)"
        view    = "timeSeries"
        region  = var.aws_region
        metrics = [["ChaosExperiment", "ProcessCpuPercent", "Service", "service-b", { stat = "Maximum", period = 60 }]]
        yAxis   = { left = { min = 0, max = 100 } }
        annotations = { horizontal = [{ value = 30, label = "Alarm threshold", color = "#ff6961" }] }
      }
    },
    {
      type   = "metric"
      x      = 12
      y      = 12
      width  = 12
      height = 6
      properties = {
        title   = "service-b Process Memory (MB)"
        view    = "timeSeries"
        region  = var.aws_region
        metrics = [["ChaosExperiment", "ProcessMemoryMB", "Service", "service-b", { stat = "Maximum", period = 60 }]]
        yAxis   = { left = { min = 0 } }
        annotations = { horizontal = [{ value = 300, label = "Alarm threshold", color = "#ff6961" }] }
      }
    },
    {
      type   = "metric"
      x      = 0
      y      = 18
      width  = 12
      height = 6
      properties = {
        title   = "service-a Process CPU (%)"
        view    = "timeSeries"
        region  = var.aws_region
        metrics = [["ChaosExperiment", "ProcessCpuPercent", "Service", "service-a", { stat = "Maximum", period = 60 }]]
        yAxis   = { left = { min = 0, max = 100 } }
        annotations = { horizontal = [{ value = 30, label = "Alarm threshold", color = "#ff6961" }] }
      }
    },
    {
      type   = "metric"
      x      = 12
      y      = 18
      width  = 12
      height = 6
      properties = {
        title   = "service-a Process Memory (MB)"
        view    = "timeSeries"
        region  = var.aws_region
        metrics = [["ChaosExperiment", "ProcessMemoryMB", "Service", "service-a", { stat = "Maximum", period = 60 }]]
        yAxis   = { left = { min = 0 } }
        annotations = { horizontal = [{ value = 300, label = "Alarm threshold", color = "#ff6961" }] }
      }
    },
  ])
}

resource "aws_cloudwatch_dashboard" "chaos_experiment" {
  dashboard_name = "${var.project_name}-experiment"
  dashboard_body = jsonencode({ widgets = local.dashboard_widgets })
}

# ---------------------------------------------------------------------------
# CloudWatch Alarms (ADR 012: CloudWatch Alarm 設計)
# ALB メトリクスは ALB が作成された後に有効になる (var.alb_arn_suffix が必要)
# ---------------------------------------------------------------------------

locals {
  alb_alarms_enabled = var.alb_arn_suffix != ""
}

# ① service-b 5xx エラーレート > 5% (ADR 005-009 共通閾値)
resource "aws_cloudwatch_metric_alarm" "service_b_5xx_rate" {
  count = local.alb_alarms_enabled ? 1 : 0

  alarm_name          = "${var.project_name}-service-b-5xx-rate"
  alarm_description   = "service-b 5xx error rate exceeded 5% (SLO threshold)"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  threshold           = 5

  metric_query {
    id          = "error_rate"
    expression  = "100 * errors / requests"
    label       = "5xx Error Rate (%)"
    return_data = true
  }

  metric_query {
    id = "errors"
    metric {
      metric_name = "HTTPCode_Target_5XX_Count"
      namespace   = "AWS/ApplicationELB"
      period      = 60
      stat        = "Sum"
      dimensions = {
        LoadBalancer = var.alb_arn_suffix
      }
    }
  }

  metric_query {
    id = "requests"
    metric {
      metric_name = "RequestCount"
      namespace   = "AWS/ApplicationELB"
      period      = 60
      stat        = "Sum"
      dimensions = {
        LoadBalancer = var.alb_arn_suffix
      }
    }
  }

  alarm_actions = [aws_sns_topic.chaos_alerts.arn]
  ok_actions    = [aws_sns_topic.chaos_alerts.arn]
  tags          = local.common_tags
}

# ② レイテンシ P95 > 1000ms (ADR 006, 007, 009)
resource "aws_cloudwatch_metric_alarm" "service_b_latency_p95" {
  count = local.alb_alarms_enabled ? 1 : 0

  alarm_name          = "${var.project_name}-service-b-latency-p95"
  alarm_description   = "service-b P95 latency exceeded 1000ms"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  threshold           = 1

  metric_name = "TargetResponseTime"
  namespace   = "AWS/ApplicationELB"
  period      = 60
  extended_statistic = "p95"
  dimensions = {
    LoadBalancer = var.alb_arn_suffix
  }

  alarm_actions = [aws_sns_topic.chaos_alerts.arn]
  ok_actions    = [aws_sns_topic.chaos_alerts.arn]
  tags          = local.common_tags
}

# ④ service-b CPU 使用率 > 30% (cpu_stress 実験検知)
# EMF メトリクス: service-b が stdout に出力 → Fargate Fluent Bit → CloudWatch Logs → 自動抽出
resource "aws_cloudwatch_metric_alarm" "service_b_cpu_high" {
  alarm_name          = "${var.project_name}-service-b-cpu-high"
  alarm_description   = "service-b process CPU exceeded 30% (cpu_stress experiment indicator)"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  threshold           = 30
  treat_missing_data  = "notBreaching"

  metric_name = "ProcessCpuPercent"
  namespace   = "ChaosExperiment"
  period      = 60
  statistic   = "Maximum"
  dimensions = {
    Service = "service-b"
  }

  alarm_actions = [aws_sns_topic.chaos_alerts.arn]
  ok_actions    = [aws_sns_topic.chaos_alerts.arn]
  tags          = local.common_tags
}

# ⑤ service-b メモリ使用量 > 300MB (memory_stress 実験検知)
resource "aws_cloudwatch_metric_alarm" "service_b_memory_high" {
  alarm_name          = "${var.project_name}-service-b-memory-high"
  alarm_description   = "service-b process memory exceeded 300MB (memory_stress experiment indicator)"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  threshold           = 300
  treat_missing_data  = "notBreaching"

  metric_name = "ProcessMemoryMB"
  namespace   = "ChaosExperiment"
  period      = 60
  statistic   = "Maximum"
  dimensions = {
    Service = "service-b"
  }

  alarm_actions = [aws_sns_topic.chaos_alerts.arn]
  ok_actions    = [aws_sns_topic.chaos_alerts.arn]
  tags          = local.common_tags
}

# ⑥ service-a CPU 使用率 > 30%
resource "aws_cloudwatch_metric_alarm" "service_a_cpu_high" {
  alarm_name          = "${var.project_name}-service-a-cpu-high"
  alarm_description   = "service-a process CPU exceeded 30% (cpu_stress experiment indicator)"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  threshold           = 30
  treat_missing_data  = "notBreaching"

  metric_name = "ProcessCpuPercent"
  namespace   = "ChaosExperiment"
  period      = 60
  statistic   = "Maximum"
  dimensions = {
    Service = "service-a"
  }

  alarm_actions = [aws_sns_topic.chaos_alerts.arn]
  ok_actions    = [aws_sns_topic.chaos_alerts.arn]
  tags          = local.common_tags
}

# ⑦ service-a メモリ使用量 > 300MB
resource "aws_cloudwatch_metric_alarm" "service_a_memory_high" {
  alarm_name          = "${var.project_name}-service-a-memory-high"
  alarm_description   = "service-a process memory exceeded 300MB (memory_stress experiment indicator)"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  threshold           = 300
  treat_missing_data  = "notBreaching"

  metric_name = "ProcessMemoryMB"
  namespace   = "ChaosExperiment"
  period      = 60
  statistic   = "Maximum"
  dimensions = {
    Service = "service-a"
  }

  alarm_actions = [aws_sns_topic.chaos_alerts.arn]
  ok_actions    = [aws_sns_topic.chaos_alerts.arn]
  tags          = local.common_tags
}

# ③ chaos-agent Pod 異常 (Container Insights)
resource "aws_cloudwatch_metric_alarm" "chaos_agent_pod_down" {
  alarm_name          = "${var.project_name}-chaos-agent-pod-down"
  alarm_description   = "chaos-agent pod is not running"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 2
  threshold           = 1
  treat_missing_data  = "notBreaching"

  metric_name = "pod_number_of_running_containers"
  namespace   = "ContainerInsights"
  period      = 60
  statistic   = "Average"
  dimensions = {
    ClusterName = module.eks.cluster_name
    Namespace   = "chaos"
    PodName     = "chaos-agent"
  }

  alarm_actions = [aws_sns_topic.chaos_alerts.arn]
  ok_actions    = [aws_sns_topic.chaos_alerts.arn]
  tags          = local.common_tags
}
