resource "aws_sns_topic" "chaos_alerts" {
  name = "${var.project_name}-alerts"

  tags = local.common_tags
}

resource "aws_sns_topic_subscription" "slack_webhook" {
  topic_arn = aws_sns_topic.chaos_alerts.arn
  protocol  = "https"
  endpoint  = var.slack_webhook_url
}

# CloudWatch Alarm → SNS → auto-stopper Lambda (ADR 012: http_error_inject は自動停止)
resource "aws_sns_topic_subscription" "auto_stopper" {
  topic_arn = aws_sns_topic.chaos_alerts.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.auto_stopper.arn
}
