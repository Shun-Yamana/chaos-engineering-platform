# ---------------------------------------------------------------------------
# GitHub Actions OIDC — CI/CD デプロイロール
# ---------------------------------------------------------------------------

resource "aws_iam_openid_connect_provider" "github_actions" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  thumbprint_list = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
    "1c58a3a8518e8759bf075b76b750d4f2df264fcd",
  ]

  tags = local.common_tags
}

resource "aws_iam_role" "github_actions" {
  name = "${var.project_name}-github-actions"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.github_actions.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        }
        StringLike = {
          "token.actions.githubusercontent.com:sub" = "repo:${var.github_repo}:*"
        }
      }
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_policy" "github_actions" {
  name = "${var.project_name}-github-actions-policy"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ECRAuth"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Sid    = "ECRPush"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload",
          "ecr:PutImage",
        ]
        Resource = [
          aws_ecr_repository.service_a.arn,
          aws_ecr_repository.service_b.arn,
          aws_ecr_repository.chaos_agent.arn,
        ]
      },
      {
        Sid      = "EKSDescribe"
        Effect   = "Allow"
        Action   = ["eks:DescribeCluster"]
        Resource = "arn:aws:eks:${var.aws_region}:${data.aws_caller_identity.current.account_id}:cluster/${module.eks.cluster_name}"
      },
      {
        Sid    = "FrontendS3"
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket",
        ]
        Resource = [
          aws_s3_bucket.frontend.arn,
          "${aws_s3_bucket.frontend.arn}/*",
        ]
      },
      {
        Sid      = "FrontendCFInvalidation"
        Effect   = "Allow"
        Action   = ["cloudfront:CreateInvalidation"]
        Resource = aws_cloudfront_distribution.frontend.arn
      },
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "github_actions" {
  role       = aws_iam_role.github_actions.name
  policy_arn = aws_iam_policy.github_actions.arn
}

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
        Sid      = "DynamoDB"
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:UpdateItem", "dynamodb:Scan"]
        Resource = "arn:aws:dynamodb:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/chaos-*"
      },
      {
        Sid    = "FISTemplateRead"
        Effect = "Allow"
        Action = ["fis:GetExperimentTemplate"]
        Resource = "arn:aws:fis:${var.aws_region}:${data.aws_caller_identity.current.account_id}:experiment-template/*"
        Condition = {
          StringEquals = { "aws:ResourceTag/Project" = "chaos-platform" }
        }
      },
      {
        Sid    = "FISTemplateCreate"
        Effect = "Allow"
        Action = ["fis:CreateExperimentTemplate"]
        Resource = [
          "arn:aws:fis:${var.aws_region}:${data.aws_caller_identity.current.account_id}:experiment-template/*",
          "arn:aws:fis:${var.aws_region}:${data.aws_caller_identity.current.account_id}:action/*",
        ]
        Condition = {
          StringEquals = { "aws:RequestTag/Project" = "chaos-platform" }
        }
      },
      {
        Sid    = "FISTemplateDelete"
        Effect = "Allow"
        Action = ["fis:DeleteExperimentTemplate"]
        Resource = "arn:aws:fis:${var.aws_region}:${data.aws_caller_identity.current.account_id}:experiment-template/*"
        Condition = {
          StringEquals = { "aws:ResourceTag/Temporary" = "true" }
        }
      },
      {
        Sid      = "FISStart"
        Effect   = "Allow"
        Action   = ["fis:StartExperiment"]
        Resource = "arn:aws:fis:${var.aws_region}:${data.aws_caller_identity.current.account_id}:experiment-template/*"
        Condition = {
          StringEquals = { "aws:ResourceTag/Project" = "chaos-platform" }
        }
      },
      {
        Sid      = "FISManage"
        Effect   = "Allow"
        Action   = ["fis:StopExperiment", "fis:GetExperiment"]
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
        Sid      = "EC2Modify"
        Effect   = "Allow"
        Action   = ["ec2:ModifyNetworkInterfaceAttribute"]
        Resource = "arn:aws:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:network-interface/*"
        Condition = {
          StringEquals = {
            "aws:ResourceTag/aws:eks:cluster-name" = module.eks.cluster_name
          }
        }
      },
      {
        # log_configuration 用: アカウントレベルの Delivery API はリソース指定不可
        Sid    = "CloudWatchLogsDelivery"
        Effect = "Allow"
        Action = [
          "logs:CreateLogDelivery",
          "logs:GetLogDelivery",
          "logs:UpdateLogDelivery",
          "logs:DeleteLogDelivery",
          "logs:ListLogDeliveries",
          "logs:PutResourcePolicy",
          "logs:DescribeResourcePolicies",
          "logs:DescribeLogGroups",
        ]
        Resource = "*"
      },
      {
        Sid      = "CloudWatchLogsPut"
        Effect   = "Allow"
        Action   = ["logs:PutLogEvents", "logs:CreateLogStream"]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/fis/${var.project_name}:*"
      },
      {
        # experiment_report_configuration のデータソース読み取り
        Sid      = "CloudWatchDashboard"
        Effect   = "Allow"
        Action   = ["cloudwatch:GetDashboard"]
        Resource = aws_cloudwatch_dashboard.chaos_experiment.dashboard_arn
      },
      {
        # experiment_report_configuration の S3 出力
        Sid    = "FISReportsS3"
        Effect = "Allow"
        Action = ["s3:PutObject", "s3:GetBucketLocation"]
        Resource = [
          aws_s3_bucket.fis_reports.arn,
          "${aws_s3_bucket.fis_reports.arn}/*",
        ]
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

# ---------------------------------------------------------------------------
# SNS 配信フィードバックログ用 IAM ロール
# Lambda・HTTPS(Slack) 両サブスクリプションの配信失敗を CloudWatch Logs に記録
# ---------------------------------------------------------------------------

resource "aws_iam_role" "sns_feedback" {
  name = "${var.project_name}-sns-feedback"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "sns.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "sns_feedback_logs" {
  name = "cloudwatch-logs"
  role = aws_iam_role.sns_feedback.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "logs:DescribeLogGroups",
        "logs:DescribeLogStreams",
      ]
      Resource = "*"
    }]
  })
}

