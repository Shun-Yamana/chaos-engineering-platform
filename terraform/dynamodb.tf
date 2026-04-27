resource "aws_dynamodb_table" "slo_definitions" {
  name         = "${var.project_name}-slo-definitions"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "service_name"

  attribute {
    name = "service_name"
    type = "S"
  }

  tags = local.common_tags
}

resource "aws_dynamodb_table" "experiment_history" {
  name         = "${var.project_name}-experiment-history"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "experiment_id"
  range_key    = "started_at"

  attribute {
    name = "experiment_id"
    type = "S"
  }

  attribute {
    name = "started_at"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  tags = local.common_tags
}

resource "aws_dynamodb_table" "sli_metrics" {
  name         = "${var.project_name}-sli-metrics"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "service_name"
  range_key    = "timestamp"

  attribute {
    name = "service_name"
    type = "S"
  }

  attribute {
    name = "timestamp"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  tags = local.common_tags
}
