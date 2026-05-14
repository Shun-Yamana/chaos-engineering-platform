resource "aws_dynamodb_table" "slo_definitions" {
  name                        = "${var.project_name}-slo-definitions"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "service_name"
  deletion_protection_enabled = true

  attribute {
    name = "service_name"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = local.common_tags
}

resource "aws_dynamodb_table" "experiment_history" {
  name                        = "${var.project_name}-experiment-history"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "experiment_id"
  range_key                   = "started_at"
  table_class                 = "STANDARD_INFREQUENT_ACCESS"
  deletion_protection_enabled = true

  # experiment_evaluator Lambda のトリガー (ADR 030)
  stream_enabled   = true
  stream_view_type = "NEW_AND_OLD_IMAGES"

  attribute {
    name = "experiment_id"
    type = "S"
  }

  attribute {
    name = "started_at"
    type = "S"
  }

  attribute {
    name = "status"
    type = "S"
  }

  attribute {
    name = "target_service"
    type = "S"
  }

  # auto_stopper が Scan ではなく Query で running 実験を取得するための GSI (ADR 016)
  global_secondary_index {
    name            = "status-target-service-index"
    hash_key        = "status"
    range_key       = "target_service"
    projection_type = "ALL"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = local.common_tags
}

resource "aws_dynamodb_table" "sli_metrics" {
  name                        = "${var.project_name}-sli-metrics"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "service_name"
  range_key                   = "timestamp"
  deletion_protection_enabled = true

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
