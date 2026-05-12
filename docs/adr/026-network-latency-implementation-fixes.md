# ADR 026 - ネットワーク遅延実験の実装変遷

- Status: Accepted
- Date: 2026-05-13

## Context

FIS の `aws:eks:pod-network-latency` アクションを使ってネットワーク遅延実験を実装しようとしたが、
EKS Fargate の制約と複数の IAM・インフラ設定不足が連鎖して動かなかった。
最終的にアプリケーションレベルの遅延注入に切り替えた。

---

## 修正一覧

### 1. FIS stop condition — CloudWatch アラームが INSUFFICIENT_DATA で実験開始できない

**何が起きたか**
FIS 実験を開始しようとすると即 failed になる。
理由：`chaos-platform-service-b-5xx-rate` アラームが `INSUFFICIENT_DATA` 状態のため。

**なぜ起きたか**
FIS は実験開始前に stop condition のアラームが `OK` であることを確認する。
ALB にトラフィックがない状態ではメトリクスが出ず、アラームが評価できない。
鶏と卵の問題：「実験が始まってからトラフィックが発生する」ため、実験前に OK にならない。

**どう直したか**
chaos-agent に `TrafficGenerator` クラスを追加。
`SERVICE_B_URL` 環境変数に指定した URL に 30 秒間隔で定期リクエストを送り、
ALB メトリクスを常に維持するようにした。

また `treat_missing_data = "notBreaching"` に変更し、
データなしを「正常」とみなすようにした（元は `"breaching"`）。

---

### 2. FIS EKS access entry 未設定 — ターゲット解決で 403

**何が起きたか**
`Error resolving targets. Not authorized to perform the required action.`
FIS が Pod 一覧を取得できず実験が即 failed になった。

**なぜ起きたか**
FIS 実行ロール (`fis-execution-role`) が EKS access entries に登録されておらず、
Kubernetes API を呼び出す権限がなかった。

**どう直したか**
`aws_eks_access_entry.fis` と `aws_eks_access_policy_association.fis` を追加。
最初は `AmazonEKSViewPolicy` を付与したが、インジェクションに write が必要だったため
`AmazonEKSEditPolicy` に変更した。

---

### 3. chaos-agent — DynamoDB Scan 権限が不足

**何が起きたか**
chaos-agent が `AccessDeniedException: dynamodb:Scan` で起動直後にクラッシュした。

**なぜ起きたか**
`chaos-agent-policy` に `dynamodb:Scan` が含まれていなかった。
Poller が pending 実験を取得するために Scan を使っているが、設計時に見落とした。

**どう直したか**
`terraform/iam.tf` の chaos-agent policy に `dynamodb:Scan` を追加した。

---

### 4. chaos-agent — FIS CreateExperimentTemplate で action リソースへの権限不足

**何が起きたか**
`AccessDeniedException: fis:CreateExperimentTemplate on resource: arn:aws:fis:...:action/aws:eks:pod-network-latency`

**なぜ起きたか**
既存の `FISTemplateCreate` ポリシーのリソースが `experiment-template/*` のみだった。
FIS テンプレート作成時は参照するアクション ARN へのアクセスも IAM が評価する。

**どう直したか**
`Resource` に `arn:aws:fis:...:action/*` を追加した。

---

### 5. ALB traffic-gen — X-Origin-Verify ヘッダーなしで 404

**何が起きたか**
chaos-agent からの `/health` リクエストが ALB に届かず、
CloudWatch の `RequestCount` が 0 のままだった。

**なぜ起きたか**
ALB の Ingress に `alb.ingress.kubernetes.io/conditions.service-b` で
`X-Origin-Verify` ヘッダーの一致を必須にしていた（CloudFront 以外をブロックする設計）。
TrafficGenerator がこのヘッダーを付けずにリクエストしていた。

**どう直したか**
`TrafficGenerator` に `origin_secret` パラメータを追加し、
`ALB_ORIGIN_SECRET` 環境変数から値を受け取って `X-Origin-Verify` ヘッダーに付与した。

---

### 6. aws:eks:pod-network-latency — EKS Fargate 非対応

**何が起きたか**
実験が「FIS Pod failed to initiate」で失敗した。
FIS のインジェクター Pod が起動せず 3 分後にタイムアウト。

**なぜ起きたか**
`aws:eks:pod-network-latency` は tc netem を使って Pod のネットワーク名前空間に
遅延を注入する。EC2 ノードでは動作するが、**EKS Fargate では非対応**。
ADR 009 で「FIS に委譲すれば Fargate でも動く」と想定していたが誤りだった。

**どう直したか**
`network_latency` の実装を FIS 委譲からアプリケーションレベル注入に変更した。

| 方式 | 内容 |
|---|---|
| 旧: FIS 委譲 | `aws:eks:pod-network-latency` で tc netem → Fargate 非対応 |
| 新: env var 注入 | Deployment に `LATENCY_MS` を patch → service-b が asyncio.sleep |

service-b に `LATENCY_MS` 環境変数を読んで `/items/{id}` レスポンスを遅延させる実装を追加。
chaos-agent の `network_latency_inject/remove` を `http_error_inject` と同様の Deployment patch 方式に変更した。

---

## まとめ

| # | 原因分類 | 教訓 |
|---|---|---|
| 1 | 鶏卵問題 | FIS stop condition には事前トラフィックが必要。定期ヘルスチェックで解決 |
| 2 | IAM 権限 | FIS ロールは EKS access entry への登録も必要 |
| 3 | IAM 権限 | DynamoDB Scan は Poller に必須。設計時に漏れやすい |
| 4 | IAM 権限 | FIS テンプレート作成は action ARN へのアクセスも評価される |
| 5 | ALB 設計 | X-Origin-Verify 必須の ALB には内部クライアントもヘッダーが必要 |
| 6 | Fargate 制約 | aws:eks:pod-network-latency は Fargate 非対応。アプリレベル注入で代替 |
