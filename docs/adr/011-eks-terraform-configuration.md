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

---

## 3. ネットワーク設定

### Context

EKS Fargate では Pod 1つが ENI 1つを占有し VPC の IP を直接消費する。既存の private サブネット `/24`（254 IP）は Pod スケールアウト時に枯渇するリスクがある。また SG の設計として Fargate 環境に適した層構造を決める必要があった。

### Decision

**private サブネットを `/20` に拡張し、SG をクラスター・Pod ENI・サービスの3層で設計する。**

| 種別 | CIDR |
|------|------|
| public（ALB・NAT GW） | 10.0.0.0/24、10.0.1.0/24 |
| private（Pod） | 10.0.16.0/20、10.0.32.0/20 |

### Rationale

#### private サブネットを `/20` にした理由

Fargate は Pod = ENI = IP の 1対1消費のため、スケールアウトで IP が枯渇しやすい。`/20` は 1 AZ あたり 4094 IP を確保でき、大幅なスケールアウトにも対応できる。VPC `10.0.0.0/16` の空き空間に余裕があるため変更コストは低い。

#### SG を3層にした理由

| 層 | 対象 | 役割 |
|----|------|------|
| ① Cluster SG | EKS コントロールプレーン | API サーバー ↔ Pod の通信制御 |
| ② Pod ENI SG | 全 Fargate Pod の ENI | Pod 間通信・AWS API 呼び出し |
| ③ Service SG | サービス単位（SecurityGroupPolicy） | service-a/b・chaos-agent の個別制御 |

EC2 ノードベース EKS の「ノード SG」に相当するのが Fargate では「Pod ENI SG」になる。③ は `SecurityGroupPolicy` CRD で Pod に付与し、最小権限の原則をサービス単位で実現する。

### Consequences

- `terraform/modules/vpc/variables.tf` の `private_subnet_cidrs` のデフォルト値を `/20` に変更する
- ③ Service SG は vpc-cni アドオン有効化後に `SecurityGroupPolicy` CRD で設定する（item 7 参照）
- public サブネットは ALB・NAT GW のみ使用するため `/24` のまま変更不要

---

## 4. IAM ロールとポリシー

### Context

EKS 固有の IAM として、Fargate Pod 実行ロールの権限範囲をどう定めるかが論点となった。AWS マネージドポリシー `AmazonEKSFargatePodExecutionRolePolicy` は ECR・CloudWatch Logs に `"Resource": "*"` を持ち、最小権限の原則に反する。

### Decision

**Fargate Pod 実行ロールは AWS マネージドポリシーを使わず、カスタマーマネージドポリシーで最小権限に絞る。**

```
ECR 認証:     ecr:GetAuthorizationToken          → Resource: *（AWS 仕様上変更不可）
ECR pull:     BatchCheckLayerAvailability 等      → Resource: chaos-* リポジトリのみ
Logs:         CreateLogGroup/Stream/PutLogEvents  → Resource: /aws/eks/chaos-cluster/*
```

その他の EKS 固有 IAM の定義場所：

| リソース | 定義場所 |
|---------|---------|
| EKS クラスターロール | `modules/eks/main.tf`（変更不要） |
| Fargate Pod 実行ロール | `modules/eks/main.tf`（カスタムポリシーに変更） |
| OIDC プロバイダー | `modules/eks/main.tf`（新規追加） |
| chaos-agent IRSA ロール | `terraform/iam.tf`（新規） |
| FIS 実行ロール | `terraform/iam.tf`（新規） |
| EKS アクセスエントリ（GitHub Actions） | `terraform/eks.tf`（新規） |

### Rationale

#### AWS マネージドポリシーを使わない理由

`AmazonEKSFargatePodExecutionRolePolicy` は `Resource: *` で広く権限を与えており最小権限の原則に反する。Fargate Pod が侵害された場合、アカウント内の全 ECR リポジトリへのアクセスが可能になるリスクがある。カスタマーマネージドポリシーで `chaos-*` リポジトリと特定のロググループに絞ることでリスクを限定する。

