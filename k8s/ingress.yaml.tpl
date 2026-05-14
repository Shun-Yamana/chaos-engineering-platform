# Ingress — service-b (ADR 012: CloudFront → ALB(internet-facing) → service-b)
# このファイルは terraform apply で生成される。直接編集しないこと。
# テンプレート: k8s/ingress.yaml.tpl
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: service-b
  namespace: default
  annotations:
    kubernetes.io/ingress.class: alb
    alb.ingress.kubernetes.io/scheme: internet-facing
    alb.ingress.kubernetes.io/target-type: ip

    # ALB に固定タグを付与することで data "aws_lb" がタグ検索で一意に特定できる
    alb.ingress.kubernetes.io/tags: "Project=chaos-platform,Component=service-b-alb"

    # ALB 本体属性
    alb.ingress.kubernetes.io/load-balancer-attributes: "access_logs.s3.enabled=true,access_logs.s3.bucket=${alb_logs_bucket},access_logs.s3.prefix=service-b,connection_logs.s3.enabled=true,connection_logs.s3.bucket=${alb_logs_bucket},connection_logs.s3.prefix=service-b,routing.http.drop_invalid_header_fields.enabled=true"

    # リスナー属性: Server ヘッダーを非表示（サーバーフィンガープリント防止）
    alb.ingress.kubernetes.io/listener-attributes: "routing.http.response.server.enabled=false"

    # ターゲットグループ属性: 登録解除待機を 300s → 30s に短縮（Pod Kill 実験後の回復速度向上）
    alb.ingress.kubernetes.io/target-group-attributes: "deregistration_delay.timeout_seconds=30"

    alb.ingress.kubernetes.io/listen-ports: '[{"HTTP": 80}]'

    # ヘルスチェック高速化: 30s/3回 → 5s/2回（10s で異常判定、カオス実験の精度向上）
    alb.ingress.kubernetes.io/healthcheck-path: /health
    alb.ingress.kubernetes.io/healthcheck-interval-seconds: "5"
    alb.ingress.kubernetes.io/healthcheck-timeout-seconds: "3"
    alb.ingress.kubernetes.io/healthy-threshold-count: "3"
    alb.ingress.kubernetes.io/unhealthy-threshold-count: "2"

    # CloudFront origin 認証: X-Origin-Verify ヘッダーがない場合はルーティングしない
    alb.ingress.kubernetes.io/conditions.service-b: '[{"field":"http-header","httpHeaderConfig":{"httpHeaderName":"X-Origin-Verify","values":["${origin_secret}"]}}]'
spec:
  rules:
    - http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: service-b
                port:
                  number: 8000
