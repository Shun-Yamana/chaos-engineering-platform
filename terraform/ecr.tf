locals {
  ecr_lifecycle_policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Delete untagged images after 1 day"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 1
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Keep last 10 tagged images"
        selection = {
          tagStatus   = "tagged"
          tagPrefixList = ["v", "sha-"]
          countType   = "imageCountMoreThan"
          countNumber = 10
        }
        action = { type = "expire" }
      },
    ]
  })
}

resource "aws_ecr_repository" "service_a" {
  name                 = "${var.project_name}/service-a"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = local.common_tags
}

resource "aws_ecr_lifecycle_policy" "service_a" {
  repository = aws_ecr_repository.service_a.name
  policy     = local.ecr_lifecycle_policy
}

resource "aws_ecr_repository" "service_b" {
  name                 = "${var.project_name}/service-b"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = local.common_tags
}

resource "aws_ecr_lifecycle_policy" "service_b" {
  repository = aws_ecr_repository.service_b.name
  policy     = local.ecr_lifecycle_policy
}

resource "aws_ecr_repository" "chaos_agent" {
  name                 = "${var.project_name}/chaos-agent"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = local.common_tags
}

resource "aws_ecr_lifecycle_policy" "chaos_agent" {
  repository = aws_ecr_repository.chaos_agent.name
  policy     = local.ecr_lifecycle_policy
}
