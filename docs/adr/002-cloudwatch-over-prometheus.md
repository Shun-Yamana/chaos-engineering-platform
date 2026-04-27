# ADR 002 - CloudWatch Container Insights over Prometheus

- Status: Accepted
- Date: 2026-04-27

## Context

SLI（エラーレート・レイテンシー）の計測基盤を選定する必要があった。
Kubernetes の標準的な観測基盤として Prometheus + Grafana が広く使われているが、
EKS Fargate 環境での採用に制約がある。

## Decision

**CloudWatch Container Insights** を採用する。

## Rationale

### Prometheus を外した理由

EKS Fargate は DaemonSet をサポートしない。
Prometheus の標準的なデプロイ方式（Node Exporter を DaemonSet で各ノードに配置）が使えないため、
Fargate 環境での Prometheus 運用は以下の追加作業が必要になる：

- Prometheus Server を Deployment として起動（Fargate 上で動かす場合は永続ストレージに EFS が必要）
- Node Exporter の代替として cAdvisor のメトリクスを直接スクレイプする設定
- Prometheus → Grafana の追加デプロイ・管理

これらはカオスエンジニアリングのロジック実証とは無関係な運用コストであり、スコープ外と判断した。

### CloudWatch Container Insights を選んだ理由

- EKS Fargate と完全統合されており、FluentBit サイドカーが自動的にメトリクス・ログを送信する
- 追加インフラのデプロイが不要（IAM ポリシーの付与のみ）
- Lambda から `boto3` で `get_metric_statistics` を呼び出すだけで SLI を計算できる
- AWS のマネージドサービスのためスケーリング・可用性を AWS に委譲できる

## Consequences

- CloudWatch の GetMetricStatistics API には最小解像度の制約（60秒）があるため、
  1分以下の粒度での SLI 計測はできない。本プロジェクトでは 1 分粒度で十分と判断。
- CloudWatch の料金はメトリクス数・API コール数に依存するため、
  大規模運用時はコスト試算が必要。本ポートフォリオの規模では問題なし。
- Prometheus/Grafana の豊富な可視化機能は利用できない。
  ただし本プロジェクトのゴールは「SLO 違反の自動検知と実験停止」であり、
  ダッシュボードの美麗さは優先度が低いと判断した。
