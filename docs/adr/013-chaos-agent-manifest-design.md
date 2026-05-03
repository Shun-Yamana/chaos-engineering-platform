# ADR 013 - chaos-agent マニフェスト設計

- Status: Accepted
- Date: 2026-05-03

## Context

chaos-agent（`chaos/agent.py`）を EKS Fargate 上でどう動かすかを決定する必要があった。

設計上の論点は主に3つあった。

1. **実行形態**: K8s Pod（常駐）か Lambda（都度起動）か
2. **FIS template ID の渡し方**: Deployment env var 直接か ConfigMap 経由か
3. **SecurityContext の適用範囲**: service-a/b と同一設定でよいか

## Decision

**chaos-agent を K8s Deployment（replicas: 1）として常駐させ、DynamoDB ポーリングで実験コマンドを受け取る。**

```
api_handler Lambda
  → chaos-experiments テーブルに status: "pending" で書き込み
  → chaos-agent Pod がポーリングで検知
  → ChaosAgent.run() 実行
  → status: "completed" / "failed" に更新
```

## Rationale

### Lambda 方式を外した理由

`agent.py` は `load_incluster_config()` を前提に書かれており、Lambda から EKS K8s API へ接続するには以下の追加実装が必要になる。

- EKS 認証トークンの取得（`boto3` 経由）
- K8s クライアントのプログラム的な設定（kubeconfig ファイル不使用）
- ephemeral container patch・pod exec・pod delete といった K8s API 操作の Lambda 内での安定実行

これらの実装コストは高く、ポートフォリオのスコープを超える。

### K8s Pod（DynamoDB ポーリング）を選んだ理由

- `agent.py` の既存実装をそのまま使える（`load_incluster_config` + IRSA）
- `chaos-experiments` DynamoDB テーブルはすでに設計済み。api_handler が書き込み、chaos-agent がポーリングして拾う流れは追加インフラなしで実現できる
- ポーリング間隔を数秒に設定すれば、ポートフォリオ用途では十分な応答性を確保できる

### FIS template ID: env var 直接を選んだ理由

ConfigMap 経由にすると `Deployment → ConfigMap → Terraform output` の3段管理になる。FIS template ID は `terraform apply` 後に変わらない静的な値であり、ConfigMap を挟む必要がない。CI/CD パイプラインで `terraform output → kubectl set env` で更新する。

```yaml
env:
  - name: FIS_TEMPLATE_SERVICE_A
    value: "<terraform output fis_template_service_a_id>"
  - name: FIS_TEMPLATE_SERVICE_B
    value: "<terraform output fis_template_service_b_id>"
```

### SecurityContext の適用範囲

chaos-agent は `cpu_stress` / `memory_stress` 実験で ephemeral container 内に `pkill` を実行する。`pkill` はプロセスファイルシステムへのアクセスが必要なため `readOnlyRootFilesystem: true` は適用しない（ADR 011 item 11 に記載済み）。

`automountServiceAccountToken: false` も適用しない。IRSA は ServiceAccount トークンを使って IAM ロールを引き受けるため、トークンのマウントが必須。

適用する設定：`runAsNonRoot: true`、`allowPrivilegeEscalation: false`

### RBAC

K8s API の操作権限は ClusterRole で付与する（`docs/iam-design.md` §2 参照）。

| リソース | 権限 |
|---------|------|
| `pods` | get, list, delete |
| `pods/ephemeralcontainers` | patch |
| `pods/exec` | create |
| `deployments` | get, patch |

Namespace スコープではなく ClusterRole にする理由：chaos-agent は `default` Namespace の Pod と Deployment を操作するが、将来的に複数 Namespace を対象にする拡張を考慮する。

### replicas: 1

chaos-agent が複数台並行して動くと、同一の pending 実験を複数 Pod が拾って二重実行されるリスクがある。実験制御の一貫性を保つため replicas: 1 に固定する。PDB は設定しない。chaos-agent が落ちて実験が止まることは、制御不能な実験が継続するより安全。

## Consequences

- `lambda.tf` の `lambda-api-handler-role` から `lambda:InvokeFunction` の chaos-agent 権限を削除する（DynamoDB 経由に変更するため不要）
- `agent.py` にポーリングループの実装が必要（`chaos-experiments` テーブルを定期スキャンして `status: "pending"` のレコードを処理する）
- replicas: 1 のため chaos-agent Pod 障害時に実験がキューに溜まる。障害検知は CloudWatch アラームで行う
- FIS template ID は `terraform apply` 後に手動で `kubectl set env` するか、GitHub Actions の CD ジョブで自動更新する