### Consequences

- AWS がマネージドポリシーを更新しても自動反映されないため、必要に応じて手動で追従する
- `ecr:GetAuthorizationToken` は AWS 仕様でリソース指定不可のため `Resource: *` が残る（許容）
- chaos-agent IRSA ロール・FIS 実行ロールの詳細は `docs/iam-design.md` に定義済み

---

## 5. ノードグループ設定

### Decision

**ノードグループは使用しない。**

### Rationale

ADR 001 で EKS Fargate を採用済み。Fargate は Pod 単位でコンピューティングが割り当てられるためノード（EC2）の管理が不要。ノードグループを追加するとノードのパッチ管理・AMI 更新・スケーリング設定が発生し、カオスエンジニアリングのロジック実証というプロジェクト目的から外れる。

---

## 6. Fargate プロファイル

### Context

既存プロファイルは `default`（service-a/b）と `kube-system`（CoreDNS）の2つ。chaos-agent と Container Insights 用の Namespace をどう扱うかを決める必要があった。

### Decision

**以下の4つの Fargate プロファイルを用意する。**

| プロファイル | Namespace | 用途 |
|------------|-----------|------|
| `default` | `default` | service-a・service-b |
| `kube-system` | `kube-system` | CoreDNS |
| `chaos` | `chaos` | chaos-agent |
| `aws-observability` | `aws-observability` | Container Insights（Fluent Bit） |

全プロファイルとも private サブネットに配置する。

### Rationale

#### `chaos` Namespace を分離した理由

chaos-agent を `default` に置くと service-a/b と RBAC の境界が曖昧になる。`chaos` Namespace に分離することで ClusterRole の適用範囲が明確になり、chaos-agent の権限スコープを意図通りに制御できる。

#### `aws-observability` を追加した理由

EKS Fargate で CloudWatch Container Insights を有効にするには Fluent Bit を `aws-observability` Namespace で動かす必要がある。このプロファイルがないと Fluent Bit Pod が Fargate で起動できない。

### Consequences

- chaos-agent の ServiceAccount は `chaos` Namespace に作成する
- IRSA の trust policy の `sub` も `system:serviceaccount:chaos:chaos-agent` になる（`docs/iam-design.md` 記載済み）
- `aws-observability` Namespace と Fluent Bit の ConfigMap は item 7 のアドオンで自動設定される

---

## 7. EKS アドオン設定

### Context

EKS クラスターの正常動作とオブザーバビリティに必要なアドオンを選定する必要があった。アドオンの管理方法（Helm vs EKS マネージドアドオン）は item 1 で `aws_eks_addon` に決定済み。

### Decision

**以下の3アドオンを使用する。**

| アドオン | 用途 |
|---------|------|
| `coredns` | Pod の DNS 解決 |
| `vpc-cni` | Pod への VPC IP 割り当て |
| `amazon-cloudwatch-observability` | Container Insights（メトリクス）+ Fluent Bit（ログ）|

### Rationale

#### `kube-proxy` を外した理由

Fargate では各 Pod が独自のネットワーク空間を持つため、iptables を管理する `kube-proxy` は動作しない。Fargate 環境では不要。

#### `eks-pod-identity-agent` を外した理由

IRSA の後継として AWS が推奨する新方式だが、本プロジェクトはすでに IRSA で設計済み。移行コストに見合うメリットがないためスコープ外。

#### `amazon-cloudwatch-observability` を採用した理由

Container Insights と Fluent Bit を個別に手動設定する従来の方法と比べ、このアドオン1つで以下をカバーできる。

- CloudWatch Container Insights（メトリクス収集）
- Fluent Bit（ログ収集・S3 転送）
- `aws-observability` Namespace の自動セットアップ
- Fargate 対応（Fluent Bit をサイドカーとして自動挿入）

`sli_calculator.py` が依存する CloudWatch メトリクスの収集基盤がこのアドオンで整う。

### Consequences

