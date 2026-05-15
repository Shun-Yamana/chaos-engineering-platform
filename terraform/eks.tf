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
  # cluster_endpoint にはクラスター固有ハッシュが含まれるため、
  # destroy+apply でクラスターが再作成されると必ずトリガーされる
  triggers = {
    cluster_endpoint = module.eks.cluster_endpoint
  }

  provisioner "local-exec" {
    command = <<-EOT
      aws eks update-kubeconfig --name ${module.eks.cluster_name} --region ${var.aws_region}
      echo "Waiting for coredns deployment to appear..."
      until kubectl get deployment coredns -n kube-system >/dev/null 2>&1; do
        echo "coredns deployment not yet available, retrying in 15s..."
        sleep 15
      done
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

# addon 作成後に CoreDNS を再起動して Fargate ノードへのスケジューリングを確定させる
# patch_coredns_fargate は addon より先に実行されるが、addon が Deployment を上書きするため
# addon 作成後にも patch + rollout restart が必要
resource "null_resource" "restart_coredns_after_addon" {
  triggers = {
    addon_arn        = aws_eks_addon.coredns.arn
    cluster_endpoint = module.eks.cluster_endpoint
  }

  provisioner "local-exec" {
    command = <<-EOT
      aws eks update-kubeconfig --name ${module.eks.cluster_name} --region ${var.aws_region}
      kubectl patch deployment coredns -n kube-system --type json \
        -p '[{"op":"remove","path":"/spec/template/metadata/annotations/eks.amazonaws.com~1compute-type"}]' || true
      kubectl rollout restart deployment/coredns -n kube-system
      kubectl rollout status deployment/coredns -n kube-system --timeout=300s
    EOT
  }

  depends_on = [aws_eks_addon.coredns]
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
    command = <<-EOT
      aws eks update-kubeconfig --name ${module.eks.cluster_name} --region ${var.aws_region}
      kubectl apply -f ${path.module}/../k8s/ingress.yaml
      echo "Waiting for ALB to be provisioned..."
      until kubectl get ingress service-b -n default -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null | grep -q amazonaws; do
        echo "ALB not yet ready, waiting 15s..."
        sleep 15
      done
      ALB_DNS=$(kubectl get ingress service-b -n default -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')
      echo "ALB provisioned: $ALB_DNS"
      kubectl set env deployment/chaos-agent -n chaos SERVICE_B_URL="http://$ALB_DNS/items/1" || true
    EOT
  }

  depends_on = [null_resource.restart_coredns_after_addon, local_file.ingress_yaml]
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
