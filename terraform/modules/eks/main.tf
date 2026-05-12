data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# AWS managed KMS key for EKS Secrets encryption (ADR 011 item 8)
data "aws_kms_key" "eks" {
  key_id = "alias/aws/eks"
}

# TLS certificate for OIDC provider thumbprint (ADR 011 item 1)
data "tls_certificate" "eks" {
  url = aws_eks_cluster.main.identity[0].oidc[0].issuer
}

# ---------------------------------------------------------------------------
# EKS cluster IAM role
# ---------------------------------------------------------------------------

resource "aws_iam_role" "eks_cluster" {
  name = "${var.project_name}-eks-cluster-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "eks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "eks_cluster_policy" {
  role       = aws_iam_role.eks_cluster.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}

# ---------------------------------------------------------------------------
# Security Groups (ADR 011 item 3 — ① Cluster SG, ② Pod ENI SG)
# ---------------------------------------------------------------------------

resource "aws_security_group" "eks_cluster" {
  name        = "${var.project_name}-eks-cluster-sg"
  description = "EKS control plane SG (Cluster SG)"
  vpc_id      = var.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, { Name = "${var.project_name}-eks-cluster-sg" })
}

resource "aws_security_group" "pod_eni" {
  name        = "${var.project_name}-pod-eni-sg"
  description = "Fargate Pod ENI SG (Pod ENI SG). Applied via SecurityGroupPolicy CRD."
  vpc_id      = var.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, { Name = "${var.project_name}-pod-eni-sg" })
}

# ---------------------------------------------------------------------------
# EKS Cluster (ADR 011 item 2)
# ---------------------------------------------------------------------------

resource "aws_eks_cluster" "main" {
  name     = "${var.project_name}-cluster"
  version  = var.kubernetes_version
  role_arn = aws_iam_role.eks_cluster.arn

  vpc_config {
    subnet_ids              = concat(var.public_subnet_ids, var.private_subnet_ids)
    security_group_ids      = [aws_security_group.eks_cluster.id]
    endpoint_private_access = true
    endpoint_public_access  = true
    public_access_cidrs     = var.public_access_cidrs
  }

  # API モード: aws_eks_access_entry で宣言的管理 (aws-auth ConfigMap 廃止)
  access_config {
    authentication_mode                         = "API"
    bootstrap_cluster_creator_admin_permissions = true
  }

  # AZ 障害時フェイルオーバー有効化 (コストゼロ)
  zonal_shift_config {
    enabled = true
  }

  # K8s Secrets を AWS マネージドキーで暗号化 (ADR 011 item 8)
  encryption_config {
    provider {
      key_arn = data.aws_kms_key.eks.arn
    }
    resources = ["secrets"]
  }

  # controllerManager/scheduler は Fargate では有用な情報が少ないため除外 (ADR 011 item 2)
  enabled_cluster_log_types = ["api", "audit", "authenticator"]

  tags = var.tags

  depends_on = [aws_iam_role_policy_attachment.eks_cluster_policy]
}

# ---------------------------------------------------------------------------
# Fargate Pod 実行ロール — カスタマーマネージドポリシー (ADR 011 item 4)
# AmazonEKSFargatePodExecutionRolePolicy は Resource: * のため使用しない
# ---------------------------------------------------------------------------

resource "aws_iam_role" "fargate_pod_execution" {
  name = "${var.project_name}-fargate-execution-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "eks-fargate-pods.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = var.tags
}

