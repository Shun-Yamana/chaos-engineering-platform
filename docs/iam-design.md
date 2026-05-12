# IAM 設計ドキュメント

## IAM ロール全体像

| ロール名 | 使うコンポーネント | 一言でいうと |
|---------|-----------------|------------|
| `chaos-fargate-execution-role` | EKS Fargate（全 Pod） | Pod を起動するために Fargate 自身が使うロール |
| `chaos-agent-role`（IRSA） | chaos/agent.py（Pod） | chaos agent が AWS API を叩くためのロール |
| `fis-execution-role` | AWS FIS サービス | FIS が Pod にフォルトを注入するためのロール |
| `lambda-api-handler-role` | lambda/api_handler.py | 実験の CRUD + chaos-agent Lambda 起動 |
| `lambda-sli-calculator-role` | lambda/sli_calculator.py | CloudWatch からメトリクス取得・SLI 保存 |
| `lambda-auto-stopper-role` | lambda/auto_stopper.py | SLI 違反検知・実験停止・SNS アラート送信 |
| `github-actions-role`（OIDC） | GitHub Actions | CI/CD でのデプロイ・インフラ更新 |

---

## 1. chaos-fargate-execution-role

### 何に使うか

EKS Fargate が Pod を起動するときに AWS が内部で使うロール。Pod が ECR からイメージを pull し、ログを CloudWatch Logs に送るために必要。

### Trust Policy

```json
{
  "Principal": { "Service": "eks-fargate-pods.amazonaws.com" },
  "Action": "sts:AssumeRole"
}
```

### Permission Policy

```json
{
  "Statement": [
    {
      "Sid": "ECRAuth",
      "Action": ["ecr:GetAuthorizationToken"],
      "Resource": "*"
    },
    {
      "Sid": "ECRPull",
      "Action": [
        "ecr:BatchCheckLayerAvailability",
        "ecr:GetDownloadUrlForLayer",
        "ecr:BatchGetImage"
      ],
      "Resource": "arn:aws:ecr:<REGION>:<ACCOUNT_ID>:repository/chaos-*"
    },
    {
      "Sid": "CloudWatchLogs",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "logs:DescribeLogStreams"
      ],
      "Resource": "arn:aws:logs:<REGION>:<ACCOUNT_ID>:log-group:/aws/eks/chaos-cluster/*"
    }
  ]
}
```

> `ecr:GetAuthorizationToken` は AWS の仕様でリソースレベル制限をサポートしていないため `*` のまま（変えられない）。それ以外は絞っている。

---

## 2. chaos-agent-role（IRSA）

### 何に使うか

`chaos/agent.py` が動く Pod のロール。K8s API は RBAC で制御されるが、**AWS API（FIS・DynamoDB）の呼び出しはこのロールで制御する。**

### Trust Policy

```json
{
  "Principal": {
    "Federated": "arn:aws:iam::<ACCOUNT_ID>:oidc-provider/<EKS_OIDC_PROVIDER>"
  },
  "Action": "sts:AssumeRoleWithWebIdentity",
  "Condition": {
    "StringEquals": {
      "<EKS_OIDC_PROVIDER>:sub": "system:serviceaccount:chaos:chaos-agent"
    }
  }
}
```

### Permission Policy

```json
{
  "Statement": [
    {
      "Sid": "DynamoDB",
      "Action": ["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:UpdateItem"],
      "Resource": "arn:aws:dynamodb:<REGION>:<ACCOUNT_ID>:table/chaos-experiments"
    },
    {
      "Sid": "FISStart",
      "Action": ["fis:StartExperiment"],
      "Resource": "arn:aws:fis:<REGION>:<ACCOUNT_ID>:experiment-template/*",
      "Condition": {
        "StringEquals": {
          "aws:ResourceTag/Project": "chaos-platform"
        }
      }
    },
    {
      "Sid": "FISManage",
      "Action": ["fis:StopExperiment", "fis:GetExperiment"],
      "Resource": "arn:aws:fis:<REGION>:<ACCOUNT_ID>:experiment/*"
    },
    {
      "Sid": "PassFISRole",
      "Action": ["iam:PassRole"],
      "Resource": "arn:aws:iam::<ACCOUNT_ID>:role/fis-execution-role"
    }
  ]
}
```

> `FISStart` にタグ条件を追加。`Project: chaos-platform` タグが付いた FIS テンプレートだけ起動できる。

### K8s RBAC（IAM ではなく K8s 側の権限）

