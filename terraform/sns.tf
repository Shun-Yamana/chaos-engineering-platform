resource "aws_sns_topic" "chaos_alerts" {
  name = "${var.project_name}-alerts"

  tags = local.common_tags
}

resource "aws_sns_topic_subscription" "slack_webhook" {
  topic_arn = aws_sns_topic.chaos_alerts.arn
  protocol  = "https"
  endpoint  = var.slack_webhook_url
}
