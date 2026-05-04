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
      ALB_ARN_SUFFIX = var.alb_arn_suffix
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