```yaml
rules:
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["get", "list", "delete"]
  - apiGroups: [""]
    resources: ["pods/ephemeralcontainers"]
    verbs: ["patch"]
  - apiGroups: [""]
    resources: ["pods/exec"]
    verbs: ["create"]
  - apiGroups: ["apps"]
    resources: ["deployments"]
    verbs: ["get", "patch"]
```

---

## 3. fis-execution-role

### 何に使うか

AWS FIS が `aws:eks:pod-network-latency` を実行するときに引き受けるロール。

### Trust Policy

```json
{
  "Principal": { "Service": "fis.amazonaws.com" },
  "Action": "sts:AssumeRole"
}
```

### Permission Policy

```json
{
  "Statement": [
    {
      "Sid": "EKSDescribe",
      "Action": ["eks:DescribeCluster"],
      "Resource": "arn:aws:eks:<REGION>:<ACCOUNT_ID>:cluster/chaos-cluster"
    },
    {
      "Sid": "EC2Describe",
      "Action": ["ec2:DescribeNetworkInterfaces"],
      "Resource": "*"
    },
    {
      "Sid": "EC2Modify",
      "Action": ["ec2:ModifyNetworkInterfaceAttribute"],
      "Resource": "arn:aws:ec2:<REGION>:<ACCOUNT_ID>:network-interface/*",
      "Condition": {
        "StringEquals": {
          "aws:ResourceTag/aws:eks:cluster-name": "chaos-cluster"
        }
      }
    },
    {
      "Sid": "CloudWatchLogs",
      "Action": ["logs:CreateLogDelivery", "logs:PutLogEvents"],
      "Resource": "arn:aws:logs:<REGION>:<ACCOUNT_ID>:log-group:/aws/fis/chaos-*"
    }
  ]
}
```

> `ec2:Describe*` は AWS の仕様でリソースレベル制限をサポートしていないため `*` のまま（変えられない）。`ModifyNetworkInterfaceAttribute` はタグ条件で chaos-cluster の ENI のみに絞っている。

---

## 4. lambda-api-handler-role

### 何に使うか

API Gateway から呼ばれ、実験の開始・停止・一覧・取得を行う。実験開始時は chaos-agent Lambda を非同期で呼び出す。

### Trust Policy

```json
{
  "Principal": { "Service": "lambda.amazonaws.com" },
  "Action": "sts:AssumeRole"
}
```

### Permission Policy

```json
{
  "Statement": [
    {
      "Sid": "DynamoDB",
      "Action": [
        "dynamodb:PutItem",
        "dynamodb:GetItem",
        "dynamodb:UpdateItem",
        "dynamodb:Scan"
      ],
      "Resource": [
        "arn:aws:dynamodb:<REGION>:<ACCOUNT_ID>:table/chaos-experiments",
        "arn:aws:dynamodb:<REGION>:<ACCOUNT_ID>:table/chaos-slo"
      ]
    },
    {
      "Sid": "InvokeChaosAgent",
      "Action": ["lambda:InvokeFunction"],
      "Resource": "arn:aws:lambda:<REGION>:<ACCOUNT_ID>:function:chaos-agent"
    },
    {
      "Sid": "CloudWatchLogs",
      "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
      "Resource": "arn:aws:logs:<REGION>:<ACCOUNT_ID>:log-group:/aws/lambda/chaos-api-handler*"
    }
  ]
}
```

---

## 5. lambda-sli-calculator-role

### 何に使うか

EventBridge で定期呼び出しされ、CloudWatch からエラーレート・バーン率を計算して DynamoDB に保存する。

### Trust Policy

```json
{
  "Principal": { "Service": "lambda.amazonaws.com" },
  "Action": "sts:AssumeRole"
}
```

### Permission Policy

```json
{
  "Statement": [
    {
      "Sid": "CloudWatchRead",
      "Action": ["cloudwatch:GetMetricStatistics", "cloudwatch:GetMetricData"],
      "Resource": "*"
    },
    {
      "Sid": "DynamoDB",
      "Action": ["dynamodb:PutItem", "dynamodb:GetItem"],
      "Resource": [
        "arn:aws:dynamodb:<REGION>:<ACCOUNT_ID>:table/chaos-sli",
        "arn:aws:dynamodb:<REGION>:<ACCOUNT_ID>:table/chaos-slo"
      ]
    },
    {
      "Sid": "CloudWatchLogs",
      "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
      "Resource": "arn:aws:logs:<REGION>:<ACCOUNT_ID>:log-group:/aws/lambda/chaos-sli-calculator*"
    }
  ]
}
```

