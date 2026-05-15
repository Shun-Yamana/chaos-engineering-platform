# ADR 035 - ClusterRole に deployments/scale subresource を追加

- Status: Accepted
- Date: 2026-05-16

## Context

chaos-agent の emergency_recover は SLO 違反時にサービスを scale-to-0 でダウンさせ、fault を除去し、30 秒後に元のレプリカ数に復元する自己防衛機能である。

scale 操作には `apps_v1.patch_namespaced_deployment_scale()` を使うが、chaos-agent ClusterRole には以下しか定義されていなかった:

```yaml
- apiGroups: ["apps"]
  resources: ["deployments"]
  verbs: ["get", "patch"]
```

`deployments/scale` は Kubernetes の **subresource** であり、`deployments` とは別のリソースとして RBAC で評価される。`deployments` への `patch` 権限があっても `deployments/scale` への `patch` は自動的に許可されない。

結果、emergency_recover が 403 Forbidden で失敗し、サービスが scale-to-0 されず自己防衛が機能しなかった。

## Decision

chaos-agent ClusterRole に `deployments/scale` への `patch` 権限を追加する。

## Rationale

### `deployments` に `*`（全 verb）を付与する案を外した理由

`delete` や `create` まで付与するのは最小権限原則に反する。chaos-agent がスケールしか必要としない操作に不要な権限を与えるべきではない。

### `deployments/scale` に `patch` のみ付与を選んだ理由

- `patch_namespaced_deployment_scale` が必要とする権限は `patch` のみ
- `deployments` への既存の `get`・`patch` は env var 操作（inject/remove）のために引き続き必要
- 最小権限で済む

## Consequences

- Kubernetes では `resource` と `subresource` は常に別エントリが必要。同様に `deployments/status` など他の subresource を使う場合も別途追加が必要
- `patch_namespaced_deployment_scale` には `body={"spec": {"replicas": N}}` を渡せばよく、フル Deployment オブジェクトは不要（scale subresource は spec.replicas のみ受け付ける）
