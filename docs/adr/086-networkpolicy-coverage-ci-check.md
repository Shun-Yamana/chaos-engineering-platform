# ADR 086 - NetworkPolicy カバレッジ CI チェック

- Status: Accepted
- Date: 2026-05-27

## Context

ADR 085 で判明したとおり、`k8s/network-policy.yaml` に service-c/d の NetworkPolicy を追記し忘れたことで
`default-deny-all` による DNS ブロッキングが発生し、複数の CI 失敗デバッグに数時間を費やした。

問題は「新しいサービスを追加するときに NetworkPolicy も追加する」というルールがコードではなく
人間の記憶にのみ存在していたことにある。同じミスは service-e/f/… が追加されるたびに再発しうる。

## Decision

`scripts/check-networkpolicy-coverage.py` を作成し、deploy.yml の Deploy to EKS ジョブで
`kubectl apply` より前に実行する。

スクリプトは以下を静的解析で検証する:
1. `k8s/service-*.yaml` 内の全 Deployment から `spec.template.metadata.labels.app` を収集
2. `k8s/network-policy.yaml` 内の全 NetworkPolicy から `spec.podSelector.matchLabels.app` を収集
3. カバーされていない app ラベルが 1 つでもあれば `exit 1` してエラーメッセージを出力

## Rationale

### CI チェックを選んだ理由（Kyverno/OPA との比較）
クラスター側でポリシーを強制する Kyverno や OPA Gatekeeper は強力だが、
このポートフォリオ規模では導入・運用コストに対してリターンが薄い。
CI チェックはリポジトリ内の静的解析で完結し、クラスターへの依存がなく、
デプロイ前に開発者が気づける（fast fail）。

### `kubectl apply` より前に置いた理由
NetworkPolicy が欠落したまま apply すると、その後の rollout が 5 分待ってから失敗する。
事前チェックは AWS 認証や kubeconfig 更新が済んでいなくても実行できる pure Python なので、
ジョブの早い段階で配置することで無駄な待ち時間を排除できる。

### YAML 静的解析を選んだ理由（kubectl get との比較）
クラスターに実際に apply 済みのリソースを `kubectl get` で確認する方法もあるが、
それでは「コードに書いてあるか」ではなく「クラスターに存在するか」を検証することになり、
ドリフト（クラスターに手動で apply したが YAML に反映されていない状態）を見逃す。
静的解析はコードを正として扱うため GitOps の原則に沿っている。

## Consequences

- 新しい `k8s/service-X.yaml` を追加したとき、対応する NetworkPolicy なしに main へ push すると CI が fail する
- スクリプトは PyYAML のみを依存とし、ubuntu-latest runner に標準インストールされている
- `app` ラベルを使わない特殊なワークロード（DaemonSet 等）はスコープ外（`service-*.yaml` のみ対象）
- チェックのスコープを広げたい場合は glob パターンと抽出ロジックを変更するだけでよい
