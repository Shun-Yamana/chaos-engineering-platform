# Chaos Engineering Platform

障害を意図的に注入し、システムの自己回復能力と SLO への影響を自動計測・停止するプラットフォーム。

## Architecture

```
┌─────────────────────────────────────┐
│  Chaos Control Plane                │
│  Python CLI (Click) / API Gateway   │
│  実験定義 (YAML) / 実行 / 停止 / 履歴│
└──────────────┬──────────────────────┘
               │ 障害注入命令
┌──────────────▼──────────────────────┐
│  Target: EKS Fargate                │
│  service-a (FastAPI)                │
│  service-b (FastAPI, service-a依存) │
│  ・Pod kill                         │
│  ・CPU stress (stress-ng sidecar)   │
└──────────────┬──────────────────────┘
               │ メトリクス
┌──────────────▼──────────────────────┐
│  Observability                      │
│  CloudWatch Container Insights      │
│  Lambda (SLI計算 / バーン率計算)     │
│  DynamoDB (SLO定義 / 実験履歴)      │
│  EventBridge → SNS → Slack通知      │
└─────────────────────────────────────┘
```

## Tech Stack

| Category | Technology |
|---|---|
| Language | Python 3.13 |
| Infrastructure | Terraform v1.8.5 |
| Container Orchestration | EKS Fargate |
| Fault Injection Targets | FastAPI × 2 |
| Fault Types | Pod kill / CPU stress (stress-ng) |
| Observability | CloudWatch Container Insights |
| SLO Management | Lambda + DynamoDB |
| Alerting | EventBridge → SNS → Slack |
| CI/CD | GitHub Actions |

## Quick Start

### Prerequisites

- AWS CLI v2 configured (`aws configure`)
- Terraform v1.8.5+
- kubectl v1.31+
- Docker

### 1. Bootstrap Terraform Backend

```bash
cd terraform/bootstrap
terraform init
terraform apply
```

### 2. Deploy Infrastructure

```bash
cd terraform
terraform init
terraform apply -var="slack_webhook_url=https://hooks.slack.com/..."
```

### 3. Build & Push Docker Images

```bash
AWS_ACCOUNT_ID=203553641035
AWS_REGION=ap-northeast-1
ECR_BASE=$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/chaos-platform

aws ecr get-login-password | docker login --username AWS --password-stdin $ECR_BASE

docker build -t $ECR_BASE/service-a:latest ./services/service-a
docker push $ECR_BASE/service-a:latest

docker build -t $ECR_BASE/service-b:latest ./services/service-b
docker push $ECR_BASE/service-b:latest
```

### 4. Deploy to EKS

```bash
aws eks update-kubeconfig --name chaos-platform-cluster --region ap-northeast-1

IMAGE_A=$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/chaos-platform/service-a:latest
IMAGE_B=$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/chaos-platform/service-b:latest

sed "s|SERVICE_A_IMAGE|$IMAGE_A|g" k8s/service-a.yaml | kubectl apply -f -
sed "s|SERVICE_B_IMAGE|$IMAGE_B|g" k8s/service-b.yaml | kubectl apply -f -

kubectl get pods
```

### 5. Run a Chaos Experiment

```bash
cd chaos
pip install -r requirements.txt

# API経由で実行
export CHAOS_API_ENDPOINT=$(cd ../terraform && terraform output -raw api_endpoint)
python cli.py run experiments/pod-kill-example.yaml

# 実験履歴を確認
python cli.py history

# 実験を手動停止
python cli.py stop <experiment_id>
```

## Experiment Definition

```yaml
name: pod-kill-service-a
target:
  namespace: default
  service: service-a
fault:
  type: pod_kill        # pod_kill | cpu_stress
  duration: 60
slo:
  error_rate_threshold: 0.05   # エラーレート 5% 超で自動停止
  burn_rate_threshold: 2.0     # バーン率 2倍 超で自動停止
notify:
  slack_webhook: "${SLACK_WEBHOOK_URL}"
```

## Auto-Stop Flow

```
EventBridge (1分ごと)
  → sli_calculator Lambda
      → CloudWatch からエラーレート取得
      → バーン率計算 → DynamoDB 保存
  → auto_stopper Lambda
      → バーン率 > 閾値？
          → 実験ステータスを stopped に更新
          → SNS → Slack 通知
```

## GitHub Actions

| Workflow | Trigger | Action |
|---|---|---|
| `terraform.yml` | PR to main | `terraform plan` → PR コメント |
| `terraform.yml` | Push to main | `terraform apply` |
| `deploy.yml` | Push to main (services/k8s 変更) | ECR push → kubectl apply |

## GitHub Secrets

| Secret | Description |
|---|---|
| `AWS_ACCESS_KEY_ID` | AWS アクセスキー |
| `AWS_SECRET_ACCESS_KEY` | AWS シークレットキー |
| `SLACK_WEBHOOK_URL` | Slack Incoming Webhook URL |

## ADR

- [001 - EKS Fargate 採用](docs/adr/001-eks-fargate.md)
- [002 - CloudWatch over Prometheus](docs/adr/002-cloudwatch-over-prometheus.md)
- [003 - SLO バーン率閾値設計](docs/adr/003-slo-burn-rate-threshold.md)
