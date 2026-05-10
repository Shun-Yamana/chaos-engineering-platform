resource "aws_sns_topic" "chaos_alerts" {
  name              = "${var.project_name}-alerts"
  kms_master_key_id = "alias/aws/sns"

  # Lambda・HTTPS(Slack) 両サブスクリプションの配信失敗を CloudWatch Logs に記録
  lambda_failure_feedback_role_arn = aws_iam_role.sns_feedback.arn
  http_failure_feedback_role_arn   = aws_iam_role.sns_feedback.arn

  # Slack への配信を粘り強くリトライ
  # 3回 → 20s固定、その後指数バックオフで最大300s、70回は300s固定 (合計100回)
  delivery_policy = jsonencode({
    http = {
      defaultHealthyRetryPolicy = {
        minDelayTarget     = 20
        maxDelayTarget     = 300
        numRetries         = 100
        numNoDelayRetries  = 0
        numMinDelayRetries = 3
        numMaxDelayRetries = 70
        backoffFunction    = "exponential"
      }
      disableSubscriptionOverrides = false
    }
  })

  tags = local.common_tags
}

# CloudWatch と auto_stopper Lambda のみ Publish を許可
resource "aws_sns_topic_policy" "chaos_alerts" {
  arn = aws_sns_topic.chaos_alerts.arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowOwnerManage"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
        }
        Action   = "sns:*"
        Resource = aws_sns_topic.chaos_alerts.arn
      },
      {
        Sid       = "AllowCloudWatchPublish"
        Effect    = "Allow"
        Principal = { Service = "cloudwatch.amazonaws.com" }
        Action    = "sns:Publish"
        Resource  = aws_sns_topic.chaos_alerts.arn
        Condition = {
          ArnLike = {
            "aws:SourceArn" = "arn:aws:cloudwatch:${var.aws_region}:${data.aws_caller_identity.current.account_id}:alarm:*"
          }
        }
      },
      {
        Sid    = "AllowAutoStopperPublish"
        Effect = "Allow"
        Principal = {
          AWS = aws_iam_role.lambda_auto_stopper.arn
        }
        Action   = "sns:Publish"
        Resource = aws_sns_topic.chaos_alerts.arn
      },
    ]
  })
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
