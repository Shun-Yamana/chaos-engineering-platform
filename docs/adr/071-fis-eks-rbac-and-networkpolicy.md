# ADR 071 - FIS EKS 実験に必要な RBAC と NetworkPolicy の整備

- Status: Accepted
- Date: 2026-05-24

## Context

`aws:eks:pod-delete` アクションを含む FIS 実験を実行すると、FIS は対象 Namespace（default）にエージェント Pod（`fispod-*`）を投入して実験を実行する。この Pod が起動してもすぐに `FIS Pod failed to initiate` で実験が失敗し続けた。

ログを調べると2つの独立した問題があった：

1. **NetworkPolicy**：`default-deny-all` がすべての Pod の Egress を遮断しており、FIS Pod が AWS FIS コントロールプレーン（HTTPS 443）と CoreDNS（UDP/TCP 53）に到達できなかった。
2. **RBAC**：FIS Pod は `system:serviceaccount:default:default`（default ServiceAccount）として動作し、実験の協調に使う ConfigMap の作成・更新と、対象 Pod の削除を行う。しかし default ServiceAccount にはこれらの権限がなく `configmaps is forbidden` エラーで即座に FAILED した。

## Decision

FIS EKS 実験向けに、以下の2リソースを追加する。

1. **NetworkPolicy `fis-agent`**（default namespace）：`experimentId` ラベルを持つ Pod（= FIS Pod）に HTTPS 443 Egress + CoreDNS 53 Egress を許可する。
2. **Role / RoleBinding `fis-agent`**（default namespace）：default ServiceAccount に ConfigMap（create/get/patch/update）と Pod（get/list/delete）の権限を付与する。

## Rationale

### NetworkPolicy を緩和せず専用ルールを追加した理由
`default-deny-all` を廃止すれば問題は解決するが、ゼロトラスト前提のセキュリティ設計（ADR 012）が崩れる。FIS Pod は必ず `experimentId=<id>` ラベルを持つので、`matchExpressions: [{key: experimentId, operator: Exists}]` でピンポイントに許可できる。

### ClusterRole ではなく Role を選んだ理由
FIS Pod の操作対象は常に自 Namespace（default）内に限定される。ClusterRole にすると他 Namespace の ConfigMap・Pod にも権限が及ぶためリスクが高い。

## Consequences

- FIS EKS 実験（pod-delete, cpu-stress 等）が正常に実行できるようになった。
- default ServiceAccount に Pod 削除権限が付くが、対象は default Namespace 内に限定される。
- 新たな FIS アクションタイプを追加する際は RBAC が十分かどうか再確認すること（例：network-latency 系は別の権限が必要な場合がある）。
