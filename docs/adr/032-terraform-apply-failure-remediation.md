# ADR 032 - terraform apply 再現トラブル3件の恒久対策

- Status: Accepted
- Date: 2026-05-15

## Context

VPC 再作成を伴う `terraform apply` で以下の3つのトラブルが発生した。
いずれも手動介入で解消したが、次回デプロイで同じ失敗をしないよう恒久対策を施す。

1. **CoreDNS addon DEGRADED（30分タイムアウト）**: addon が DEGRADED のまま Terraform がタイムアウト
2. **`data.aws_lb.service_b` 0件**: ALB が存在せず plan/apply が失敗
3. **CloudFront `IllegalUpdate`**: `custom_error_response` で `response_code` だけ設定し `response_page_path` が欠如

## Decision

1. `aws_eks_addon.coredns` 作成後に patch + rollout restart する `null_resource.restart_coredns_after_addon` を追加する
2. `null_resource.apply_ingress` のトリガーに `cluster_endpoint` を追加し、クラスター再作成時に必ず再実行する
3. `cloudfront.tf` の `custom_error_response` に `response_page_path = "/"` を追加する

## Rationale

### CoreDNS: patch を addon の前後両方で実行する理由

既存の `null_resource.patch_coredns_fargate` は addon 作成より前に実行される。
しかし `aws_eks_addon.coredns` が CoreDNS Deployment を作成・上書きするため、
addon 適用後の Deployment には `eks.amazonaws.com/compute-type: ec2` アノテーションが
再び付与された状態になる。addon 後に `rollout restart` することで Fargate scheduler が
新しい Pod を正しいノードへ割り当て、DEGRADED を回避できる。

### ALB: cluster_endpoint をトリガーに追加する理由

`null_resource.apply_ingress` のトリガーが `origin_secret` と `ingress_hash` だけでは、
VPC/クラスターを再作成しても Ingress YAML の内容が変わらない限り再実行されない。
クラスター再作成後は Kubernetes 上の Ingress リソースが消えるため ALB も消える。
`cluster_endpoint` はクラスター固有ハッシュを含み、再作成で必ず変化するため
これをトリガーに加えることで Ingress/ALB を自動再作成できる。
また `depends_on` を `restart_coredns_after_addon` に変更し、
CoreDNS と LBC が Ready になってから Ingress を apply する順序を保証する。

### CloudFront: response_page_path を追加する理由

CloudFront API の仕様で `ResponseCode` と `ResponsePagePath` は両方設定するか
両方空にする必要がある。`response_code = 503` のみでは `IllegalUpdate` になる。
`response_page_path = "/"` を追加することで API 要件を満たす。

## Consequences

- 次回 `terraform apply` で `null_resource.restart_coredns_after_addon` が新規作成されトリガーされる（CoreDNS restart が1回走る）
- `null_resource.apply_ingress` のトリガーに `cluster_endpoint` が加わり、今回の apply で1回再実行される（Ingress が再 apply される。ALB は既存のため変化なし）
- CloudFront distribution が1回更新される（`response_page_path` の追加）
