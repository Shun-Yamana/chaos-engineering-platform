# ADR 012 - K8s Deployment マニフェスト設計：service-a / service-b

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

chaos-agent の Deployment 設計（RBAC・IRSA・SecurityContext の例外）は別 ADR に記述する。

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

## Consequences

- `readOnlyRootFilesystem: true` により、アプリケーションが `/tmp` 等への書き込みを必要とする場合は `emptyDir` ボリュームの追加が必要になる
- `preStop: sleep 5` は `terminationGracePeriodSeconds`（デフォルト 30 秒）の範囲内であるため追加設定不要
- chaos-agent の ephemeral container（stress-ng）には `readOnlyRootFilesystem` を適用しない（ADR 011 item 11 記載済み）
- `topologySpreadConstraints` の `whenUnsatisfiable: DoNotSchedule` により、単一 AZ 環境では Pod が Pending になる可能性がある（本番 2AZ 構成では問題なし）