- `amazon-cloudwatch-observability` アドオン専用の IRSA ロールが必要（`CloudWatchAgentServerPolicy` を付与）
- item 6 で追加した `aws-observability` Fargate Profile がこのアドオンの前提となる
- item 12（モニタリング・オブザーバビリティ）はこのアドオンでほぼカバーされる

---

## 8. KMS 暗号化設定

### Context

item 2 で K8s Secrets を KMS で暗号化することを決定した。KMS キーとして AWS マネージドキーとカスタマーマネージドキー（CMK）のどちらを使うかを決める必要があった。

### Decision

**AWS マネージドキー（`aws/eks`）を使用する。**

### Rationale

CMK が必要になるのは以下のケースに限られる。

- マルチアカウントでキーを共有する
- PCI-DSS・HIPAA 等のコンプライアンス要件がある
- 侵害時にキーを即時無効化したい
- キー使用をリソース単位で細かく監査したい

本プロジェクトは単一アカウント・ポートフォリオ用途であり上記のいずれにも該当しない。AWS マネージドキーは無料かつ自動ローテーションされるため、管理コストなしに暗号化の要件を満たせる。

### Consequences

- CMK の月額コスト（$1/月）が不要
- キーポリシーの管理が不要
- Fargate のため EBS 暗号化は対象外

---

## 9. CloudWatch ログ設定

### Decision

**全ロググループの保持期間を30日に統一する。API Gateway アクセスログ・VPC Flow Logs は設定しない。**

| ロググループ | 保持期間 | 収集方法 |
|-------------|---------|---------|
| EKS コントロールプレーン（api・audit・authenticator） | 30日 | Terraform |
| Lambda 3関数 | 30日 | Lambda が自動作成 |
| FIS | 30日 | Terraform |
| Pod／コンテナ | 30日 | amazon-cloudwatch-observability アドオンが自動収集 |

### Rationale

- デフォルトの無期限保存はコストが増加し続けるため明示的に設定する
- Pod／コンテナログは item 7 のアドオンが自動収集するため手動設定不要
- API Gateway アクセスログは Lambda ログで代替できるため不要
- VPC Flow Logs は FIS network_latency 実験の観測を CloudWatch メトリクスで行うため不要

### Consequences

- ポートフォリオ用途のためコスト最小化を優先して30日に統一
- 30日を超えるログ分析が必要になった場合は保持期間の変更が必要

---

## 10. EKS Auto Mode 設定

### Decision

**使用しない。**

### Rationale

EKS Auto Mode は EC2 ノードの自動プロビジョニング・管理機能であり、Fargate 環境には適用されない（ADR 001 参照）。

---

## 11. セキュリティ設定

### Decision

以下の7つのセキュリティ設定を採用する。

| # | 設定 | 対象 |
|---|------|------|
| ① | Pod Security Standards（baseline） | chaos Namespace |
| ② | EKS アクセスエントリ（API モード） | GitHub Actions ロール |
| ③ | Secrets Store CSI Driver | スコープ外 |
| ④ | Network Policy | service-a/b 間の通信制御 |
| ⑤ | Container Security Context | 全コンテナ |
| ⑥ | ECR イメージスキャン | push 時に自動実行 |
| ⑦ | PodDisruptionBudget | service-a・service-b |

### Rationale

#### ① Pod Security Standards を `baseline` にした理由

`restricted` にすると ephemeral container の実行が制限される可能性があり、cpu_stress・memory_stress の注入（ADR 010）が動作しなくなる。`baseline` は特権コンテナや hostPath マウントを禁止しつつ ephemeral container を許容する現実的な選択。

#### ② アクセスエントリ（API モード）
item 2 で決定済み。`aws_eks_access_entry` で GitHub Actions ロールを登録し、`aws-auth` ConfigMap の手動管理を排除する。

#### ③ Secrets Store CSI Driver は不要
Slack webhook URL は `Experiment` dataclass で渡す設計であり、K8s Secret への同期が必要なユースケースがない。

#### ④ Network Policy を追加した理由