> `cloudwatch:GetMetricStatistics` / `GetMetricData` は AWS の仕様でリソースレベル制限をサポートしていないため `*` のまま（変えられない）。

---

## 6. lambda-auto-stopper-role

### 何に使うか

SLI 違反を検知したら実験を停止し、SNS にアラートを送る。

### Trust Policy

```json
{
  "Principal": { "Service": "lambda.amazonaws.com" },
  "Action": "sts:AssumeRole"
}
```

### Permission Policy

```json
{
  "Statement": [
    {
      "Sid": "DynamoDB",
      "Action": [
        "dynamodb:GetItem",
        "dynamodb:UpdateItem",
        "dynamodb:Query",
        "dynamodb:Scan"
      ],
      "Resource": [
        "arn:aws:dynamodb:<REGION>:<ACCOUNT_ID>:table/chaos-experiments",
        "arn:aws:dynamodb:<REGION>:<ACCOUNT_ID>:table/chaos-sli",
        "arn:aws:dynamodb:<REGION>:<ACCOUNT_ID>:table/chaos-slo"
      ]
    },
    {
      "Sid": "SNSPublish",
      "Action": ["sns:Publish"],
      "Resource": "arn:aws:sns:<REGION>:<ACCOUNT_ID>:chaos-alerts"
    },
    {
      "Sid": "CloudWatchLogs",
      "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
      "Resource": "arn:aws:logs:<REGION>:<ACCOUNT_ID>:log-group:/aws/lambda/chaos-auto-stopper*"
    }
  ]
}
```

---

## 7. github-actions-role（OIDC）

### 何に使うか

GitHub Actions から AWS にデプロイするためのロール。静的キーを発行せず OIDC で一時認証する。

### Trust Policy

```json
{
  "Principal": {
    "Federated": "arn:aws:iam::<ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com"
  },
  "Action": "sts:AssumeRoleWithWebIdentity",
  "Condition": {
    "StringEquals": {
      "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
    },
    "StringLike": {
      "token.actions.githubusercontent.com:sub": "repo:<ORG>/<REPO>:*"
    }
  }
}
```

### Permission Policy

```json
{
  "Statement": [
    {
      "Sid": "ECRAuth",
      "Action": ["ecr:GetAuthorizationToken"],
      "Resource": "*"
    },
    {
      "Sid": "ECRPush",
      "Action": [
        "ecr:BatchCheckLayerAvailability",
        "ecr:PutImage",
        "ecr:InitiateLayerUpload",
        "ecr:UploadLayerPart",
        "ecr:CompleteLayerUpload"
      ],
      "Resource": "arn:aws:ecr:<REGION>:<ACCOUNT_ID>:repository/chaos-*"
    },
    {
      "Sid": "EKSDeploy",
      "Action": ["eks:DescribeCluster"],
      "Resource": "arn:aws:eks:<REGION>:<ACCOUNT_ID>:cluster/chaos-cluster"
    },
    {
      "Sid": "LambdaDeploy",
      "Action": ["lambda:UpdateFunctionCode", "lambda:UpdateFunctionConfiguration"],
      "Resource": "arn:aws:lambda:<REGION>:<ACCOUNT_ID>:function:chaos-*"
    }
  ]
}
```

---

## `"Resource": "*"` が残っている箇所と理由

AWS の仕様でリソースレベル制限をサポートしていないアクションは `*` のまま。これは回避不可能。

| アクション | 該当ロール | 理由 |
|-----------|-----------|------|
| `ecr:GetAuthorizationToken` | fargate-execution, github-actions | ECR 認証トークン取得はリソース指定不可 |
| `ec2:DescribeNetworkInterfaces` | fis-execution | Fargate Pod の ENI 特定に使用。EC2 Describe 系はリソース指定不可 |
| `cloudwatch:GetMetricStatistics` / `GetMetricData` | sli-calculator | CloudWatch メトリクス取得はリソース指定不可 |

---

## DynamoDB テーブル一覧

| テーブル名 | 用途 | 読み書きするロール |
|-----------|------|-----------------|
| `chaos-experiments` | 実験レコード | chaos-agent, api-handler, auto-stopper |
| `chaos-sli` | エラーレート・バーン率の時系列 | sli-calculator（書）, auto-stopper（読） |
| `chaos-slo` | サービスごとの SLO 閾値 | sli-calculator（読）, api-handler（読）, auto-stopper（読） |
