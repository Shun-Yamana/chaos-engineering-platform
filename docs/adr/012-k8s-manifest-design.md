# ADR 012 - K8s Deployment マニフェスト設計：service-a / service-b / chaos-agent

- Status: Accepted
- Date: 2026-05-03

## Context

service-a・service-b を EKS Fargate 上で動かすにあたり、Deployment マニフェストに設定すべき項目を決定する必要があった。

Kubernetes の Deployment マニフェストは API フィールドが多岐にわたるが、このプロジェクトには以下の固有の制約がある。

- **EKS Fargate**: ノードを直接指定できないため、`nodeSelector` や `affinity` ではなく `topologySpreadConstraints` でAZ分散を制御する必要がある
- **カオス実験の影響**: `http_error_inject` は Deployment spec をパッチするためローリングアップデートが発生する。また `pod_kill` は Pod を強制削除するため、接続ドレインの設計が必要
- **最小権限**: ADR 011 item 11 でコンテナセキュリティコンテキストの適用を決定済み

## Decision

**以下の7項目を service-a・service-b の Deployment に適用する。**

| # | フィールド | 値 |
|---|-----------|---|
| 1 | `strategy.rollingUpdate` | maxSurge: 1 / maxUnavailable: 0 |
| 2 | `topologySpreadConstraints` | maxSkew: 1 / zone 単位 / DoNotSchedule |
| 3 | Pod `securityContext` | runAsUser: 1000, runAsGroup: 1000, seccompProfile: RuntimeDefault |
| 4 | Container `securityContext` | runAsNonRoot, allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities.drop: [ALL] |
| 5 | `lifecycle.preStop` | exec: sleep 5 |
| 6 | `automountServiceAccountToken` | false |
| 7 | `revisionHistoryLimit` | 3 |

chaos-agent の Deployment 設計は本 ADR の「chaos-agent」セクションに記述する。

## Rationale

### 1. strategy.rollingUpdate: maxSurge:1 / maxUnavailable:0

`http_error_inject` は対象 Deployment に `FAULT_RATE` 環境変数をパッチする（ADR 010）。このパッチがローリングアップデートをトリガーするため、更新中のリクエスト断を防ぐ設定が必要。

`maxUnavailable: 0` で旧 Pod を削除する前に新 Pod の Ready を待つ。`maxSurge: 1` で一時的に replicas+1 台まで増加を許容する。

デフォルト（maxSurge:25%, maxUnavailable:25%）は Pod 数が少ない構成では切り捨てが発生し意図通りに動かない。

### 2. topologySpreadConstraints を affinity より優先した理由

`podAntiAffinity` は「同一ノードに配置しない」制御だが、EKS Fargate にはノードという概念がなく（Pod ≒ ノード）、実質的に機能しない。

`topologySpreadConstraints` は `topology.kubernetes.io/zone` キーで AZ 単位の分散を直接制御できる。`whenUnsatisfiable: DoNotSchedule` により AZ 偏りが生じる場合はスケジューリングを止め、均等分散を強制する。

HPA の `minReplicas: 2` と組み合わせることで、AZ 障害時も最低1台が残ることを保証する（PDB の `minAvailable: 1` と二重の保護）。

### 3 & 4. SecurityContext

ADR 011 item 11 で決定済みのセキュリティ設定の実装。service-a・b はステートレスな HTTP API であり、ファイルシステムへの書き込みを必要としないため `readOnlyRootFilesystem: true` まで適用可能。

`seccompProfile: RuntimeDefault` はシステムコールをコンテナランタイムのデフォルトプロファイルで制限する。EKS Fargate は Firecracker MicroVM 上で動くため追加のホスト保護効果もある。

### 5. lifecycle.preStop: sleep 5

Kubernetes は Pod 削除時に以下を並行して実行する。

```
① Endpoints から Pod IP を削除（LB のルーティング更新）
② Pod に SIGTERM を送信
```

①の伝播に数秒かかるため、② が先に処理されると進行中のリクエストが切断される。`preStop: sleep 5` で SIGTERM の実行を5秒遅らせ、LB からの切り離しを待つ。

pod_kill 実験（`gracePeriodSeconds: 0`）では preStop はスキップされるが、それは意図的な強制削除であり許容する。

### 6. automountServiceAccountToken: false

service-a・b は K8s API を直接呼ばない。デフォルトでは ServiceAccount トークンが `/var/run/secrets/kubernetes.io/serviceaccount/` に自動マウントされるが、これは攻撃者に K8s API へのアクセス手段を与えるリスクになる。明示的に無効化する。