# ---------------------------------------------------------------------------
# Grafana IRSA — CloudWatch + X-Ray 読み取り (ADR 053)
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "grafana_assume_role" {
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
      values   = ["system:serviceaccount:monitoring:grafana"]
    }
  }
}

resource "aws_iam_role" "grafana" {
  name               = "${var.project_name}-grafana"
  assume_role_policy = data.aws_iam_policy_document.grafana_assume_role.json
  tags               = local.common_tags
}

resource "aws_iam_role_policy" "grafana_cloudwatch_xray" {
  name = "cloudwatch-xray-read"
  role = aws_iam_role.grafana.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "CloudWatchRead"
        Effect = "Allow"
        Action = [
          "cloudwatch:DescribeAlarmsForMetric",
          "cloudwatch:DescribeAlarmHistory",
          "cloudwatch:DescribeAlarms",
          "cloudwatch:ListMetrics",
          "cloudwatch:GetMetricData",
          "cloudwatch:GetInsightRuleReport",
        ]
        Resource = "*"
      },
      {
        Sid    = "CloudWatchLogsRead"
        Effect = "Allow"
        Action = [
          "logs:DescribeLogGroups",
          "logs:DescribeLogStreams",
          "logs:GetLogEvents",
          "logs:StartQuery",
          "logs:StopQuery",
          "logs:GetQueryResults",
          "logs:GetLogGroupFields",
          "logs:FilterLogEvents",
        ]
        Resource = "*"
      },
      {
        Sid    = "XRayRead"
        Effect = "Allow"
        Action = [
          "xray:GetSamplingRules",
          "xray:GetSamplingTargets",
          "xray:GetGroups",
          "xray:GetGroup",
          "xray:ListTagsForResource",
          "xray:GetTraceSummaries",
          "xray:BatchGetTraces",
          "xray:GetServiceGraph",
          "xray:GetTraceGraph",
          "xray:GetTimeSeriesServiceStatistics",
        ]
        Resource = "*"
      },
    ]
  })
}