resource "aws_iam_policy" "fargate_pod_execution" {
  name        = "${var.project_name}-fargate-execution-policy"
  description = "Least-privilege policy for Fargate pod execution role (ADR 011 item 4)"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ECRAuth"
        Effect = "Allow"
        # AWS 仕様でリソース指定不可
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Sid    = "ECRPullOwn"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
        ]
        Resource = "arn:aws:ecr:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:repository/*"
      },
      {
        # EKS アドオン（coredns / vpc-cni 等）は AWS マネージド ECR アカウントから pull
        Sid    = "ECRPullEKSManaged"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
        ]
        Resource = "arn:aws:ecr:${data.aws_region.current.name}:602401143452:repository/*"
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:DescribeLogStreams",
        ]
        Resource = [
          "arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:/aws/eks/${var.project_name}-cluster/*",
          "arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:/aws/containerinsights/${var.project_name}-cluster/*",
        ]
      },
      {
        # Container Insights サイドカーがメトリクスを送信するために必要
        Sid      = "CloudWatchMetrics"
        Effect   = "Allow"
        Action   = ["cloudwatch:PutMetricData"]
        Resource = "*"
      },
    ]
  })

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "fargate_pod_execution_policy" {
  role       = aws_iam_role.fargate_pod_execution.name
  policy_arn = aws_iam_policy.fargate_pod_execution.arn
}

# ---------------------------------------------------------------------------
# Fargate プロファイル × 4 (ADR 011 item 6)
# ---------------------------------------------------------------------------

resource "aws_eks_fargate_profile" "default" {
  cluster_name           = aws_eks_cluster.main.name
  fargate_profile_name   = "${var.project_name}-fargate-default"
  pod_execution_role_arn = aws_iam_role.fargate_pod_execution.arn
  subnet_ids             = var.private_subnet_ids

  selector { namespace = "default" }

  tags       = var.tags
  depends_on = [aws_iam_role_policy_attachment.fargate_pod_execution_policy]
}

resource "aws_eks_fargate_profile" "kube_system" {
  cluster_name           = aws_eks_cluster.main.name
  fargate_profile_name   = "${var.project_name}-fargate-kube-system"
  pod_execution_role_arn = aws_iam_role.fargate_pod_execution.arn
  subnet_ids             = var.private_subnet_ids

  selector { namespace = "kube-system" }

  tags       = var.tags
  depends_on = [aws_iam_role_policy_attachment.fargate_pod_execution_policy]
}

resource "aws_eks_fargate_profile" "chaos" {
  cluster_name           = aws_eks_cluster.main.name
  fargate_profile_name   = "${var.project_name}-fargate-chaos"
  pod_execution_role_arn = aws_iam_role.fargate_pod_execution.arn
  subnet_ids             = var.private_subnet_ids

  selector { namespace = "chaos" }

  tags       = var.tags
  depends_on = [aws_iam_role_policy_attachment.fargate_pod_execution_policy]
}

resource "aws_eks_fargate_profile" "aws_observability" {
  cluster_name           = aws_eks_cluster.main.name
  fargate_profile_name   = "${var.project_name}-fargate-aws-observability"
  pod_execution_role_arn = aws_iam_role.fargate_pod_execution.arn
  subnet_ids             = var.private_subnet_ids

  selector { namespace = "aws-observability" }

  tags       = var.tags
  depends_on = [aws_iam_role_policy_attachment.fargate_pod_execution_policy]
}

resource "aws_eks_fargate_profile" "amazon_cloudwatch" {
  cluster_name           = aws_eks_cluster.main.name
  fargate_profile_name   = "${var.project_name}-fargate-amazon-cloudwatch"
  pod_execution_role_arn = aws_iam_role.fargate_pod_execution.arn
  subnet_ids             = var.private_subnet_ids

  selector { namespace = "amazon-cloudwatch" }

  tags       = var.tags
  depends_on = [aws_iam_role_policy_attachment.fargate_pod_execution_policy]
}

# ---------------------------------------------------------------------------
# OIDC プロバイダー — IRSA の前提 (ADR 011 item 1, 2)
# ---------------------------------------------------------------------------

resource "aws_iam_openid_connect_provider" "eks" {
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.eks.certificates[0].sha1_fingerprint]
  url             = aws_eks_cluster.main.identity[0].oidc[0].issuer

  tags = var.tags
}
