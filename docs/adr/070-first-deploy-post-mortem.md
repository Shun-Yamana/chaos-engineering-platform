# ADR 070 - 初回デプロイ ポストモーテム：10 の失敗と再発防止策

- Status: Accepted
- Date: 2026-05-24

## Context

terraform destroy 後の完全再構築 → GitHub Actions デプロイという初回フローで
10 件の障害が連続して発生した。それぞれの根本原因と修正内容・再発防止策を記録する。

---

## Decision

10 件すべてを個別に根本原因まで掘り下げ、コードレベルで修正する。
「動いたから OK」ではなく、なぜ発生したかを ADR に残すことで次回再構築コストを下げる。

---

## 障害一覧と Rationale

### 障害 1: ECR IMMUTABLE + deploy.yml が :latest を push

**症状**: `ImagePushFailed – tag already exists`  
**原因**: ECR を MUTABLE→IMMUTABLE に変更したが、deploy.yml がまだ `:latest` タグを push していた。  
**修正**: deploy.yml から `:latest` push を削除し、k8s マニフェストの image を `SERVICE_A_IMAGE` / `SERVICE_B_IMAGE` / `CHAOS_AGENT_IMAGE` プレースホルダーに変更。  
**再発防止**: ECR IMMUTABLE を有効にした時点で deploy.yml と k8s manifest の両方をセットで確認する。SHA タグのみ使うルールを README に明記。

---

### 障害 2: GitHub Actions OIDC プロバイダーが存在しない

**症状**: `Could not assume role with OIDC: No OpenIDConnect provider found`  
**原因**: terraform destroy 後に terraform apply を一度も実行していなかった。OIDC プロバイダー（`aws_iam_openid_connect_provider.github_actions`）が AWS に存在しない状態で push した。  
**修正**: ローカルで `terraform apply` を先に実行してから push。  
**再発防止**: destroy → rebuild の手順書に「terraform apply が OIDC を作るまで GitHub Actions は使えない」を追記。最初の `terraform apply` だけはローカル実行が必須。

---

### 障害 3: FIS EKS アクションのパラメータ名変更（API breaking change）

**症状**: `ValidationException: Unexpected parameter "cpuPercentage"` / `Missing value for required parameter "kubernetesServiceAccount"`  
**原因**: AWS FIS EKS アクションの API が変更された。
- `aws:eks:pod-cpu-stress`: `cpuPercentage` → `percent`
- `aws:eks:pod-memory-stress`: `memoryPercentage` → `percent`
- `aws:eks:pod-delete`: `kubernetesServiceAccount` が必須化

**修正**: `terraform/fis.tf` のパラメータ名を修正、pod_kill に `kubernetesServiceAccount: default` を追加。  
**再発防止**: FIS テンプレートを作成・変更する前に `aws fis get-action --id <action-id>` で現在のパラメータ定義を確認する。

---

### 障害 4: CloudWatch ロググループが既に存在する

**症状**: `ResourceAlreadyExistsException: The specified log group already exists`  
**原因**: EKS クラスター作成時に `/aws/eks/chaos-platform-cluster/cluster` ロググループが自動生成される。Terraform も同じ名前で `aws_cloudwatch_log_group` リソースを定義していたため競合。  
**修正**: `terraform import aws_cloudwatch_log_group.eks_control_plane /aws/eks/chaos-platform-cluster/cluster` で既存リソースを取り込み。  
**再発防止**: EKS が自動作成するロググループは `terraform import` で管理下に入れるか、Terraform 定義を削除して EKS に任せる。再構築時は plan の `ResourceAlreadyExists` 候補を事前に import してから apply する。

---

### 障害 5: ingress.yaml が gitignored で CI から参照不可

**症状**: `sed: can't read k8s/ingress.yaml: No such file or directory`  
**原因**: `ingress.yaml` には origin secret（機密値）が埋め込まれるため `.gitignore` に追加されていた。GitHub Actions は checkout したリポジトリから参照するため存在しない。  
**修正**: deploy.yml を `ingress.yaml` → `ingress.yaml.tpl` 参照に変更し、Terraform 変数構文 (`${alb_logs_bucket}`, `${origin_secret}`) を sed で置換するよう修正。  
**再発防止**: CI でデプロイするファイルは gitignored にしない。機密値はプレースホルダー or Secrets 経由で注入する設計にする。

---

### 障害 6: FIS テンプレート ID の 6/8 が chaos-agent に未渡し

**症状**: chaos-agent が pod_kill / cpu_stress / memory_stress 実験を開始できない  
**原因**: deploy.yml は `FIS_TEMPLATE_SERVICE_A/B`（network_latency 2 件）しか注入していなかった。chaos-agent が必要とする残り 6 件（pod_kill × 2、cpu_stress × 2、memory_stress × 2）が空文字のままだった。  
**修正**: GitHub Secrets に 8 件すべてを登録し、deploy.yml と chaos-agent.yaml のプレースホルダーを拡張。  
**再発防止**: FIS テンプレートを追加したら chaos-agent.yaml の env 定義・deploy.yml の sed・GitHub Secrets の 3 点セットを同時に更新する。

