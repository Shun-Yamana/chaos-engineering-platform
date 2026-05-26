# ---------------------------------------------------------------------------
# 共有 DLQ — 全 Lambda 失敗イベントの退避 (ADR 014)
# ---------------------------------------------------------------------------

resource "aws_sqs_queue" "lambda_dlq" {
  name                      = "${var.project_name}-lambda-dlq"
  message_retention_seconds = 1209600 # 14 日
  tags                      = local.common_tags
}

data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "lambda_sli_calculator" {
  name               = "${var.project_name}-lambda-sli-calculator"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "lambda_basic_execution" {
  role       = aws_iam_role.lambda_sli_calculator.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "sli_calculator_xray" {
  role       = aws_iam_role.lambda_sli_calculator.name
  policy_arn = "arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess"
}

resource "aws_iam_policy" "lambda_sli_policy" {
  name = "${var.project_name}-lambda-sli-policy"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "cloudwatch:GetMetricStatistics",
          "cloudwatch:ListMetrics"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:Query"
        ]
        Resource = [
          aws_dynamodb_table.slo_definitions.arn,
          aws_dynamodb_table.sli_metrics.arn,
          aws_dynamodb_table.experiment_history.arn,
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.lambda_dlq.arn
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_sli_policy" {
  role       = aws_iam_role.lambda_sli_calculator.name
  policy_arn = aws_iam_policy.lambda_sli_policy.arn
}

data "archive_file" "sli_calculator" {
  type        = "zip"
  source_file = "${path.module}/../lambda/sli_calculator.py"
  output_path = "${path.module}/../lambda/sli_calculator.zip"
}

resource "aws_lambda_function" "sli_calculator" {
  filename         = data.archive_file.sli_calculator.output_path
  function_name    = "${var.project_name}-sli-calculator"
  role             = aws_iam_role.lambda_sli_calculator.arn
  handler          = "sli_calculator.handler"
  runtime          = "python3.13"
  source_code_hash = data.archive_file.sli_calculator.output_base64sha256
  timeout          = 30
  memory_size      = 256
  architectures    = ["arm64"]

  environment {
    variables = {
      PROJECT_NAME   = var.project_name
      SLI_TABLE      = aws_dynamodb_table.sli_metrics.name
      SLO_TABLE      = aws_dynamodb_table.slo_definitions.name
      WINDOW_MINUTES = "1"
      ALB_ARN_SUFFIX = data.aws_lb.service_b.arn_suffix
    }
  }

  tracing_config {
    mode = "Active"
  }

  dead_letter_config {
    target_arn = aws_sqs_queue.lambda_dlq.arn
  }

  logging_config {
    log_format            = "JSON"
    application_log_level = "INFO"
    system_log_level      = "WARN"
    log_group             = "/aws/lambda/${var.project_name}-sli-calculator"
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_event_rule" "sli_calculator_schedule" {
  name                = "${var.project_name}-sli-calculator-schedule"
  description         = "Trigger SLI calculator every minute"
  schedule_expression = "rate(1 minute)"

  tags = local.common_tags
}

resource "aws_cloudwatch_event_target" "sli_calculator" {
  rule      = aws_cloudwatch_event_rule.sli_calculator_schedule.name
  target_id = "sli-calculator"
  arn       = aws_lambda_function.sli_calculator.arn
}

resource "aws_lambda_permission" "allow_eventbridge" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.sli_calculator.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.sli_calculator_schedule.arn
}

# --- auto_stopper ---

resource "aws_iam_role" "lambda_auto_stopper" {
  name               = "${var.project_name}-lambda-auto-stopper"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "auto_stopper_basic_execution" {
  role       = aws_iam_role.lambda_auto_stopper.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "auto_stopper_xray" {
  role       = aws_iam_role.lambda_auto_stopper.name
  policy_arn = "arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess"
}

resource "aws_iam_policy" "lambda_auto_stopper_policy" {
  name = "${var.project_name}-lambda-auto-stopper-policy"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:UpdateItem",
          "dynamodb:Query",
          "dynamodb:Scan"
        ]
        Resource = [
          aws_dynamodb_table.slo_definitions.arn,
          aws_dynamodb_table.sli_metrics.arn,
          aws_dynamodb_table.experiment_history.arn,
          "${aws_dynamodb_table.experiment_history.arn}/index/*",
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = aws_sns_topic.chaos_alerts.arn
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.lambda_dlq.arn
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "auto_stopper_policy" {
  role       = aws_iam_role.lambda_auto_stopper.name
  policy_arn = aws_iam_policy.lambda_auto_stopper_policy.arn
}

data "archive_file" "auto_stopper" {
  type        = "zip"
  source_file = "${path.module}/../lambda/auto_stopper.py"
  output_path = "${path.module}/../lambda/auto_stopper.zip"
}

resource "aws_lambda_function" "auto_stopper" {
  filename         = data.archive_file.auto_stopper.output_path
  function_name    = "${var.project_name}-auto-stopper"
  role             = aws_iam_role.lambda_auto_stopper.arn
  handler          = "auto_stopper.handler"
  runtime          = "python3.13"
  source_code_hash = data.archive_file.auto_stopper.output_base64sha256
  timeout          = 30
  memory_size      = 128
  architectures    = ["arm64"]

  environment {
    variables = {
      PROJECT_NAME     = var.project_name
      SLI_TABLE        = aws_dynamodb_table.sli_metrics.name
      SLO_TABLE        = aws_dynamodb_table.slo_definitions.name
      EXPERIMENT_TABLE = aws_dynamodb_table.experiment_history.name
      SNS_TOPIC_ARN    = aws_sns_topic.chaos_alerts.arn
    }
  }

  tracing_config {
    mode = "Active"
  }

  dead_letter_config {
    target_arn = aws_sqs_queue.lambda_dlq.arn
  }

  logging_config {
    log_format            = "JSON"
    application_log_level = "INFO"
    system_log_level      = "WARN"
    log_group             = "/aws/lambda/${var.project_name}-auto-stopper"
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_event_rule" "auto_stopper_schedule" {
  name                = "${var.project_name}-auto-stopper-schedule"
  description         = "Trigger auto stopper every minute"
  schedule_expression = "rate(1 minute)"

  tags = local.common_tags
}

resource "aws_cloudwatch_event_target" "auto_stopper" {
  rule      = aws_cloudwatch_event_rule.auto_stopper_schedule.name
  target_id = "auto-stopper"
  arn       = aws_lambda_function.auto_stopper.arn
}

resource "aws_lambda_permission" "auto_stopper_eventbridge" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.auto_stopper.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.auto_stopper_schedule.arn
}

# SNS → auto-stopper (CloudWatch Alarm 発火時の即時トリガー)
resource "aws_lambda_permission" "auto_stopper_sns" {
  statement_id  = "AllowExecutionFromSNS"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.auto_stopper.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.chaos_alerts.arn
}

# ---------------------------------------------------------------------------
# experiment_evaluator — DynamoDB Stream トリガー (ADR 030)
# 実験 completed/stopped 後に 2 フェーズ PASS/FAIL 判定を実行する
# ---------------------------------------------------------------------------

resource "aws_iam_role" "lambda_experiment_evaluator" {
  name               = "${var.project_name}-lambda-experiment-evaluator"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "evaluator_basic_execution" {
  role       = aws_iam_role.lambda_experiment_evaluator.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "evaluator_xray" {
  role       = aws_iam_role.lambda_experiment_evaluator.name
  policy_arn = "arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess"
}

# DynamoDB Streams の読み取り権限
resource "aws_iam_role_policy_attachment" "evaluator_dynamodb_stream" {
  role       = aws_iam_role.lambda_experiment_evaluator.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaDynamoDBExecutionRole"
}

resource "aws_iam_policy" "lambda_evaluator_policy" {
  name = "${var.project_name}-lambda-evaluator-policy"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "cloudwatch:GetMetricStatistics",
          "cloudwatch:ListMetrics",
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:UpdateItem",
          "dynamodb:Query",
        ]
        Resource = [
          aws_dynamodb_table.experiment_history.arn,
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.lambda_dlq.arn
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "evaluator_policy" {
  role       = aws_iam_role.lambda_experiment_evaluator.name
  policy_arn = aws_iam_policy.lambda_evaluator_policy.arn
}

data "archive_file" "experiment_evaluator" {
  type        = "zip"
  source_file = "${path.module}/../lambda/experiment_evaluator.py"
  output_path = "${path.module}/../lambda/experiment_evaluator.zip"
}

resource "aws_lambda_function" "experiment_evaluator" {
  filename         = data.archive_file.experiment_evaluator.output_path
  function_name    = "${var.project_name}-experiment-evaluator"
  role             = aws_iam_role.lambda_experiment_evaluator.arn
  handler          = "experiment_evaluator.handler"
  runtime          = "python3.13"
  source_code_hash = data.archive_file.experiment_evaluator.output_base64sha256
  # CloudWatch 遅延バッファ 5 分 + 評価処理 = 最大 10 分
  timeout          = 600
  memory_size      = 256
  architectures    = ["arm64"]

  environment {
    variables = {
      EXPERIMENT_TABLE   = aws_dynamodb_table.experiment_history.name
      ALB_ARN_SUFFIX     = data.aws_lb.service_b.arn_suffix
      CW_BUFFER_SECONDS  = "300"
      SLACK_WEBHOOK_URL  = var.slack_webhook_url
      EKS_CLUSTER_NAME   = module.eks.cluster_name
    }
  }

  tracing_config {
    mode = "Active"
  }

  dead_letter_config {
    target_arn = aws_sqs_queue.lambda_dlq.arn
  }

  logging_config {
    log_format            = "JSON"
    application_log_level = "INFO"
    system_log_level      = "WARN"
    log_group             = "/aws/lambda/${var.project_name}-experiment-evaluator"
  }

  tags = local.common_tags
}

# DynamoDB Stream → experiment_evaluator
# status = "completed" or "stopped" のアイテム変更のみフィルタリング
resource "aws_lambda_event_source_mapping" "evaluator_dynamodb_stream" {
  event_source_arn  = aws_dynamodb_table.experiment_history.stream_arn
  function_name     = aws_lambda_function.experiment_evaluator.arn
  starting_position = "LATEST"
  batch_size        = 1

  filter_criteria {
    filter {
      pattern = jsonencode({
        dynamodb = {
          NewImage = {
            status = { S = ["completed", "stopped"] }
          }
        }
      })
    }
  }

  destination_config {
    on_failure {
      destination_arn = aws_sqs_queue.lambda_dlq.arn
    }
  }

  depends_on = [aws_iam_role_policy_attachment.evaluator_dynamodb_stream]
}

# ---------------------------------------------------------------------------
# grafana_annotator — DynamoDB Streams → Grafana Annotation API (ADR 053)
# ---------------------------------------------------------------------------

resource "aws_iam_role" "grafana_annotator" {
  name               = "${var.project_name}-grafana-annotator"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
  tags               = local.common_tags
}

resource "aws_iam_role_policy_attachment" "grafana_annotator_basic" {
  role       = aws_iam_role.grafana_annotator.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "grafana_annotator_stream" {
  name = "dynamodb-stream-read"
  role = aws_iam_role.grafana_annotator.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetRecords",
          "dynamodb:GetShardIterator",
          "dynamodb:DescribeStream",
          "dynamodb:ListStreams",
        ]
        Resource = aws_dynamodb_table.experiment_history.stream_arn
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.lambda_dlq.arn
      },
    ]
  })
}

data "archive_file" "grafana_annotator" {
  type        = "zip"
  source_file = "${path.module}/../lambda/grafana_annotator.py"
  output_path = "${path.module}/../lambda/grafana_annotator.zip"
}

resource "aws_lambda_function" "grafana_annotator" {
  function_name    = "${var.project_name}-grafana-annotator"
  role             = aws_iam_role.grafana_annotator.arn
  handler          = "grafana_annotator.handler"
  runtime          = "python3.12"
  filename         = data.archive_file.grafana_annotator.output_path
  source_code_hash = data.archive_file.grafana_annotator.output_base64sha256
  timeout          = 30

  environment {
    variables = {
      GRAFANA_URL             = var.grafana_url
      GRAFANA_TOKEN           = var.grafana_token
      ANNOTATION_DASHBOARD_UID = "chaos-experiment"
    }
  }

  dead_letter_config {
    target_arn = aws_sqs_queue.lambda_dlq.arn
  }

  tags = local.common_tags
}

resource "aws_lambda_event_source_mapping" "grafana_annotator_stream" {
  event_source_arn  = aws_dynamodb_table.experiment_history.stream_arn
  function_name     = aws_lambda_function.grafana_annotator.arn
  starting_position = "LATEST"
  batch_size        = 10

  # 注釈は running / completed / stopped / failed のみ対象
  filter_criteria {
    filter {
      pattern = jsonencode({
        dynamodb = {
          NewImage = {
            status = { S = ["running", "completed", "stopped", "failed"] }
          }
        }
      })
    }
  }

  destination_config {
    on_failure {
      destination_arn = aws_sqs_queue.lambda_dlq.arn
    }
  }

  depends_on = [aws_iam_role_policy.grafana_annotator_stream]
}
