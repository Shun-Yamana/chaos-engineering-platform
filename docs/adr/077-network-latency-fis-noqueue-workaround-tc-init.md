# ADR 077 - network_latency FIS 再挑戦：tc-init init container で noqueue 制約を回避

- Status: Accepted
- Date: 2026-05-26

## Context

ADR 076 で `aws:eks:pod-network-latency` が EKS veth の `noqueue` qdisc 制約により即死することが判明し、network_latency 実験を SKIP とした。

SKIP 解消の手段として以下の 2 案を検討した。

**案 A：Envoy delay filter（L7 での遅延注入）**

http_error_inject と同じ仕組み（chaos-agent → ConfigMap パッチ → service-a rollout restart）で、abort filter の代わりに delay filter を使う。実装コストが低い反面、注入レイヤーが L7（HTTP プロキシ）であり、本物のネットワーク遅延ではない。また、テストする防衛経路（Envoy timeout → CB → stale cache）は cpu_stress（ADR 066）と実質同一のため、追加の実験価値が低い。

**案 B：FIS + init container（L3/L4 での遅延注入）**

Pod 起動時に init container で `tc qdisc replace dev eth0 root pfifo_fast` を実行し、FIS が `tc qdisc del` を実行する前に削除可能な qdisc を設置しておく。FIS は本物の TC netem 遅延を eth0 に注入できる。

**ローカル検証：**

`tc qdisc del`（FIS の操作）は `noqueue` に対して失敗するが、`tc qdisc replace` は異なるカーネル操作（`RTM_NEWQDISC` with `NLM_F_REPLACE`）を使うため成功することを Docker コンテナで確認した。

```
=== 初期状態 ===
qdisc noqueue 0: root refcnt 2
=== replace 試行 ===
replace 成功
=== replace 後の状態 ===
qdisc pfifo_fast 8001: root refcnt 17 ...
=== del 試行（FISが実行する操作）===
del 成功 → FISが動く
```

## Decision

**案 B（FIS + init container）を採用する。**

`docker/tc-init/Dockerfile`（alpine:3.19 + iproute2）をビルドして ECR に push し、`k8s/service-b.yaml` に init container として追加する。

```yaml
initContainers:
  - name: tc-qdisc-setup
    image: 203553641035.dkr.ecr.ap-northeast-1.amazonaws.com/chaos-platform/tc-init:latest
    command: ["tc", "qdisc", "replace", "dev", "eth0", "root", "pfifo_fast"]
    securityContext:
      runAsUser: 0
      runAsNonRoot: false
      allowPrivilegeEscalation: false
      capabilities:
        add: ["NET_ADMIN"]
        drop: ["ALL"]
```

## Rationale

### 案 A（Envoy delay filter）を外した理由

- 注入レイヤーが L7（HTTP）のため、FIS の本来の目的（ネットワーク層の遅延）を再現できない
- cpu_stress 実験（ADR 066）と同一の防衛経路（timeout → CB → stale cache）を検証するだけになり、新たな実験価値がない
- ポートフォリオとして「Envoy delay は cpu_stress と同じ」という説明になり、実験の独立性が失われる

### 案 B（FIS + init container）を選んだ理由

- `tc qdisc replace` が `noqueue` に対して動作することをローカル Docker で検証済み
- L3/L4 レベルの本物のネットワーク遅延を注入できる。防衛策（Envoy timeout + CB + stale cache）が実際のネットワーク障害に対して機能することを証明できる
- init container は Pod 起動時に一度だけ実行されるため、定常時のオーバーヘッドはゼロ
- namespace PSA はすでに `privileged`（ADR 076 で設定済み）のため NET_ADMIN は許可される
- 既存の FIS 実験テンプレート（`aws:eks:pod-network-latency`）をそのまま再利用できる

## Consequences

- service-b の Pod 起動に init container（数秒）が追加される。rolling update・スケールアウト時に影響するが許容範囲内。
- `seccompProfile: RuntimeDefault`（pod-level）が init container に引き継がれる。netlink ソケット操作がブロックされる場合は init container に `seccompProfile: Unconfined` を追加する。
- ECR リポジトリ `chaos-platform/tc-init` は Terraform 管理外（MUTABLE タグ）。Terraform state に取り込む場合は `terraform import` が必要。
- FIS 実験（`aws:eks:pod-network-latency`）の結果は別 ADR に記録する。
