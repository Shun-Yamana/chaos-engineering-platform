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

# Fargate 上の coredns は eks.amazonaws.com/compute-type: ec2 アノテーションを
# 削除しないと Pending のまま DEGRADED になる（EKS Fargate の既知制約）
# addon 作成より先に patch することで ACTIVE になる
resource "null_resource" "patch_coredns_fargate" {
  triggers = {
    cluster_name = module.eks.cluster_name
  }

  provisioner "local-exec" {
    command = <<-EOT
      aws eks update-kubeconfig --name ${module.eks.cluster_name} --region ${var.aws_region}
      kubectl patch deployment coredns -n kube-system --type json \
        -p '[{"op":"remove","path":"/spec/template/metadata/annotations/eks.amazonaws.com~1compute-type"}]' || true
      kubectl rollout status deployment/coredns -n kube-system --timeout=300s || true
    EOT
  }

  depends_on = [module.eks]
}

resource "aws_eks_addon" "coredns" {
  cluster_name                = module.eks.cluster_name
  addon_name                  = "coredns"
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"
  tags                        = local.common_tags

  depends_on = [null_resource.patch_coredns_fargate]

  timeouts {
    create = "30m"
  }
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
  cluster_name                = module.eks.cluster_name
  addon_name                  = "amazon-cloudwatch-observability"
  service_account_role_arn    = aws_iam_role.cloudwatch_agent.arn
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"
  tags                        = local.common_tags

  depends_on = [aws_eks_addon.coredns]
}

# ---------------------------------------------------------------------------
# EKS アクセスエントリ — GitHub Actions OIDC ロール (ADR 011 item 2, 4)
# API モードで宣言的管理。aws-auth ConfigMap は使用しない。
# ---------------------------------------------------------------------------

resource "aws_eks_access_entry" "github_actions" {
  cluster_name  = module.eks.cluster_name
  principal_arn = aws_iam_role.github_actions.arn
  type          = "STANDARD"
  tags          = local.common_tags
}

resource "aws_eks_access_policy_association" "github_actions" {
  cluster_name  = module.eks.cluster_name
  principal_arn = aws_iam_role.github_actions.arn
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSEditPolicy"

  access_scope {
    type = "cluster"
  }

  depends_on = [aws_eks_access_entry.github_actions]
}
