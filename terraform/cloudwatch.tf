# EKS コントロールプレーンログ (ADR 011 item 9)
# AWS が /aws/eks/<cluster>/cluster に自動作成するが、保持期間を明示的に30日に設定する
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
