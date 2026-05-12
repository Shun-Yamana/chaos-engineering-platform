# ADR 001 - EKS Fargate の採用

- Status: Accepted
- Date: 2026-04-27

## Context

カオスエンジニアリング基盤のターゲットとなるマイクロサービス実行環境を選定する必要があった。
候補は以下の3つ：

1. **EKS + EC2 ノードグループ**
2. **EKS Fargate**
3. **ECS Fargate**

## Decision

**EKS Fargate** を採用する。

## Rationale

### ECS Fargate を外した理由
Pod kill の実装に `kubernetes-client` を使用するため、Kubernetes API が必須。
ECS は独自の Task/Service モデルであり、`kubectl delete pod` に相当する操作が ECS API 経由となり、
Chaos Agent のロジックが複雑化する。業界標準の Kubernetes エコシステムを使う EKS が適切。

### EKS + EC2 ノードグループを外した理由
EC2 ノードはノードのパッチ管理・スケーリング設定・AMI 更新などの運用負担が発生する。
本プロジェクトはカオスエンジニアリングのロジック実証が目的であり、ノード管理はスコープ外。
Fargate であれば Pod 単位でコンピューティングが割り当てられ、ノード管理が不要。

### EKS Fargate を選んだ理由
- Kubernetes API をそのまま使えるため `kubectl delete pod` で Pod kill を実装できる
- ノード管理が不要でインフラのノイズを最小化できる
- 1 Pod = 1 Fargate タスクのため、Pod kill の影響範囲が明確で観測しやすい
- CloudWatch Container Insights との統合が公式サポートされている

## Consequences

- Fargate は DaemonSet をサポートしないため、Prometheus の Node Exporter は使用不可
  → CloudWatch Container Insights で代替（ADR 002 参照）
- Fargate Pod の起動は EC2 より遅い（約 30〜60 秒）ため、Pod kill 後の復旧観測ウィンドウを考慮した実験設計が必要