### 7. revisionHistoryLimit: 3

デフォルト値 10 は ReplicaSet を10世代保持する。`http_error_inject` が繰り返し Deployment をパッチする性質上、履歴が蓄積しやすい。3世代で kubectl rollout undo の実用的な範囲をカバーしつつ、不要なリソースの蓄積を防ぐ。

## Consequences（service-a / service-b）

- `readOnlyRootFilesystem: true` により、アプリケーションが `/tmp` 等への書き込みを必要とする場合は `emptyDir` ボリュームの追加が必要になる
- `preStop: sleep 5` は `terminationGracePeriodSeconds`（デフォルト 30 秒）の範囲内であるため追加設定不要
- `topologySpreadConstraints` の `whenUnsatisfiable: DoNotSchedule` により、単一 AZ 環境では Pod が Pending になる可能性がある（本番 2AZ 構成では問題なし）

---

## NetworkPolicy 設計

### Context

ADR 011 item 11 で「NetworkPolicy を追加する」と決定したが、具体的な設計は本セクションで定める。対象は `default` Namespace（service-a, service-b）と `chaos` Namespace（chaos-agent）。

EKS Fargate では VPC CNI の NetworkPolicy 強制モードを明示的に有効化しなければ、NetworkPolicy リソースを apply しても通信制御が一切機能しない。

### Decision

**5つのポリシーを定める。**

| # | 対象 | 内容 |
|---|------|------|
| ① | default・chaos Namespace 全 Pod | Default Deny All（Ingress/Egress 全拒否） |
| ② | service-a | Ingress: service-b のみ / Egress: CoreDNS のみ |
| ③ | service-b | Ingress: ALB public サブネット / Egress: service-a + CoreDNS |
| ④ | chaos-agent | Ingress: なし / Egress: K8s API + AWS API + CoreDNS |
| ⑤ | vpc-cni addon | `enableNetworkPolicy: true` で NetworkPolicy 強制を有効化 |

### Rationale

#### ① Default Deny All を入れた理由

NetworkPolicy が設定されていない Pod はデフォルトで全通信が許可される。Default Deny を先に入れることで「許可されていない Pod は全遮断」という安全側のベースラインを確立する。将来 Namespace に追加される Pod が意図せず全通になるリスクを防ぐ。

#### ② service-a の制御

service-a は service-b から呼ばれる上流 API。chaos-agent は K8s API Server 経由で ephemeral container パッチや pod exec を行うため、service-a への直接ネットワーク通信は発生しない。Ingress は `app: service-b` のみに絞れる。

#### ③ service-b の Ingress: ALB public サブネットに絞った理由

ALB は Pod ではないため `podSelector` で指定できない。`ipBlock` で書く必要があるが範囲の選択肢は3つあった。

| 案 | CIDR | 問題点 |
|----|------|--------|
| A | 10.0.0.0/16（VPC 全体） | VPC 内の任意のリソースから直接到達できる |
| **B** | **10.0.0.0/24, 10.0.1.0/24（ALB サブネット）** | ALB が使う public サブネットのみ |
| C | 0.0.0.0/0 | 制限なし |

#### ④ chaos-agent の Egress を2 CIDR に分けた理由

AWS API（DynamoDB・FIS・STS）への通信は NAT Gateway 経由でインターネットに出る（VPC エンドポイント未設定）。`0.0.0.0/0:443` のみでも可能だが、K8s API Server（VPC 内）と外部 AWS API を分けて書くことで通信先の意図を明確にする。

```
10.0.0.0/16:443  → K8s API Server（VPC 内）
0.0.0.0/0:443    → DynamoDB・FIS・STS（NAT 経由）
kube-system:53   → CoreDNS
```

#### ⑤ VPC CNI の NetworkPolicy モード有効化

`enableNetworkPolicy: "true"` を vpc-cni addon に設定しなければ NetworkPolicy が機能しない。`terraform/eks.tf` の `aws_eks_addon.vpc_cni` に `configuration_values` を追加する。

### Consequences（NetworkPolicy）

- Default Deny により DNS Egress（port 53）を各ポリシーに明示しなければ名前解決が止まる
- ALB の public サブネット CIDR 変更時は service-b の NetworkPolicy も更新が必要
- VPC エンドポイント（item 12）追加後は chaos-agent の `0.0.0.0/0:443` を VPC CIDR 内に閉じる

---

## chaos-agent Deployment 設計

### Context

