module "eks" {
  source = "./modules/eks"

  project_name       = var.project_name
  kubernetes_version = var.kubernetes_version
  vpc_id             = module.vpc.vpc_id
  public_subnet_ids  = module.vpc.public_subnet_ids
  private_subnet_ids = module.vpc.private_subnet_ids

  tags = local.common_tags
}
