  # ADR 029 - EKS+VPC の安全な destroy 手順（LBC 管理 ALB の依存関係解消）

  - Status: Accepted
  - Date: 2026-05-14

  ## Context

  コスト節約のため EKS+VPC を一時的に destroy し、開発時に再 apply するサイクルを繰り返している。

  `terraform destroy -target=module.vpc` を直接実行すると以下のエラーでブロックされる。

  ```
  Error: deleting VPC (vpc-xxx): DependencyViolation:
    The vpc 'vpc-xxx' has dependencies and cannot be deleted.
  ```

  原因は ALB と ENI（Elastic Network Interface）が VPC のサブネット・セキュリティグループを掴んでいるため。これらは Terraform 管理外のリソースで、AWS Load Balancer Controller（LBC）が Kubernetes Ingress から自動生成する。`terraform destroy` はこれらの存在を知らないため削除できず、VPC の依存関係違反になる。

  加えて destroy 後の再 apply でも 2 つの問題が毎回発生していた（ADR 028 で解消済み）:
  1. coredns DEGRADED: null_resource のトリガーが再実行されず Fargate パッチが当たらない
  2. ALB ホスト名変更: `terraform.tfvars` の手動更新が必要だった

  ## Decision

  EKS+VPC を destroy する際は、Terraform を実行する前に kubectl で LBC 管理リソースを手動ドレインし、その後 Terraform ターゲット指定で順序通りに destroy する。

  ## Rationale

  ### `terraform destroy -target=module.eks` だけ先に実行する方法を外した理由

  EKS を先に destroy しても ALB は LBC によって管理されているため、EKS が消えても ALB 本体は AWS 上に残る場合がある。ENI がサブネットを掴んだままになり VPC destroy が引き続きブロックされる。

  ### Ingress 削除 → スケールダウン → 順序付き destroy を選んだ理由

  - `kubectl delete ingress service-b` で LBC が ALB を削除する（通常数秒〜数十秒）
  - `kubectl scale deployment --all --replicas=0` で Fargate Pod が終了し ENI が解放される
  - ALB・ENI がなくなってから EKS → VPC の順で destroy すれば依存関係違反は発生しない

  ## Consequences

  **destroy 手順（毎回この順序で実行すること）**

  ```bash
  # 1. クラスター認証
  aws eks update-kubeconfig --name chaos-platform-cluster --region ap-northeast-1

  # 2. LBC 管理の ALB を削除（ENI 解放まで待機）
  kubectl delete ingress service-b -n default

  # 3. Fargate Pod を停止して ENI を解放
  kubectl scale deployment --all --replicas=0 -n default
  kubectl scale deployment --all --replicas=0 -n chaos

  # 4. Terraform 側の null_resource ステートをクリア
  terraform destroy -target=null_resource.apply_ingress \
                    -target=null_resource.patch_coredns_fargate -auto-approve

  # 5. EKS アドオンを先に削除
  terraform destroy -target=aws_eks_addon.cloudwatch_observability \
                    -target=aws_eks_addon.coredns \
                    -target=aws_eks_addon.vpc_cni -auto-approve

  # 6. EKS クラスター本体を削除（10〜15 分）
  terraform destroy -target=module.eks -auto-approve

  # 7. VPC + NAT Gateway を削除（課金停止）
  terraform destroy -target=module.vpc -auto-approve
  ```

  - 手順 2〜3 を省略すると手順 7 が DependencyViolation でブロックされる
  - 再 apply 時の coredns DEGRADED・ALB ホスト名変更は ADR 028 の対応で自動解消される（手動作業不要）
  - NAT Gateway x2 と EKS コントロールプレーンが主要課金源。CloudFront・DynamoDB・Lambda・Cognito は低コストのため残置して問題ない