chaos-agent は EKS Fargate 上で常駐し、DynamoDB をポーリングして実験コマンドを受け取る。service-a/b とは異なり HTTP サーバーを持たず、K8s API・AWS API を直接呼び出す。そのため SecurityContext・probe・strategy の設定が service-a/b と異なる。

### Decision

**chaos-agent に適用するフィールド：**

| フィールド | 値 | service-a/b との差分 |
|-----------|---|---------------------|
| `strategy.type` | `Recreate` | RollingUpdate ではなく Recreate |
| `replicas` | `1` | スケールアウト不要 |
| `serviceAccountName` | `chaos-agent` | IRSA で chaos-agent-role に紐付け |
| `automountServiceAccountToken` | `true`（明示） | IRSA に必要（false にしない） |
| `revisionHistoryLimit` | `3` | 同じ |
| Pod `securityContext` | runAsUser:1000, runAsGroup:1000, seccompProfile:RuntimeDefault | 同じ |
| Container `securityContext` | runAsNonRoot, allowPrivilegeEscalation:false, **readOnlyRootFilesystem:false** | pkill のため false |
| `terminationGracePeriodSeconds` | `30`（デフォルト） | 実験中断を許容 |
| `livenessProbe` | なし | crash は K8s が検知。ハングは CloudWatch Alarm でカバー |
| `topologySpreadConstraints` | なし | replicas:1 のため不要 |
| `preStop` | なし | トラフィックを受けていない |
| `resources` | requests: cpu:64m/mem:128Mi, limits: cpu:256m/mem:256Mi | API 呼び出しのみで軽量 |
| `env` | FIS_TEMPLATE_SERVICE_A/B, TABLE_NAME, AWS_REGION | Terraform output から設定 |

### Rationale

#### strategy.type: Recreate を選んだ理由

replicas:1 で RollingUpdate を使うと、更新中に旧 Pod が生きたまま新 Pod が起動し一瞬2台並走する。chaos-agent が複数台同時に動くと DynamoDB の同じ pending レコードを二重取りするリスクがある。Recreate は旧 Pod を完全停止してから新 Pod を起動するため、この問題が発生しない。

#### readOnlyRootFilesystem: false にした理由

cpu_stress・memory_stress 実験で ephemeral container 内の `pkill stress-ng` を実行する際、プロセスファイルシステムへのアクセスが必要（ADR 011 item 11 に記載済み）。

#### livenessProbe なしにした理由

chaos-agent がポーリングループで無音ハングした場合、exec probe（PID 存在確認）では検知できない。検知するには agent が定期的にファイルを更新するロジックが必要で実装コストが上がる。Pod crash は K8s が自動再起動するため、ハングのみ CloudWatch Alarm（Pod の Running 状態監視）でカバーする。

#### terminationGracePeriodSeconds: 30（デフォルト）にした理由

実験の duration_seconds が 30 秒を超える場合、Pod 削除時に実験が中断される。これを許容する理由：

- 中断された実験は DynamoDB に status:"running" のまま残るが、auto-stopper が SLO 違反を検知して整合性を保つ
- chaos-agent の再起動後、ポーリングで拾い直すことはしない（in-memory の `_stress_targets` が消えるため）
- ポートフォリオ用途では実験の強制中断を完全に防ぐより、起きたときの挙動を把握していることの方が重要

#### RBAC（ClusterRole）

`docs/iam-design.md` §2 に定義済み。chaos Namespace の ServiceAccount `chaos-agent` に ClusterRoleBinding で付与する。

| リソース | 権限 |
|---------|------|
| `pods` | get, list, delete |
| `pods/ephemeralcontainers` | patch |
| `pods/exec` | create |
| `deployments` | get, patch |

#### DynamoDB ポーリング方式（Lambda 呼び出しを外した理由）

api_handler Lambda から `lambda:InvokeFunction` で chaos-agent Lambda を呼ぶ案も検討したが外した。`agent.py` は `load_incluster_config()` で書かれており、Lambda から EKS K8s API に接続するには EKS トークン取得・K8s クライアント設定の追加実装が必要で実装コストが高い。DynamoDB は既存のインフラを流用でき追加コストがない。

### Consequences（chaos-agent）

- `agent.py` にポーリングループの実装が必要（chaos-experiments テーブルを定期スキャンし status:"pending" を処理）
- `lambda.tf` の api-handler ロールから `lambda:InvokeFunction` の chaos-agent 権限を削除する（DynamoDB 経由に変更）
- FIS template ID は `terraform apply` 後に CI/CD で `kubectl set env` により自動更新する
- replicas:1 のため chaos-agent Pod 障害時に実験がキューに溜まる。CloudWatch Alarm で Pod の異常を検知する
- in-memory の `_stress_targets` / `_fis_experiments` は Pod 再起動で消える。実験が中断されると DynamoDB の status が "running" のまま残る可能性があり、auto-stopper による整合性回復を期待する

