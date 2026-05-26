variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "ap-northeast-1"
}

variable "project_name" {
  description = "Project name used as prefix for all resources"
  type        = string
  default     = "chaos-platform"
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "prod"
}

variable "vpc_cidr" {
  description = "CIDR block for VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "availability_zones" {
  description = "Availability zones"
  type        = list(string)
  default     = ["ap-northeast-1a", "ap-northeast-1c"]
}

variable "public_subnet_cidrs" {
  description = "CIDR blocks for public subnets (ALB, NAT GW)"
  type        = list(string)
  default     = ["10.0.0.0/24", "10.0.1.0/24"]
}

variable "private_subnet_cidrs" {
  description = "CIDR blocks for private subnets. /20 per AZ to avoid ENI exhaustion on Fargate (ADR 011 item 3)"
  type        = list(string)
  default     = ["10.0.16.0/20", "10.0.32.0/20"]
}

variable "kubernetes_version" {
  description = "Kubernetes version for EKS"
  type        = string
  default     = "1.32"
}

variable "eks_public_access_cidrs" {
  description = "CIDR blocks allowed to reach the public EKS API endpoint. Restrict to GitHub Actions IP ranges in production."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "github_repo" {
  description = "GitHub repository in owner/repo format. Used to scope the OIDC trust policy (e.g. 'yamas/chaos-platform')."
  type        = string
}

variable "slack_webhook_url" {
  description = "Slack incoming webhook URL for chaos alerts (optional)"
  type        = string
  sensitive   = true
  default     = ""
}

variable "frontend_url" {
  description = "CloudFront frontend URL for Cognito callback and CORS (e.g. https://xxx.cloudfront.net). Set after frontend distribution is created."
  type        = string
  default     = "https://localhost:5173"
}

variable "cognito_test_user_password" {
  description = "Password for the demo Cognito user. Must satisfy the password policy (min 12, upper/lower/number/symbol)."
  type        = string
  sensitive   = true
}

