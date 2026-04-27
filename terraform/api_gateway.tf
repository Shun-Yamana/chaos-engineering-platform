data "archive_file" "api_handler" {
  type        = "zip"
  source_file = "${path.module}/../lambda/api_handler.py"
  output_path = "${path.module}/../lambda/api_handler.zip"
}

resource "aws_iam_role" "lambda_api_handler" {
  name               = "${var.project_name}-lambda-api-handler"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "api_handler_basic_execution" {
  role       = aws_iam_role.lambda_api_handler.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_policy" "lambda_api_handler_policy" {
  name = "${var.project_name}-lambda-api-handler-policy"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:Query",
          "dynamodb:Scan"
        ]
        Resource = [
          aws_dynamodb_table.experiment_history.arn,
          aws_dynamodb_table.slo_definitions.arn,
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = aws_lambda_function.sli_calculator.arn
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "api_handler_policy" {
  role       = aws_iam_role.lambda_api_handler.name
  policy_arn = aws_iam_policy.lambda_api_handler_policy.arn
}

resource "aws_lambda_function" "api_handler" {
  filename         = data.archive_file.api_handler.output_path
  function_name    = "${var.project_name}-api-handler"
  role             = aws_iam_role.lambda_api_handler.arn
  handler          = "api_handler.handler"
  runtime          = "python3.13"
  source_code_hash = data.archive_file.api_handler.output_base64sha256
  timeout          = 30

  environment {
    variables = {
      PROJECT_NAME       = var.project_name
      EXPERIMENT_TABLE   = aws_dynamodb_table.experiment_history.name
      SLO_TABLE          = aws_dynamodb_table.slo_definitions.name
      CHAOS_AGENT_FUNCTION = aws_lambda_function.sli_calculator.function_name
    }
  }

  tags = local.common_tags
}

resource "aws_apigatewayv2_api" "chaos" {
  name          = "${var.project_name}-api"
  protocol_type = "HTTP"

  tags = local.common_tags
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.chaos.id
  name        = "$default"
  auto_deploy = true
}

resource "aws_apigatewayv2_integration" "api_handler" {
  api_id                 = aws_apigatewayv2_api.chaos.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api_handler.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "post_experiments" {
  api_id    = aws_apigatewayv2_api.chaos.id
  route_key = "POST /experiments"
  target    = "integrations/${aws_apigatewayv2_integration.api_handler.id}"
}

resource "aws_apigatewayv2_route" "delete_experiment" {
  api_id    = aws_apigatewayv2_api.chaos.id
  route_key = "DELETE /experiments/{id}"
  target    = "integrations/${aws_apigatewayv2_integration.api_handler.id}"
}

resource "aws_apigatewayv2_route" "get_experiments" {
  api_id    = aws_apigatewayv2_api.chaos.id
  route_key = "GET /experiments"
  target    = "integrations/${aws_apigatewayv2_integration.api_handler.id}"
}

resource "aws_apigatewayv2_route" "get_experiment" {
  api_id    = aws_apigatewayv2_api.chaos.id
  route_key = "GET /experiments/{id}"
  target    = "integrations/${aws_apigatewayv2_integration.api_handler.id}"
}

resource "aws_lambda_permission" "api_gateway" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api_handler.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.chaos.execution_arn}/*/*"
}
