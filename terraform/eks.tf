module "eks" {
  source = "./modules/eks"

  project_name        = var.project_name
  kubernetes_version  = var.kubernetes_version
  vpc_id              = module.vpc.vpc_id
  public_subnet_ids   = module.vpc.public_subnet_ids
  private_subnet_ids  = module.vpc.private_subnet_ids
  public_access_cidrs = var.eks_public_access_cidrs

  tags = local.common_tags

  # ロググループを先に作成することで EKS の自動生成との競合を防ぐ
  depends_on = [aws_cloudwatch_log_group.eks_control_plane]
}

# ---------------------------------------------------------------------------
# EKS アドオン (ADR 011 item 7)
# ---------------------------------------------------------------------------

# EC2ノード上では CoreDNS は自動的に Running になるため Fargate patch は不要 (ADR 051)
resource "aws_eks_addon" "coredns" {
  cluster_name                = module.eks.cluster_name
  addon_name                  = "coredns"
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"
  tags                        = local.common_tags

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
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"

  access_scope {
    type = "cluster"
  }

  depends_on = [aws_eks_access_entry.github_actions]
}

# ---------------------------------------------------------------------------
# Ingress マニフェスト自動生成・適用
# cloudfront_origin_secret が apply のたびに変わるため、
# templatefile で ingress.yaml を生成し kubectl apply で反映する
# ---------------------------------------------------------------------------

resource "local_file" "ingress_yaml" {
  content = templatefile("${path.module}/../k8s/ingress.yaml.tpl", {
    origin_secret   = random_password.cloudfront_origin_secret.result
    alb_logs_bucket = "${var.project_name}-alb-logs-${data.aws_caller_identity.current.account_id}"
  })
  filename = "${path.module}/../k8s/ingress.yaml"
}

resource "null_resource" "apply_ingress" {
  triggers = {
    origin_secret    = random_password.cloudfront_origin_secret.result
    ingress_hash     = local_file.ingress_yaml.content_md5
    # クラスター再作成時（endpoint が変化）に必ず再実行して Ingress/ALB を再作成する
    cluster_endpoint = module.eks.cluster_endpoint
  }

  provisioner "local-exec" {
    interpreter = ["PowerShell", "-Command"]
    command     = <<-EOT
      aws eks update-kubeconfig --name ${module.eks.cluster_name} --region ${var.aws_region}
      kubectl apply -f ${path.module}/../k8s/ingress.yaml
      Write-Host "Waiting for ALB to be provisioned..."
      $deadline = (Get-Date).AddMinutes(10)
      $albDns = ""
      while ((Get-Date) -lt $deadline) {
        $albDns = kubectl get ingress service-b -n default -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>$null
        if ($albDns -like "*amazonaws*") { break }
        Write-Host "ALB not yet ready, waiting 15s..."
        Start-Sleep -Seconds 15
      }
      if ($albDns -like "*amazonaws*") {
        Write-Host "ALB provisioned: $albDns"
        kubectl set env deployment/chaos-agent -n chaos "SERVICE_B_URL=http://$albDns/items/1" 2>&1 | Write-Host
        $global:LASTEXITCODE = 0
      } else {
        Write-Host "WARNING: ALB not provisioned within timeout"
      }
    EOT
  }

  depends_on = [aws_eks_addon.coredns, local_file.ingress_yaml]
}

# ---------------------------------------------------------------------------
# ALB 自動検出 — インジケーターで付与したタグで一意に特定する
# apply_ingress が ALB プロビジョニングを待機してから読み取る
# ---------------------------------------------------------------------------

data "aws_lb" "service_b" {
  tags = {
    Project   = "chaos-platform"
    Component = "service-b-alb"
  }

  depends_on = [null_resource.apply_ingress]
}

# ---------------------------------------------------------------------------
# FIS がターゲット解決（Pod 一覧取得）に使う Kubernetes API 呼び出しを許可
resource "aws_eks_access_entry" "fis" {
  cluster_name  = module.eks.cluster_name
  principal_arn = aws_iam_role.fis_execution.arn
  type          = "STANDARD"
  tags          = local.common_tags
}

resource "aws_eks_access_policy_association" "fis" {
  cluster_name  = module.eks.cluster_name
  principal_arn = aws_iam_role.fis_execution.arn
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSEditPolicy"

  access_scope {
    type = "cluster"
  }

  depends_on = [aws_eks_access_entry.fis]
}
