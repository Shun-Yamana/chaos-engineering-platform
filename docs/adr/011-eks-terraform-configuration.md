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

---

## 2. EKS クラスター本体設定

### Context

EKS クラスター本体に関して、エンドポイントアクセス・Kubernetes バージョン・OIDC・ゾーンシフト・アクセス管理方式・暗号化・ログの各設定を決定する必要があった。

### Decision

| 設定 | 値 |
|------|---|
| エンドポイント | public + private。`public_access_cidrs` で GitHub Actions IP 帯に制限 |
| Kubernetes バージョン | EKS サポート最新安定版（1.32 または 1.33） |
| OIDC プロバイダー | `aws_iam_openid_connect_provider` を追加 |
| ゾーンシフト | `enabled = true` |
| アクセス管理方式 | `API` モード、`bootstrap_cluster_creator_admin_permissions = true` |
| Secrets 暗号化 | KMS（詳細は item 8） |
| コントロールプレーンログ | `api`・`audit`・`authenticator` の3種 |

### Rationale

#### エンドポイント：public を残す理由

private のみにすると GitHub Actions（パブリックインターネット）からクラスターに到達できなくなる。self-hosted runner や CodeBuild を VPC 内に置く方法もあるが、ポートフォリオのスコープを超える。`public_access_cidrs` で GitHub Actions の IP 帯に絞ることでセキュリティと利便性を両立する。

#### アクセス管理：`API` モードを選んだ理由

従来の `CONFIG_MAP`（aws-auth ConfigMap）は手動管理でミスが起きやすく Terraform との相性が悪い。`API` モードは `aws_eks_access_entry` リソースで宣言的に管理できる。

#### ログを3種に絞った理由

`controllerManager` と `scheduler` は Fargate 環境では有用な情報が少ない。`api`・`audit`・`authenticator` の3種で「誰が何を操作したか」「IRSA の認証が通ったか」を確認するには十分であり、CloudWatch Logs のコストを抑えられる。

#### ゾーンシフトを有効にした理由

有効化のコストはゼロで、AZ 障害時のフェイルオーバー対応を設計に組み込んでいることを示せる。

### Consequences

- `public_access_cidrs` に設定する GitHub Actions の IP 帯は定期的に変わるため、運用上の管理が必要
- KMS キーの作成は item 8 で行う。クラスター作成前に KMS キーが存在している必要がある（depends_on の設定が必要）
- `authenticator` ログにより IRSA のトラブルシューティングが可能になる
