# ADR 042 - chaos-agent ClusterRole への networking.k8s.io/ingresses get 権限追加

- Status: Accepted
- Date: 2026-05-16

## Context

`chaos/agent.py` の `_discover_service_b_url()` は `read_namespaced_ingress()` を呼び出して
ALB の URL を動的に取得するが、`ClusterRole` に `networking.k8s.io` apiGroup の権限がなかった。

`kubectl set env` で `SERVICE_B_URL` を事前設定している場合は起動後に上書きされるため問題が顕在化しないが、
set env が失敗した場合や pod が再起動された直後は `_discover_service_b_url()` が 403 エラーで失敗し、
traffic generator が起動しないため SLI データが流れなくなる。

`networking.k8s.io` は `apps` や `""` とは別の API グループであり、明示的に ClusterRole に追加する必要がある。

## Decision

`k8s/chaos-agent.yaml` の ClusterRole に以下を追加する。

```yaml
- apiGroups: ["networking.k8s.io"]
  resources: ["ingresses"]
  verbs: ["get"]
```

## Rationale

### list を含めない理由
`_discover_service_b_url()` は namespace と ingress 名を指定した単一リソース取得（`read_namespaced_ingress`）
のみを行い、`list_namespaced_ingress` は呼ばない。最小権限の原則に従い `get` のみを付与する。

## Consequences

- `SERVICE_B_URL` の `kubectl set env` が失敗した場合も traffic generator が正常起動する。
- `chaos-agent.yaml` の変更は `kubectl apply` で反映する必要がある（terraform 管理外）。