---

### 障害 7: `aws_xray_sdk.ext.fastapi` が Python 3.13 コンテナで不在

**症状**: `ModuleNotFoundError: No module named 'aws_xray_sdk.ext.fastapi'`  
**原因**: `python:3.13-slim` ベースイメージで `aws-xray-sdk>=2.14.0` をインストールしたとき、`ext/fastapi.py` が含まれないバージョンが解決されていた（またはインストール失敗）。ローカル（Python 3.11）では問題なし。  
**修正**: `XRayMiddleware` の import・使用を削除。`patch_all()` で outbound HTTP/boto3 トレースは継続。  
**再発防止**: Python バージョンを上げる前に主要ライブラリのコンテナ内動作確認を行う。特に X-Ray SDK などの AWS 製 SDK は Python 新バージョン対応が遅れることがある。

---

### 障害 8: uvloop が Python 3.13 の task_factory 変更と非互換

**症状**: `TypeError: task_factory() got an unexpected keyword argument 'context'`  
**原因**: `uvicorn[standard]` が uvloop をインストールする。Python 3.13 で `asyncio` の `create_task` シグネチャが変更されたが uvloop が未対応。`AsyncContext` も同じ理由でクラッシュ。  
**修正**: `uvicorn[standard]` → `uvicorn` に変更（uvloop をインストールしない）。`AsyncContext` も削除。  
**再発防止**: Python バージョンを上げる際は uvloop の Python 3.13 対応状況を事前確認する。`[standard]` extras は本番では不要（パフォーマンス差は EKS Fargate のネットワーク遅延に埋もれる）。

---

### 障害 9: CloudWatch Observability Addon が uvloop を自動注入（ADOT）

**症状**: uvloop 削除後も同じ TypeError が発生し続ける  
**原因**: `amazon-cloudwatch-observability` addon が `cloudwatch.aws.amazon.com/auto-annotate-python: true` を pod に付与し、ADOT Python auto-instrumentation（`adot-autoinstrumentation-python:v0.17.0`）を init container として注入していた。このコンテナが uvloop を含む ADOT SDK を挿入するため、requirements.txt から uvloop を消しても意味がなかった。  
**修正**: service-a/b の pod template に `instrumentation.opentelemetry.io/inject-python: "false"` アノテーションを追加して opt-out。  
**再発防止**: CloudWatch addon を有効にした EKS クラスターでは、Python pod に ADOT が自動注入されることを前提に動作確認する。自前で X-Ray SDK を使う場合は必ず opt-out する。

---

### 障害 10: Envoy HTTPFault の `fixed_delay: 0s` が proto 検証エラー

**症状**: `Proto constraint validation failed: FaultDelayValidationError.FixedDelay: value must be greater than 0s`  
**原因**: http_error_inject 実験の「無効状態」として `fixed_delay: 0s` を設定していた。Envoy の proto 検証は `delay` ブロックが存在する場合 `fixed_delay > 0s` を必須とする。percentage = 0 でも値の検証は行われる。  
**修正**: `fixed_delay: 0s` → `fixed_delay: 1s` に変更（percentage = 0 のため実際の遅延は発生しない）。  
**再発防止**: Envoy の fault filter は「無効化」のために delay を残す場合、`fixed_delay` を有効な値（≥1ms）に設定し、percentage で 0% にして無効化する。`fixed_delay: 0s` は proto 違反。

---

## Consequences

**今回修正したファイル一覧**

| ファイル | 変更内容 |
|---------|---------|
| `.github/workflows/deploy.yml` | :latest 削除 / ingress.yaml.tpl 参照 / 8 FIS template 注入 |
| `k8s/service-a.yaml` | SERVICE_A_IMAGE placeholder / ADOT opt-out annotation |
| `k8s/service-b.yaml` | SERVICE_B_IMAGE placeholder / ADOT opt-out annotation |
| `k8s/chaos-agent.yaml` | CHAOS_AGENT_IMAGE placeholder / 8 FIS template placeholder |
| `k8s/envoy-config.yaml` | fixed_delay 0s → 1s |
| `services/service-a/main.py` | XRayMiddleware 削除 / AsyncContext 削除 |
| `services/service-a/requirements.txt` | uvicorn[standard] → uvicorn |
| `services/service-b/main.py` | XRayMiddleware 削除 / AsyncContext 削除 |
| `services/service-b/requirements.txt` | uvicorn[standard] → uvicorn |
| `terraform/fis.tf` | FIS パラメータ名修正 / kubernetesServiceAccount 追加 |

**次回 terraform destroy → rebuild 時のチェックリスト**

1. `terraform apply` をローカル実行（OIDC プロバイダー作成）
2. `terraform output` で FIS テンプレート ID を全件取得
3. GitHub Secrets を新しい ID で更新（8 件）
4. push → GitHub Actions が自動デプロイ

**失った X-Ray 機能**: リクエスト単位の incoming segment 作成（XRayMiddleware が担っていた）。
outbound HTTP と boto3 の downstream トレースは `patch_all()` で継続。
