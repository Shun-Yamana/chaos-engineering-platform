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
      }
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

  environment {
    variables = {
      PROJECT_NAME     = var.project_name
      SLI_TABLE        = aws_dynamodb_table.sli_metrics.name
      SLO_TABLE        = aws_dynamodb_table.slo_definitions.name
      WINDOW_MINUTES   = "1"
    }
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
