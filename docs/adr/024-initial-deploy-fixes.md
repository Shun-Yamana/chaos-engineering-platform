# ADR 024 - 初回デプロイで発生した修正の記録

- Status: Accepted
- Date: 2026-05-12

## Context

Terraform・kubectl・Docker を使った初回フルデプロイを実施した。
設計時には想定できなかった AWS API の制約・Fargate の既知バグ・IAM 権限の抜け漏れが
複数箇所で顕在化した。本 ADR はそれらを「何が起きたか → なぜ起きたか → どう直したか」
の形式でまとめ、同種の問題を繰り返さないための記録とする。

---

## 修正一覧

### 1. CloudWatch ダッシュボード — ALB が未作成の段階でメトリクス dimension が空になる

**何が起きたか**
`terraform plan` 時点で `var.alb_arn_suffix = ""` のとき、ダッシュボードの widgets に
空文字の LoadBalancer dimension が含まれ、CloudWatch API が 400 を返した。

**なぜ起きたか**
ALB は k8s Ingress 作成後に払い出されるため、初回 apply 時には ARN suffix が不明。
ダッシュボード定義を静的に書いていたため、空文字がそのまま API に渡った。

**どう直したか**
`locals { dashboard_widgets = local.alb_alarms_enabled ? [...] : [] }` で条件分岐し、
`alb_arn_suffix` が未設定のときは widgets を空配列にした。
CloudWatch Alarms・FIS stop_condition も同様に `alb_alarms_enabled` フラグで制御。

---

### 2. SNS トピックポリシー — `sns:*` ワイルドカードが KMS 暗号化済みトピックで拒否される

**何が起きたか**
`aws_sns_topic_policy` の Principal=root に `Action = "sns:*"` を指定したところ、
KMS 暗号化トピックでは `sns:*` ワイルドカードを受け付けないエラーが発生。

**なぜ起きたか**
SNS の KMS 連携では、トピックポリシーに kms:GenerateDataKey / kms:Decrypt の
暗黙呼び出しが絡むため、ワイルドカードを安全でないとみなして拒否される。

**どう直したか**
`Action` を明示的なアクションリスト（GetTopicAttributes / SetTopicAttributes /
AddPermission / RemovePermission / DeleteTopic / Subscribe /
ListSubscriptionsByTopic / Publish / Receive）に書き換えた。

---

### 3. SNS 配信ポリシー — 総リトライ時間が 3600 秒上限を超過

**何が起きたか**
`numRetries=100, numMaxDelayRetries=70` で設定した配信ポリシーが
「合計待機時間が 3600 秒を超えている」として拒否された。

**なぜ起きたか**
指数バックオフの計算を手動で行わずに設計したため上限を把握していなかった。

**どう直したか**
`numRetries=10, numMinDelayRetries=2, numMaxDelayRetries=3` に削減。
合計時間 ≤ 1840 秒に収まることを計算で確認した。

---

### 4. EKS セキュリティグループ — description に非 ASCII 文字が含まれ拒否

**何が起きたか**
`description = "Cluster SG ①"` のように丸数字（①②）を含む文字列を指定したところ、
EC2 API が非 ASCII を拒否してエラーになった。

**なぜ起きたか**
Terraform の HCL は UTF-8 文字列を許容するが、EC2 SG description の受付文字集合は
ASCII のみ（`[a-zA-Z0-9 ._\-:/()#,@[\]+=&;{}!$*]`）に制限されている。

**どう直したか**
description から ①② を削除してピュア ASCII に変更した。

---

### 5. EKS Fargate + coredns — `eks.amazonaws.com/compute-type: ec2` アノテーション問題

**何が起きたか**
EKS クラスタ作成直後、coredns Deployment に `eks.amazonaws.com/compute-type: ec2`
アノテーションが自動付与されており、Fargate プロファイルのスケジューラが Pod を
Fargate に配置できず DEGRADED のまま止まった。

**なぜ起きたか**
EKS が管理する coredns には EC2 向けのアノテーションがデフォルトで付くが、
Fargate-only クラスタでは EC2 ノードが存在しないため Pod が Pending になる。
これは EKS Fargate の既知の制約。

**どう直したか**
`null_resource.patch_coredns_fargate` で `kubectl patch deployment coredns` を実行し、
問題のアノテーションを addon 作成より前に削除するようにした。
`aws_eks_addon.coredns` はこの null_resource に depends_on させることで順序を保証。

---

### 6. Fargate Pod 実行ロール — EKS マネージド ECR からの Image Pull が 403

**何が起きたか**
coredns / vpc-cni などの EKS アドオン Pod が `ImagePullBackOff` (403 Forbidden) になった。
自アカウントの ECR リポジトリへのアクセス権はあったが、アドオンイメージは
AWS マネージドアカウント (`602401143452`) の ECR から pull される。

**なぜ起きたか**
Fargate pod execution role の `ECRPullOwn` ポリシーを
`arn:aws:ecr:...:自アカウントID:repository/*` のみに限定していたため。

**どう直したか**
`ECRPullEKSManaged` ステートメントを追加し、
`arn:aws:ecr:...:602401143452:repository/*` への BatchGetImage 等を許可した。

---

### 7. amazon-cloudwatch-observability addon — Fargate Profile 未作成で Pending

**何が起きたか**
cloudwatch-observability addon の Pod が `amazon-cloudwatch` namespace に展開されたが、
該当 namespace の Fargate Profile が存在しないため Pending のまま動かなかった。

**なぜ起きたか**
設計時に `default`, `kube-system`, `chaos`, `aws-observability` の 4 プロファイルのみ
定義しており、`amazon-cloudwatch` namespace を見落としていた。