---

## Namespace 設計

### Decision

**`chaos` と `default` の両 Namespace を `k8s/namespace.yaml` でマニフェスト管理し、PSS ラベルを付与する。**

| Namespace | PSS ラベル | 管理方法 |
|-----------|-----------|---------|
| `chaos` | `enforce: baseline` | namespace.yaml で新規作成 |
| `default` | `enforce: baseline` | namespace.yaml で既存リソースにパッチ |

### Rationale

#### `default` Namespace にも PSS ラベルを付けた理由

service-a・b は `default` Namespace で動く。ADR 012 の Deployment 設計で baseline 準拠の SecurityContext を設定済みのため、PSS `baseline` ラベルを付けても競合しない。`chaos` だけに付けて `default` を外すと、service-a/b に特権コンテナが混入したときに検知できない。

#### `default` Namespace をマニフェストで管理する理由

`default` は Kubernetes 組み込みの Namespace だが、`kubectl apply` でラベルをパッチすることは可能（既存リソースの更新として扱われる）。手動 `kubectl label` で付けると CI/CD でのべき等性が保証できない。マニフェストに含めることでラベルをコードとして管理できる。

### Consequences

- `kubectl apply -f k8s/namespace.yaml` で `default` Namespace が PSS `baseline` に変わる。既存 Pod が baseline 違反（hostNetwork: true 等）を持つ場合は起動拒否される（本プロジェクトの Pod は準拠済みのため問題なし）
- `chaos` Namespace の PSS を `restricted` に上げると ephemeral container が制限される可能性がある（ADR 011 item 11 で `baseline` に留めた理由と同じ）

---

## 外部アクセス設計（CloudFront → ALB → service-b）

### Context

service-b は外部からのリクエストを受け付けるエントリポイント。EKS Fargate では NodePort が使えないため、ALB を IP モードで使う AWS Load Balancer Controller（LBC）が必要。

service-a は service-b からのみ呼ばれる内部サービスのため、外部アクセス経路は不要。

### Decision

**CloudFront → ALB（internal）→ service-b（ClusterIP + Ingress）の構成とする。**

| 項目 | 決定 |
|------|------|
| 外部エントリポイント | CloudFront |
| ALB スキーム | `internal`（VPC 内のみ） |
| Service type | `ClusterIP` + Ingress |
| ターゲットタイプ | `ip`（Fargate 固定） |
| LBC インストール | Helm + IRSA |
| CloudFront → ALB 認証 | カスタムヘッダーで直アクセス防止 |

### Rationale

#### CloudFront を前段に置き ALB を internal にした理由

ALB を `internet-facing` にすると ALB の DNS 名を知れば誰でも直接アクセスできる。`internal` にして CloudFront 経由のみ許可することで ALB への直接アクセスを防ぐ。CloudFront は固定のカスタムヘッダー（例: `X-Origin-Verify`）を付与し、ALB のリスナールールで該当ヘッダーがないリクエストを 403 で弾く。

#### NLB ではなく ALB を選んだ理由

CloudFront の origin に ALB を指定する場合、HTTP/HTTPS（L7）で通信するため ALB が適切。NLB は L4 のみで HTTP ヘッダー検査ができず、カスタムヘッダーによる origin 認証を実装できない。

#### Service type を ClusterIP + Ingress にした理由

`LoadBalancer` type は NLB を自動作成するが、今回は LBC + Ingress で ALB を管理する。ClusterIP + Ingress の構成により ALB の詳細設定（アノテーション）を Ingress リソースに集約できる。

#### ターゲットタイプを ip にした理由

EKS Fargate は仮想ノードが Pod ごとに存在するため、`instance` モードで登録できるノードがない。`ip` モードで Pod IP を直接ターゲットグループに登録する必要がある（Fargate の制約）。

### Consequences

- ALB は `internal` のため、CloudFront の Origin として ALB DNS 名を設定する必要がある
- カスタムヘッダーの値はシークレットとして管理し、CloudFront の Origin カスタムヘッダーと ALB リスナールールの両方に同じ値を設定する
- LBC の Helm インストールと IRSA ロールは Terraform で管理する（実装フェーズ）
- service-b の NetworkPolicy（③）は ALB サブネット CIDR からの Ingress を許可済み（本 ADR NetworkPolicy セクション参照）