SG 3層設計（item 3）はインフラレベルの制御だが、Network Policy は K8s レベルで Pod 間通信を制御する。VPC CNI アドオンが有効なため Fargate でも使用できる。service-b からしか service-a に到達できない制御を入れることで、意図しない横断アクセスを防ぐ。

#### ⑤ Container Security Context
`runAsNonRoot`・`allowPrivilegeEscalation: false` を全コンテナに設定する。chaos-agent の ephemeral container は `pkill` を実行するため `readOnlyRootFilesystem` は対象外とする。

#### ⑥ ECR イメージスキャン
`image_scanning_configuration { scan_on_push = true }` を有効にするだけで push 時に脆弱性スキャンが走る。設定コストがほぼゼロでセキュリティの可視性が上がる。

#### ⑦ PodDisruptionBudget（PDB）
ADR 005 で「未設定・要対応」として記録済みの積み残し。`minAvailable: 1` を service-a・service-b に設定することで、pod_kill 実験中でも最低1台が稼働し続けることを保証し、実験の爆発半径を制御する。

### Consequences

- Network Policy は `aws-eks-addon` の vpc-cni が有効化された後に適用する
- PDB は K8s マニフェスト（`k8s/`）に追加する
- ECR スキャン結果は AWS Security Hub または ECR コンソールで確認する

---

## 12. モニタリング・オブザーバビリティ

### Context

カオス実験中のメトリクス監視・アラート・実験操作 UI の構成を決める必要があった。データは CloudWatch に集約済み（ADR 002・item 7）であり、その上に何を乗せるかが論点となった。

### Decision

**以下の構成を採用する。**

```
メトリクス監視: Amazon Managed Grafana（CloudWatch データソース）
リアルタイムアラート: CloudWatch Alarm → SNS → Slack + auto_stopper Lambda
実験操作 GUI: React フロントエンド（S3 + CloudFront）→ API Gateway
```

### Rationale

#### Amazon Managed Grafana を選んだ理由

| 選択肢 | 判断 |
|--------|------|
| CloudWatch ダッシュボード | 無料だが見た目がシンプルでポートフォリオとしての訴求力が低い |
| **Amazon Managed Grafana** | 業界標準 UI・CloudWatch をそのままデータソースに使える・~$9/月 |
| Self-hosted Grafana（EKS 上） | Managed と同等だが管理コストが上がる |

Datadog・New Relic はエージェント導入コストともにスコープ外。

#### CloudWatch Alarm を追加した理由

現状の EventBridge 定期実行方式は SLO 違反から停止まで最大 N 分の遅延が生じる。CloudWatch Alarm は閾値超過を即時検知して SNS をトリガーするため、リアルタイム停止が実現できる。EventBridge は定期チェックのフェイルセーフとして併用する。

```
CloudWatch Alarm（エラーレート > 5%）
  → SNS（chaos-alerts）
  → ① Slack 通知（即時）
  → ② auto_stopper Lambda（即時停止）
```

#### フロントエンドを S3 + CloudFront にした理由

`frontend/` ディレクトリの React アプリを EKS 上に置くと管理コストが増える。静的ファイルのホスティングは S3 + CloudFront で十分であり、API Gateway エンドポイントに接続して実験の開始・停止・一覧を操作できる。

### Consequences

- Amazon Managed Grafana の IAM ロールに `CloudWatchReadOnlyAccess` の付与が必要
- CloudWatch Alarm のアラーム対象メトリクスは `sli_calculator.py` が書き込む `chaos-platform/SLI` ネームスペースの値
- S3 バケット・CloudFront ディストリビューションの Terraform 定義が必要
- EventBridge の定期実行は Alarm のフェイルセーフとして残す

---

## 13. オートスケーリング

### Decision

**service-a・service-b に HPA を設定する。具体的な閾値・レプリカ数は実験を通じて調整する。**

### Rationale

Fargate 環境では Cluster Autoscaler（EC2 ノード専用）は不要。HPA のみが対象。

