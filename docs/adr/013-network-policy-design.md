# ADR 013 - NetworkPolicy 設計

- Status: Accepted
- Date: 2026-05-03

## Context

ADR 011 item 11 で「NetworkPolicy を追加する」と決定したが、具体的な設計は本 ADR で定める。

NetworkPolicy はホワイトリスト形式の Pod ファイアウォールであり、「許可を書かない限り通信は通らない（Default Deny を適用した場合）」という前提に立つ。対象は `default` Namespace（service-a, service-b）と `chaos` Namespace（chaos-agent）。

EKS Fargate では VPC CNI の NetworkPolicy 強制モードを明示的に有効化しなければ、NetworkPolicy リソースを apply しても通信制御が一切機能しない（⑤参照）。

## Decision

**5つのポリシーを定める。**

| # | 対象 | 内容 |
|---|------|------|
| ① | default・chaos Namespace 全 Pod | Default Deny All（Ingress/Egress 全拒否） |
| ② | service-a | Ingress: service-b のみ / Egress: CoreDNS のみ |
| ③ | service-b | Ingress: ALB public サブネット / Egress: service-a + CoreDNS |
| ④ | chaos-agent | Ingress: なし / Egress: K8s API + AWS API + CoreDNS |
| ⑤ | vpc-cni addon | `enableNetworkPolicy: true` で NetworkPolicy 強制を有効化 |

## Rationale

### ① Default Deny All を入れた理由

NetworkPolicy が設定されていない Pod はデフォルトで全通信が許可される。Default Deny を先に入れることで「許可されていない Pod は全遮断」という安全側のベースラインを確立する。将来 Namespace に追加される Pod が意図せず全通になるリスクを防ぐ。

### ② service-a の制御

service-a は service-b から呼ばれる上流 API であり、外部への通信を必要としない。chaos-agent は K8s API Server 経由で ephemeral container のパッチや pod exec を行うため、service-a への直接ネットワーク通信は発生しない。Ingress は `app: service-b` のみに絞れる。

### ③ service-b の Ingress: ipBlock を ALB public サブネットに絞った理由

ALB は Pod ではないため `podSelector` で指定できない。`ipBlock` で書く必要があるが、範囲の選択肢は3つあった。

| 案 | CIDR | 問題点 |
|----|------|--------|
| A | 10.0.0.0/16（VPC 全体） | VPC 内の任意のリソースから直接到達できる |
| **B** | **10.0.0.0/24, 10.0.1.0/24（ALB サブネット）** | ALB が使う public サブネットのみ |
| C | 0.0.0.0/0 | 制限なし |

案 B は ALB が配置される public サブネット（ADR 011 item 3 で定義）に絞ることで最小権限を実現する。

### ④ chaos-agent の Egress を2 CIDR に分けた理由

AWS API（DynamoDB・FIS・STS）への通信は現状 NAT Gateway 経由でインターネットに出る（VPC エンドポイント未設定）。`0.0.0.0/0:443` のみで書くことも可能だが、K8s API Server（VPC 内 private IP: 10.0.0.0/16）と外部 AWS API を分けて書くことで通信先の意図が明確になる。

```
10.0.0.0/16:443  → K8s API Server（クラスター内）
0.0.0.0/0:443    → DynamoDB・FIS・STS（AWS API、NAT 経由）
kube-system:53   → CoreDNS（DNS 解決）
```

chaos-agent は service-a/b Pod に直接通信しない（K8s API 経由）ため、cross-namespace の Pod 間許可は不要。

### ⑤ VPC CNI の NetworkPolicy モード有効化

EKS vpc-cni アドオンはデフォルトでは NetworkPolicy の強制を行わない。`enableNetworkPolicy: "true"` を設定することで初めて機能する。この設定なしに NetworkPolicy を apply しても無効であり、セキュリティ上の誤解を招く。

```hcl
resource "aws_eks_addon" "vpc_cni" {
  cluster_name = module.eks.cluster_name
  addon_name   = "vpc-cni"
  configuration_values = jsonencode({
    enableNetworkPolicy = "true"
  })
}
```

## Consequences

- Default Deny により、DNS Egress（port 53）を各ポリシーに明示的に含めなければ Pod の名前解決が止まる
- ALB の public サブネット CIDR（10.0.0.0/24, 10.0.1.0/24）を変更した場合は service-b の NetworkPolicy も更新が必要
- VPC エンドポイント（ADR 011 item 12）を追加した場合、chaos-agent の `0.0.0.0/0:443` は VPC CIDR 内に閉じられるため、その時点でポリシーを更新する
- `enableNetworkPolicy: true` を vpc-cni addon に追加する Terraform 変更が必要（`terraform/eks.tf`）
