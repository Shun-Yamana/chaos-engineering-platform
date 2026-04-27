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

output "fargate_profile_name" {
  description = "Fargate profile name"
  value       = aws_eks_fargate_profile.default.fargate_profile_name
}

output "fargate_pod_execution_role_name" {
  description = "Fargate pod execution IAM role name"
  value       = aws_iam_role.fargate_pod_execution.name
}
