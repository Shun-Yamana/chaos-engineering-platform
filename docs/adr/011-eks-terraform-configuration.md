# ADR 011 - EKS Terraform 設定

- Status: Accepted
- Date: 2026-05-02

> このADRはEKSに関する設計判断を集約する。項目が増えるたびに追記する。

## 1. 基本プロバイダー設定

### Context

EKS クラスターおよび周辺リソースを Terraform で管理するにあたり、必要なプロバイダーを選定する必要があった。特に K8s アドオンの管理方法（Helm vs EKS マネージドアドオン）と、IRSA のための OIDC 設定に使うプロバイダーの選択が論点となった。

### Decision

**aws・kubernetes・tls・archive の4プロバイダーを使用する。helm プロバイダーは使用しない。**

### Rationale

#### helm プロバイダーを外した理由

K8s アドオン（CoreDNS・vpc-cni 等）のインストール方法は2通りある。

```
方法 A: aws_eks_addon（EKS マネージドアドオン）
  → バージョン管理が AWS 任せ。設定がシンプル。

方法 B: helm_release
  → 細かいカスタマイズが可能。管理コストが上がる。
```

このプロジェクトはカオスエンジニアリングのロジック実証が目的であり、アドオンの細かいカスタマイズは不要。`aws_eks_addon` で十分。

#### tls プロバイダーを追加した理由

IRSA（IAM Roles for Service Accounts）の前提として OIDC プロバイダーの登録が必要であり、その際に EKS クラスターの TLS 証明書フィンガープリントを取得するために `data "tls_certificate"` が必要になる。

#### archive プロバイダーを使う理由

Lambda デプロイパッケージを Terraform 内で zip 化するために使用する。

### Consequences

- アドオンのバージョンは AWS の提供するデフォルトに従う（細かい制御は不要と判断）
- `tls` プロバイダーを `required_providers` に追加する必要がある
- S3 + DynamoDB によるリモートステート管理はすでに設定済みのため変更不要
