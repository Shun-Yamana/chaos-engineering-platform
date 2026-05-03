# FIS 実験テンプレート — network_latency (ADR 009, ADR 011 item 4)
# chaos/agent.py が FIS_TEMPLATE_SERVICE_A / FIS_TEMPLATE_SERVICE_B 環境変数で参照する

locals {
  fis_services = toset(["service-a", "service-b"])
}

resource "aws_fis_experiment_template" "network_latency" {
  for_each    = local.fis_services
  description = "Network latency injection for ${each.key} (aws:eks:pod-network-latency)"
  role_arn    = aws_iam_role.fis_execution.arn

  # 実験停止条件: auto-stopper Lambda が手動で止めるため none
  stop_condition {
    source = "none"
  }

  target {
    name           = "pods"
    resource_type  = "aws:eks:pod"
    selection_mode = "ALL"

    filter {
      path   = "Namespace"
      values = ["default"]
    }

    filter {
      path   = "Labels.app"
      values = [each.key]
    }

    parameters = {
      clusterIdentifier = module.eks.cluster_name
    }
  }

  action {
    name      = "inject-network-latency"
    action_id = "aws:eks:pod-network-latency"

    parameter {
      key   = "duration"
      value = "{{ duration }}"
    }

    parameter {
      key   = "delayMilliseconds"
      value = "{{ delayMilliseconds }}"
    }

    target {
      key   = "Pods"
      value = "pods"
    }
  }

  parameter {
    key         = "duration"
    description = "Duration of the experiment (ISO 8601, e.g. PT300S)"
    required    = true
  }

  parameter {
    key         = "delayMilliseconds"
    description = "Network latency to inject in milliseconds"
    required    = true
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
