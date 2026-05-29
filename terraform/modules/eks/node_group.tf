# EC2 Managed Node Group (ADR 051: Fargate → EC2 移行)
# DaemonSet対応・tc netem有効化・コスト削減のためEC2を選択

resource "aws_iam_role" "eks_node" {
  name = "${var.project_name}-eks-node-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "eks_node_worker" {
  role       = aws_iam_role.eks_node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy"
}

resource "aws_iam_role_policy_attachment" "eks_node_ecr" {
  role       = aws_iam_role.eks_node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

resource "aws_iam_role_policy_attachment" "eks_node_cni" {
  role       = aws_iam_role.eks_node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"
}

resource "aws_iam_role_policy_attachment" "eks_node_xray" {
  role       = aws_iam_role.eks_node.name
  policy_arn = "arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess"
}

resource "aws_iam_role_policy_attachment" "eks_node_ssm" {
  role       = aws_iam_role.eks_node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# Fluent Bit (EC2 ノード DaemonSet) が Container Insights ログを CloudWatch Logs へ送信するために必要
resource "aws_iam_policy" "eks_node_cloudwatch_logs" {
  name = "${var.project_name}-eks-node-cloudwatch-logs"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
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
          "arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:/aws/containerinsights/${var.project_name}-cluster:*",
          "arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:/aws/containerinsights/${var.project_name}-cluster/*",
          "arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:/aws/containerinsights/${var.project_name}-cluster/*:*",
        ]
      },
      {
        Sid      = "CloudWatchMetrics"
        Effect   = "Allow"
        Action   = ["cloudwatch:PutMetricData"]
        Resource = "*"
      },
    ]
  })

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "eks_node_cloudwatch_logs" {
  role       = aws_iam_role.eks_node.name
  policy_arn = aws_iam_policy.eks_node_cloudwatch_logs.arn
}

resource "aws_eks_node_group" "main" {
  cluster_name    = aws_eks_cluster.main.name
  node_group_name = "${var.project_name}-node-group"
  node_role_arn   = aws_iam_role.eks_node.arn
  subnet_ids      = var.private_subnet_ids

  instance_types = ["t3.medium"]

  scaling_config {
    min_size     = 2
    max_size     = 4
    desired_size = 2
  }

  tags = merge(var.tags, {
    "kubernetes.io/cluster/${aws_eks_cluster.main.name}" = "owned"
  })

  depends_on = [
    aws_iam_role_policy_attachment.eks_node_worker,
    aws_iam_role_policy_attachment.eks_node_ecr,
    aws_iam_role_policy_attachment.eks_node_cni,
    aws_iam_role_policy_attachment.eks_node_xray,
    aws_iam_role_policy_attachment.eks_node_ssm,
    aws_iam_role_policy_attachment.eks_node_cloudwatch_logs,
  ]
}