**どう直したか**
`aws_eks_fargate_profile.amazon_cloudwatch` を追加し、
`selector { namespace = "amazon-cloudwatch" }` を設定した。

---

### 8. aws_eks_addon.cloudwatch_observability — ResourceInUseException で import が必要

**何が起きたか**
Fargate Profile 追加後に addon を再作成しようとしたところ、
「同名の addon がすでに存在する」と ResourceInUseException が出た。

**なぜ起きたか**
前回の apply で addon 自体は作成済みだったが、Terraform state には入っていなかった。
Fargate Profile の不足が原因で DEGRADED だっただけで、addon リソース自体は存在していた。

**どう直したか**
`terraform import aws_eks_addon.cloudwatch_observability` で既存リソースを state に取り込み、
taint → recreate の流れで正常に更新した。

---

### 9. AWS FIS テンプレート — `aws:eks:pod-network-latency` に必須パラメータが不足

**何が起きたか**
FIS テンプレートの作成が以下のエラーで失敗した。
1. `Missing value for required parameter "kubernetesServiceAccount"`
2. `logSchemaVersion must be >= 2 for aws:eks:pod-network-latency`
3. `Target type 'aws:eks:pod' does not support 'filters'`

**なぜ起きたか**
- `kubernetesServiceAccount` は EKS Pod ネットワーク系アクションに必須だが AWS ドキュメント上で見落とした
- `log_schema_version = 1` で書いていたが EKS 系アクションは v2 以上が必須
- `aws:eks:pod` リソースタイプは `filter` ブロックを持たず、Pod 選択は `parameters` のみで行う

**どう直したか**
- `parameter { key = "kubernetesServiceAccount", value = "default" }` を追加
- `log_schema_version = 2` に変更
- `filter` ブロックを削除し、`parameters` に `namespace / selectorType / selectorValue` を移動

---

### 10. k8s マニフェスト — ECR URL・IAM ARN などのプレースホルダーが未置換

**何が起きたか**
`kubectl apply` 後に Pod が `InvalidImageName` で起動しなかった。
ingress も S3 バケット名プレースホルダーのまま ALB 作成に失敗した。

**なぜ起きたか**
マニフェストを CI/CD パイプライン（`kubectl set env` / envsubst）で値を注入する
設計にしていたが、初回は手動 apply を行ったためプレースホルダーが残った。

**どう直したか**
以下のプレースホルダーを実際の値に直接置換した：
- `SERVICE_A_IMAGE` / `SERVICE_B_IMAGE` / `CHAOS_AGENT_IMAGE` → ECR URL
- `CHAOS_AGENT_ROLE_ARN` → `arn:aws:iam::203553641035:role/chaos-agent-role`
- `PLACEHOLDER_FIS_TEMPLATE_SERVICE_A/B` → 払い出された FIS template ID
- `CHAOS_ALB_LOGS_BUCKET` → `chaos-platform-alb-logs-203553641035`
- `CLOUDFRONT_ORIGIN_SECRET` → Terraform output の値

---

### 11. chaos-agent — boto3 が `AWS_REGION` 環境変数を自動検出しない

**何が起きたか**
chaos-agent Pod が `NoRegionError: You must specify a region` で起動クラッシュした。

**なぜ起きたか**
boto3 が自動検出するリージョン環境変数は `AWS_DEFAULT_REGION` であり、
`AWS_REGION` は認識しない（ECS と異なり Fargate IRSA 環境では自動注入もされない）。

**どう直したか**
`boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", ...))` のように
`region_name` を明示するよう `agent.py` を修正した。`boto3.client("fis")` も同様に対処。

---

### 12. ECR — IMMUTABLE タグで iterative push が失敗

**何が起きたか**
`chaos-agent` イメージのリビルド後に `latest` タグで push したところ
「immutable tag cannot be overwritten」エラーが発生した。

**なぜ起きたか**
ECR リポジトリを `image_tag_mutability = "IMMUTABLE"` で作成していたが、
CI/CD 未整備の手動デプロイ段階では同タグでの上書きが必要だった。

**どう直したか**
`image_tag_mutability = "MUTABLE"` に変更した。
本番運用段階では `sha-<commit>` タグを使い IMMUTABLE に戻す方針とする。

---

### 13. AWS LBC — `DescribeListenerAttributes` 権限が IAM ポリシーに未定義

**何が起きたか**
Ingress を apply して ALB は作成されたが、LBC が
`AccessDenied: elasticloadbalancing:DescribeListenerAttributes` で設定を完了できなかった。

**なぜ起きたか**
LBC v2.x 以降が利用する `DescribeListenerAttributes` API が、
設計時に参照した IAM ポリシーサンプルに含まれていなかった（API が比較的新しいため）。

**どう直したか**
`alb.tf` の LBC ポリシーの Describe アクションリストに
`elasticloadbalancing:DescribeListenerAttributes` を追加した。

---

## まとめ

初回デプロイで発生した問題は大別すると 3 種類だった：

1. **AWS API の制約** — SNS ワイルドカード拒否、SNS 配信時間上限、SG description ASCII 制限、
   FIS パラメータ必須制約など。ドキュメントに記載があるが読み飛ばしやすい制約が多い。

2. **Fargate の既知バグ・設計上の抜け漏れ** — coredns アノテーション問題、
   ECR マネージドアカウント、amazon-cloudwatch namespace の Fargate Profile 未作成。
   Fargate-only 構成特有の罠であり、EC2 ノードベースの EKS では発生しない。

3. **2フェーズデプロイの実装漏れ** — ALB ARN suffix・プレースホルダー未置換など。
   CI/CD パイプラインが未整備の段階での手動デプロイで顕在化した。
   本番運用ではこれらを GitHub Actions で自動注入する。
