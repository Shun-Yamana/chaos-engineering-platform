output "cluster_name" {
  description = "EKS cluster name"
  value       = aws_eks_cluster.main.name
}

output "cluster_endpoint" {
  description = "EKS cluster endpoint"
  value       = aws_eks_cluster.main.endpoint
}

output "cluster_ca_certificate" {
  description = "EKS cluster CA certificate (base64 encoded)"
  value       = aws_eks_cluster.main.certificate_authority[0].data
}

output "fargate_pod_execution_role_name" {
  description = "Fargate pod execution IAM role name"
  value       = aws_iam_role.fargate_pod_execution.name
}

output "oidc_provider_arn" {
  description = "OIDC provider ARN for IRSA"
  value       = aws_iam_openid_connect_provider.eks.arn
}

output "oidc_provider" {
  description = "OIDC provider URL without https:// (used in trust policy condition key)"
  value       = replace(aws_eks_cluster.main.identity[0].oidc[0].issuer, "https://", "")
}

output "pod_eni_sg_id" {
  description = "Security group ID for Fargate Pod ENI (② Pod ENI SG)"
  value       = aws_security_group.pod_eni.id
}
