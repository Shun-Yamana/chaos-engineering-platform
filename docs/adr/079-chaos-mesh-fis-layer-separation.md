# ADR 079 - 障害注入の2層分離：FIS（AWSインフラ層）+ Chaos Mesh（K8sコンテナ層）

- Status: Accepted
- Date: 2026-05-26

## Context

ADR 054でFISをchaos-agentのバックエンドとして採用したが、2つの実験でFISの構造的な限界に突き当たった。

- **memory_stress**（ADR 075）：FIS ephemeral sidecarが独立したcgroupで動作するため、service-bのcgroupにメモリ圧力が届かない。sidecar自身がOOMKillされて実験終了。
- **network_latency**（ADR 076）：EKS vethのroot qdiscが`noqueue`であり、FISが実行する`tc qdisc del`が失敗して即死。

ADR 077（tc-init init container）とADR 078（SSM + cgroup直接操作）でそれぞれ回避策を設計したが、どちらもFISの設計意図から外れたハックである。

Chaos Meshを検討する中で、以下の4案を比較した。

**案A：FIS継続（ADR 077・078の回避策で運用）**

tc-initとSSM cgroup操作で実験は動くが、両者ともFISの`aws:eks:*`アクションが本来想定していない実装。「FISの限界を回避した」は技術的理解の証明になるが、本番投入には躊躇う実装が残る。

**案B：Chaos Mesh単独（FIS廃止）**

Chaos MeshはDaemonSetのchaos-daemonが`nsenter`でターゲットコンテナのcgroup namespaceに入るため、memory_stressとnetwork_latencyを構造的に正しく実現できる。ただしFISを外すとIAM統合・CloudWatch stop condition・S3実験レポートなどAWS固有の価値がなくなり、「なぜAWS？」の答えが失われる。

**案C：FISオーケストレート + Chaos Mesh注入（ハイブリッド）**

FIS stop conditionが発火したときにChaos Mesh CRを削除する連携ロジックが必要になる。どちらのツールもこの連携を想定した設計ではなく、chaos-agentにカスタムグルーコードが積み上がる。ベストプラクティスではない。

**案D：FIS（AWSインフラ層）+ Chaos Mesh（K8sコンテナ層）の役割分担**

それぞれのツールが本来得意とする層を担当する。FISはEC2ノード終了などAWSリソースレベルの障害注入とAWS observability統合（CloudWatch stop condition、S3レポート）を担う。Chaos MeshはK8sコンテナ内部の障害注入（正しいcgroup、tc netem自動設定）を担う。2つのツールは独立して動作し、連携しない。

## Decision

**案D（FIS：AWSインフラ層 / Chaos Mesh：K8sコンテナ層）を採用する。**

実験を以下のように再分類する。

| 実験 | ツール | 理由 |
|---|---|---|
| EC2ノード終了（新規） | FIS `aws:ec2:terminate-instances` | AWSリソース操作、FISの本来用途 |
| memory_stress | Chaos Mesh `StressChaos` | nsenterで正しいcgroupを操作 |
| network_latency | Chaos Mesh `NetworkChaos` | tc netemを自動セットアップ |
| cpu_stress | Chaos Mesh `StressChaos` | FISも動くが統一のためChaos Meshへ移行 |
| pod_kill | Chaos Mesh `PodChaos` | FISも動くが統一のためChaos Meshへ移行 |
| http_error_inject | Envoy ConfigMap patch | 変更なし（L7層、どちらのツールも不要） |

ADR 077（tc-init）とADR 078（SSM cgroup injection）は撤回する。

## Rationale

### 案A（FIS継続）を外した理由

ADR 077・078の実装は技術的理解の証明として価値があるが、cgroup v1前提のSSMスクリプトやinit containerへの依存はプロダクション品質として課題が残る。技術的正解を優先する。

### 案B（Chaos Mesh単独）を外した理由

AWS observability統合（CloudWatch stop condition、IAM最小権限、S3実験レポート）はこのポートフォリオがAWSプラットフォームである根拠になっている。FISを完全に廃止すると、EKSをGKEやkindに替えても動く汎用K8sプラットフォームになり、AWS専門性の証明が弱くなる。

### 案C（FISオーケストレート + Chaos Mesh注入）を外した理由

2つのツールを結合させるカスタムグルーコードは保守コストになる。各ツールが独立して動作する設計の方がシンプルで信頼性が高い。

### 案D（層ごとの役割分担）を選んだ理由

- FISが本来得意なAWSリソース操作（EC2ノード終了）を担い、AWS統合の価値を維持する
- Chaos MeshがK8sコンテナ層の障害注入を担い、cgroup分離・qdisc制約を根本から解消する
- 2ツールは独立して動作し、連携ロジックが不要
- 「AWS層とK8s層でツールを使い分ける」設計は実際のエンタープライズ環境でも自然な分離

## Consequences

- chaos-agentはFIS API（EC2ノード終了）とChaos Mesh API（CRD apply/delete）の両方をサポートする必要がある
- Chaos Mesh operatorをEKSクラスターにインストールする（Helm）
- EC2ノード終了実験の合格基準を新たに定義する必要がある（ADR 080予定）
- ADR 077（tc-init）・ADR 078（SSM cgroup injection）のStatusをSupersededに更新する
- ADR 054（FIS as chaos-agent backend）は部分的に撤回：K8sコンテナ層の実験はChaos Meshに移行するが、AWSインフラ層の実験ではFISを継続使用する
