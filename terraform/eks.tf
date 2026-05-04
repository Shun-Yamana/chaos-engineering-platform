module "eks" {
  source = "./modules/eks"

  project_name        = var.project_name
  kubernetes_version  = var.kubernetes_version
  vpc_id              = module.vpc.vpc_id
  public_subnet_ids   = module.vpc.public_subnet_ids
  private_subnet_ids  = module.vpc.private_subnet_ids
  public_access_cidrs = var.eks_public_access_cidrs

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# EKS アドオン (ADR 011 item 7)
# ---------------------------------------------------------------------------

resource "aws_eks_addon" "coredns" {
  cluster_name = module.eks.cluster_name
  addon_name   = "coredns"
  tags         = local.common_tags

  depends_on = [module.eks]
}

resource "aws_eks_addon" "vpc_cni" {
  cluster_name = module.eks.cluster_name
  addon_name   = "vpc-cni"
  # NetworkPolicy 強制を有効化 (ADR 012: NetworkPolicy 設計 ⑤)
  configuration_values = jsonencode({
    enableNetworkPolicy = "true"
  })
  tags = local.common_tags

  depends_on = [module.eks]
}

resource "aws_eks_addon" "cloudwatch_observability" {
  cluster_name             = module.eks.cluster_name
  addon_name               = "amazon-cloudwatch-observability"
  service_account_role_arn = aws_iam_role.cloudwatch_agent.arn
  tags                     = local.common_tags

  depends_on = [module.eks]
}

# ---------------------------------------------------------------------------
# EKS アクセスエントリ — GitHub Actions OIDC ロール (ADR 011 item 2, 4)
# API モードで宣言的管理。aws-auth ConfigMap は使用しない。
# ---------------------------------------------------------------------------

resource "aws_eks_access_entry" "github_actions" {
  cluster_name  = module.eks.cluster_name
  principal_arn = var.github_actions_role_arn
  type          = "STANDARD"
  tags          = local.common_tags
}

resource "aws_eks_access_policy_association" "github_actions" {
  cluster_name  = module.eks.cluster_name
  principal_arn = var.github_actions_role_arn
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSEditPolicy"

  access_scope {
    type = "cluster"
  }

  depends_on = [aws_eks_access_entry.github_actions]
}
