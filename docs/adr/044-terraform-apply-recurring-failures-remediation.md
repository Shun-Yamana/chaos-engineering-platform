# ADR 044 - terraform apply 再発障害の根本対策（CoreDNS / apply_ingress / frontend_url）

- Status: Accepted
- Date: 2026-05-16

## Context

VPC 再作成を伴う `terraform apply` で以下の 3 つの問題が ADR 028・032 の対策後も再発した。

### ① CoreDNS addon DEGRADED — ADR 032 の対策が効かなかった理由

ADR 032 で追加した `null_resource.restart_coredns_after_addon` は
`depends_on = [aws_eks_addon.coredns]` になっている。
`aws_eks_addon.coredns` は Pod が ACTIVE になるまで最大 30 分待つが、
Fargate scheduler が起動完了する前に Pod が作られると toleration が注入されず Pending のまま。
Pod が Pending → addon が 30 分タイムアウト → `restart_coredns_after_addon` **が永遠に実行されない**。
restart が addon 完了後に依存していることが根本の設計ミス。

### ② `apply_ingress` until ループが Windows cmd で動かない

`provisioner "local-exec"` はデフォルトで Windows 上では `cmd /C` を使う。
bash の `until … do … done` 構文は cmd では解釈されず即座に終了する。
ALB の払い出しを待たずに `null_resource.apply_ingress` が 6 秒で完了し、
直後に `data.aws_lb.service_b` を検索しても ALB が存在せず 0 件エラーになる。
ADR 028 で設計した「ALB 払い出し待ちループ」が Windows 環境では機能していなかった。

### ③ CloudFront 再作成後に frontend_url / Cognito が旧ドメインのまま

`terraform.tfvars` の `frontend_url` は手動設定の変数で、
CloudFront を再作成するたびに新しいドメインに手動更新が必要だった。
更新を忘れると API Gateway CORS が旧ドメインのみを許可し、
ブラウザから "No Access-Control-Allow-Origin" エラーが発生する。
Cognito callback_urls も同様に旧ドメインのまま残り、
Cognito hosted UI が "An error was encountered with the requested page." を返す。

## Decision

### ① CoreDNS: rollout restart を addon 作成より前にループ実行

`null_resource.patch_coredns_fargate`（addon より先に実行）を PowerShell に変更し、
coredns pod が Running になるまで最大 15 分間 30 秒ごとに rollout restart を繰り返す。
Fargate scheduler の起動完了タイミングに関わらず、
Running になったことを確認してから addon 作成に進む。

### ② apply_ingress: PowerShell の while ループに変換

`null_resource.apply_ingress` の `local-exec` に `interpreter = ["PowerShell", "-Command"]` を設定し、
bash の `until` を PowerShell の `while` + `Start-Sleep` に書き換える。
同様に `null_resource.restart_coredns_after_addon` も PowerShell に統一する。

### ③ frontend_url: CloudFront computed 値を直接参照し変数を廃止

`aws_apigatewayv2_api.chaos` の `cors_configuration.allow_origins` と
`aws_cognito_user_pool_client.this` の `callback_urls` / `logout_urls` を
`var.frontend_url` から `aws_cloudfront_distribution.frontend.domain_name` の computed 値に変更する。
Terraform が CloudFront 作成後に自動で正しいドメインを参照するため、
`terraform.tfvars` の手動更新が不要になる。

## Rationale

### CoreDNS: 依存方向を逆にした理由

restart を addon 完了後の別 null_resource に委ねていた設計では、
addon が DEGRADED でタイムアウトすると restart の機会が失われる（鶏と卵）。
addon 作成の**前**に pod を Running にしてしまえば、
addon は最初から ACTIVE な pod を見つけて即 ACTIVE になる。

### apply_ingress: PowerShell を選んだ理由

Windows 環境では bash の `until`/`sleep` は cmd で解釈されない。
`interpreter = ["PowerShell", "-Command"]` を指定すれば
WSL や Git Bash なしで Windows ネイティブに PowerShell 構文を実行できる。
CI/CD（GitHub Actions / Linux）では `local-exec` が bash で動くため変更不要だが、
本プロジェクトは Windows ローカルでの `terraform apply` が主なので PowerShell を選択する。

### frontend_url: 変数廃止の理由

CloudFront ドメインは apply のたびに変わる可能性がある（destroy → 再作成）。
手動変数は「更新を忘れる」という人的ミスを避けられない。
`aws_cloudfront_distribution.frontend.domain_name` は Terraform が自動計算する computed 値であり、
常に現在の CloudFront ドメインを指す。CORS と Cognito を computed 値に紐付ければ
再作成後の手動作業がゼロになる。

## Consequences

- 次回 `terraform apply` で `null_resource.patch_coredns_fargate` が再実行され、
  CoreDNS pod が Running になってから addon 作成に進む（所要時間 5〜15 分）。
- `apply_ingress` が PowerShell の while ループで最大 10 分間 ALB 払い出しを待つ。
  ALB が存在しなければ "WARNING: ALB not provisioned within timeout" を出して続行する（apply 失敗にならない）。
- CloudFront ドメインが変わっても API Gateway CORS と Cognito callback_urls が
  `terraform apply` だけで自動更新される。`terraform.tfvars` の `frontend_url` は参照されなくなる。
- `aws_cloudfront_distribution.frontend` と `aws_apigatewayv2_api.chaos` の間に依存関係が生まれるため、
  CloudFront が先に作成されてから API Gateway が更新される順序になる。