service-a と service-b は cpu_stress・memory_stress 実験の観測対象であり、ADR 006/007 の合格基準「HPA が不要なスケールアウトをしないこと」は HPA が存在することを前提にしている。chaos-agent はスケールアウト不要のため対象外。

閾値・min/maxReplicas は実験結果を見ながら調整するものであり、初期値はデプロイ後のチューニングフェーズで確定する。

### Consequences

- HPA マニフェストは `k8s/` に追加する
- 初期値（CPU 70%・min 2・max 5 など）はあくまで出発点であり、実験結果に基づいて更新する

---

## 14. ストレージ設定

### Decision

**EBS・EFS 等の永続ストレージは使用しない。**

### Rationale

service-a・service-b・chaos-agent はいずれもステートレスな設計であり、Pod 再起動後もデータを維持する必要がない。実験レコード・SLI・SLO はすべて DynamoDB に保存するため Pod レベルの永続ストレージは不要。

EKS Fargate は EBS の直接マウントをサポートしていないため、EBS を使いたい場合は EC2 ノードへの移行が必要になるが、ADR 001 で Fargate を採用済みのため該当しない。

---

## 15. サービスメッシュ（Istio）

### Decision

**Istio は使用しない。**

### Rationale

Istio が提供するトラフィック制御・相互 TLS・オブザーバビリティは本プロジェクトには過剰である。

| 機能 | 本プロジェクトでの代替 |
|------|----------------------|
| トラフィック制御 | ALB ルーティング |
| 相互 TLS | VPC 内通信（信頼境界はSGとNetworkPolicy） |
| オブザーバビリティ | CloudWatch + Grafana（item 12） |

Istio のインストール・設定・アップグレードは大きな運用コストを伴い、カオスエンジニアリングのロジック実証というプロジェクト目的から外れる。

---

## 16. GitOps（ArgoCD）

### Decision

**ArgoCD は使用しない。GitHub Actions による CI/CD を継続する。**

### Rationale

ArgoCD は複数チーム・複数クラスターの GitOps 管理に威力を発揮するが、本プロジェクトは単一リポジトリ・単一クラスターの1人開発である。

GitHub Actions（`github-actions-role` OIDC）がすでに設計済みであり（item 4・`docs/iam-design.md`）、`kubectl apply` と Lambda デプロイを GitHub Actions で実行する構成で十分。ArgoCD を追加するとクラスター内に別の管理コンポーネントが増え、カオス実験の観測対象と混在する。

---

## 17. 変数定義

### Decision

**以下の変数を Terraform の入力変数として外部化する。**

| 変数名 | 型 | 用途 |
|-------|----|------|
| `aws_region` | string | デプロイ先リージョン |
| `cluster_name` | string | EKS クラスター名（デフォルト: `chaos-cluster`） |
| `kubernetes_version` | string | Kubernetes バージョン（デフォルト: `1.32`） |
| `private_subnet_cidrs` | list(string) | private サブネット CIDR（item 3 で `/20` に決定） |
| `public_subnet_cidrs` | list(string) | public サブネット CIDR |
| `github_actions_role_arn` | string | EKS アクセスエントリに登録する GitHub Actions OIDC ロール ARN |
| `dynamodb_table_name` | string | Terraform リモートステート用 DynamoDB テーブル名 |
| `tfstate_bucket` | string | Terraform リモートステート用 S3 バケット名 |
| `slack_webhook_url` | string | auto-stopper の Slack 通知先（sensitive） |
| `account_id` | string | AWS アカウント ID（ARN 構築に使用） |

### Rationale

ハードコードを避けてパラメータを外部化することで、リージョンやアカウントが変わっても `terraform.tfvars` の変更だけで対応できる。`slack_webhook_url` は `sensitive = true` を設定して Terraform の出力ログに表示されないようにする。

### Consequences

- `terraform/variables.tf` に全変数を定義する
- `terraform/terraform.tfvars.example` を提供し、実際の値は `.gitignore` で除外する
- `account_id` は `data "aws_caller_identity"` で動的取得する方法も有効（ハードコード回避）
