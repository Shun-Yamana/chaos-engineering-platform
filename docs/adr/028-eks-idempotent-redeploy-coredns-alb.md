# ADR 028 - EKS destroy/apply 再現性問題の解消（coredns DEGRADED + ALB 自動検出）

- Status: Accepted
- Date: 2026-05-14

## Context

`terraform destroy && terraform apply` を繰り返すと毎回 2 つの問題が手動対応を要していた。

1. **coredns DEGRADED**: Fargate クラスターでは coredns の Deployment に `eks.amazonaws.com/compute-type: ec2` アノテーションが残ると Pod が Pending のまま DEGRADED になる。`null_resource.patch_coredns_fargate` でパッチを当てているが、クラスター作成直後は coredns Deployment がまだ存在しないためパッチが空振りし、アノテーションが残ったまま addon が適用されていた。また `triggers = { cluster_name = ... }` はクラスター名が不変のため destroy/apply 後に再トリガーされなかった。

2. **ALB ホスト名変更**: AWS Load Balancer Controller が生成する ALB 名には EKS クラスターの UID 由来のハッシュが含まれる。クラスターを再作成するたびにホスト名が変わるため、`terraform.tfvars` の `alb_dns_name`・`alb_arn_suffix` を毎回手動更新し、`k8s/chaos-agent.yaml` の `SERVICE_B_URL` も手動で直していた。

## Decision

1. coredns パッチを destroy/apply で必ず再実行させ、Deployment 出現を待ってからパッチする。
2. ALB の DNS 名と ARN suffix を `data "aws_lb"` タグ検索で自動取得し、変数への手動入力を廃止する。

## Rationale

### coredns: cluster_name トリガーをやめた理由

クラスター名 `chaos-platform-cluster` は不変なのでトリガーが変化せず null_resource が再実行されない。`cluster_endpoint` はクラスター固有のランダムハッシュを含むため、destroy/apply のたびに必ず値が変わる。

### coredns: until ループを追加した理由

EKS クラスター作成直後は API サーバーが応答し始めるタイミングに遅延がある。`kubectl patch` を即時実行すると "deployment not found" で `|| true` により素通りし、アノテーションが残ったままになる。`until kubectl get deployment coredns ...` でポーリングすることで Deployment が確実に存在してからパッチを当てる。

### ALB: 変数手動入力をやめた理由

ALB 名はクラスター UID 由来のハッシュを含み、destroy/apply のたびに変わる。手動更新は忘れると CloudWatch Alarm・CloudFront が壊れた状態になり、デバッグコストが高い。

### ALB: `data "aws_lb"` タグ検索を選んだ理由

- `ingress.yaml.tpl` に `alb.ingress.kubernetes.io/tags: "Project=chaos-platform,Component=service-b-alb"` を追加すれば LBC が ALB に固定タグを付与する
- Terraform の `data "aws_lb" { tags = {...} }` + `depends_on = [null_resource.apply_ingress]` でタグ検索すれば ALB 名・ホスト名・ARN suffix を自動取得できる
- `depends_on` により data source の読み取りが apply フェーズに延期されるため、ALB が存在しない plan 時でもエラーにならない

### chaos-agent: SERVICE_B_URL の扱い

`null_resource.apply_ingress` が ALB プロビジョニング完了後に `kubectl set env` で Deployment の `SERVICE_B_URL` を更新する。加えて `chaos/agent.py` に起動時フォールバックを追加し、`SERVICE_B_URL` が未設定の場合は Kubernetes Ingress の `.status.loadBalancer.ingress[0].hostname` から自動検出する。

## Consequences

- `terraform plan` 時、`data.aws_lb.service_b` の値は "known after apply" となる。CloudFront や CloudWatch Alarm の plan 出力が一部 unknown を示すが、apply は正常完了する。
- `terraform.tfvars` から `alb_dns_name`・`alb_arn_suffix` が不要になり、destroy/apply 後の手動作業が不要になる。
- `null_resource.apply_ingress` が ALB 待機ループを含むため、apply の所要時間が ALB プロビジョニング（通常 2〜5 分）分だけ増加する。
- coredns の until ループは最長 5 分待機（15s × 20 回）する設計。EKS クラスターの API 応答が極端に遅延した場合は手動介入が必要。
