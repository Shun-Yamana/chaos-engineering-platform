resource "aws_cloudwatch_log_group" "eks_application" {
  name              = "/aws/eks/${var.project_name}-cluster/application"
  retention_in_days = 7

  tags = local.common_tags
}

resource "aws_cloudwatch_log_group" "eks_performance" {
  name              = "/aws/eks/${var.project_name}-cluster/performance"
  retention_in_days = 7

  tags = local.common_tags
}

resource "aws_iam_policy" "cloudwatch_container_insights" {
  name        = "${var.project_name}-cloudwatch-container-insights"
  description = "Allow Fargate pods to push logs and metrics to CloudWatch"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:CreateLogGroup",
          "logs:DescribeLogStreams",
          "logs:PutLogEvents",
          "cloudwatch:PutMetricData"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "fargate_cloudwatch" {
  role       = module.eks.fargate_pod_execution_role_name
  policy_arn = aws_iam_policy.cloudwatch_container_insights.arn
}
