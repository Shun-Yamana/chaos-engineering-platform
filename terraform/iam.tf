# ---------------------------------------------------------------------------
# chaos-agent IRSA (ADR 011 item 4, docs/iam-design.md §2)
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "chaos_agent_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [module.eks.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${module.eks.oidc_provider}:sub"
      values   = ["system:serviceaccount:chaos:chaos-agent"]
    }
  }
}

resource "aws_iam_role" "chaos_agent" {
  name               = "chaos-agent-role"
  assume_role_policy = data.aws_iam_policy_document.chaos_agent_assume_role.json
  tags               = local.common_tags
}

resource "aws_iam_policy" "chaos_agent" {
  name        = "chaos-agent-policy"
  description = "Least-privilege policy for chaos-agent Pod (docs/iam-design.md §2)"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DynamoDB"
        Effect = "Allow"
        Action = ["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:UpdateItem"]
        Resource = "arn:aws:dynamodb:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/chaos-*"
      },
      {
        Sid    = "FISStart"
        Effect = "Allow"
        Action = ["fis:StartExperiment"]
        Resource = "arn:aws:fis:${var.aws_region}:${data.aws_caller_identity.current.account_id}:experiment-template/*"
        Condition = {
          StringEquals = { "aws:ResourceTag/Project" = "chaos-platform" }
        }
      },
      {
        Sid    = "FISManage"
        Effect = "Allow"
        Action = ["fis:StopExperiment", "fis:GetExperiment"]
        Resource = "arn:aws:fis:${var.aws_region}:${data.aws_caller_identity.current.account_id}:experiment/*"
      },
      {
        Sid      = "PassFISRole"
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = aws_iam_role.fis_execution.arn
      },
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "chaos_agent" {
  role       = aws_iam_role.chaos_agent.name
  policy_arn = aws_iam_policy.chaos_agent.arn
}

# ---------------------------------------------------------------------------
# FIS 実行ロール (ADR 011 item 4, docs/iam-design.md §3)
# ---------------------------------------------------------------------------

resource "aws_iam_role" "fis_execution" {
  name = "fis-execution-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "fis.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_policy" "fis_execution" {
  name        = "fis-execution-policy"
  description = "Least-privilege policy for FIS to inject network latency (docs/iam-design.md §3)"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "EKSDescribe"
        Effect   = "Allow"
        Action   = ["eks:DescribeCluster"]
        Resource = "arn:aws:eks:${var.aws_region}:${data.aws_caller_identity.current.account_id}:cluster/${module.eks.cluster_name}"
      },
      {
        # AWS 仕様でリソース指定不可
        Sid      = "EC2Describe"
        Effect   = "Allow"
        Action   = ["ec2:DescribeNetworkInterfaces"]
        Resource = "*"
      },
      {
        Sid    = "EC2Modify"
        Effect = "Allow"
        Action = ["ec2:ModifyNetworkInterfaceAttribute"]
        Resource = "arn:aws:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:network-interface/*"
        Condition = {
          StringEquals = {
            "aws:ResourceTag/aws:eks:cluster-name" = module.eks.cluster_name
          }
        }
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = ["logs:CreateLogDelivery", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/fis/chaos-*"
      },
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "fis_execution" {
  role       = aws_iam_role.fis_execution.name
  policy_arn = aws_iam_policy.fis_execution.arn
}

# ---------------------------------------------------------------------------
# CloudWatch Agent IRSA — amazon-cloudwatch-observability addon (ADR 011 item 7)
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "cloudwatch_agent_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [module.eks.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${module.eks.oidc_provider}:sub"
      values   = ["system:serviceaccount:amazon-cloudwatch-observability:cloudwatch-agent"]
    }
  }
}

resource "aws_iam_role" "cloudwatch_agent" {
  name               = "${var.project_name}-cloudwatch-agent-role"
  assume_role_policy = data.aws_iam_policy_document.cloudwatch_agent_assume_role.json
  tags               = local.common_tags
}

resource "aws_iam_role_policy_attachment" "cloudwatch_agent" {
  role       = aws_iam_role.cloudwatch_agent.name
  policy_arn = "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy"
}
