# ADR 073 - metrics-server を default namespace の EC2 ノードで動かす

- Status: Accepted
- Date: 2026-05-24

## Context

HPA が `cpu: <unknown>/60%` を返し続けていた。原因を調査すると metrics-server が未インストールだった。

標準の `kubectl apply -f https://…/components.yaml` でインストールすると、metrics-server が `kube-system` namespace に作成されるが、`kube-system` には Fargate profile が存在し label selector がないため全 Pod を捕捉する。EC2 向けの `nodeSelector` を指定しても Fargate scheduler が先に処理して `MatchNodeSelector failed` で Pending のまま止まった。

さらに kube-system の Fargate Pod は `default-deny-all` NetworkPolicy によって K8s API（172.20.0.1:443）への egress がブロックされ、起動しても即 panic でクラッシュした。

## Decision

metrics-server を `default` namespace にデプロイし、`nodeSelector: eks.amazonaws.com/nodegroup: chaos-platform-node-group` で EC2 ノードに固定する。必要な NetworkPolicy（K8s API 443 + kubelet 10250 + DNS 53）を `default` namespace に追加する。

## Rationale

### kube-system のまま動かさない理由
kube-system Fargate profile は label selector なしで namespace 全体を捕捉する（immutable のため変更不可）。Fargate 上では NetworkPolicy 問題が再現するため同じ対処が必要になる。

### 別途 namespace を作らない理由
`default` namespace ならすでに EC2 nodegroup が割り当て済みで NetworkPolicy の管理も集中できる。`k8s/metrics-server.yaml` に ServiceAccount・ClusterRole・ClusterRoleBinding・APIService を含めて一元管理する。

### ClusterRole で node/pod metrics を取る理由
metrics-server は全 namespace の Pod metrics を収集するため Namespace スコープ Role では不十分。

## Consequences

- `kubectl top nodes/pods` と HPA CPU metrics が正常に動作する。
- `default` namespace の RBAC（fis-agent Role）と metrics-server RBAC が同居するため、RBAC の見通しが悪くなる。将来的に kube-system Fargate profile に label selector を追加して kube-system に戻すことが望ましい（ADR 更新要）。
- kube-system Fargate profile の label selector 変更は Fargate profile の immutability により terraform destroy+apply が必要。
