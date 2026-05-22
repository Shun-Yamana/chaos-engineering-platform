# ADR 053 - kube-prometheus-stack による Prometheus + Grafana 導入

- Status: Accepted
- Date: 2026-05-22

## Context

EC2 Managed Node Group への移行（ADR 051）により DaemonSet が全ノードに配置可能になり、Node Exporter や kube-state-metrics など Kubernetes ネイティブなメトリクス収集が可能になった。


1. Prometheus による Pod/Node メトリクス収集
2. Grafana による統合ダッシュボード（CloudWatch + X-Ray + Prometheus を一画面に集約）
3. DynamoDB Streams → Grafana Annotation API により実験タイムラインをダッシュボードに自動反映

## Decision

`kube-prometheus-stack` Helm Chart を `monitoring` namespace にデプロイし、Grafana の ServiceAccount に IRSA を付与して CloudWatch・X-Ray への読み取り権限を与える。実験注釈は新規 Lambda（grafana_annotator）が DynamoDB Streams を消費して Grafana Annotation API に POST する。

## Rationale

### Prometheus Operator を個別デプロイしなかった理由

- kube-prometheus-stack は Prometheus Operator + Alertmanager + Node Exporter + Grafana + kube-state-metrics を一括管理するため、個別インストールより運用コストが低い
- CRD（ServiceMonitor / PrometheusRule）による宣言的な監視設定が将来のサービス追加に対応しやすい

### Grafana に IRSA を付与した理由

- Grafana Pod が CloudWatch および X-Ray API を直接呼び出せることで、EC2/Lambda/EKS の全メトリクスをシングルペインで表示できる
- IRSA により Pod 単位で最小権限を付与でき、Node IAM Role への過剰権限付与を避けられる
- IRSA なしの代替（static credentials）はシークレットローテーションのリスクがある

### grafana_annotator Lambda を採用した理由

- chaos-agent は実験中に Kubernetes API と FIS API を操作しており、Grafana HTTP 呼び出しを同期的に追加するとエラーパスが増える
- DynamoDB Streams をトリガーとする非同期 Lambda にすることで chaos-agent と疎結合になり、Grafana 側の障害が実験制御に影響しない
- 既存の experiment_evaluator と同一の Streams を消費するためインフラ追加なし（DynamoDB stream_enabled は ADR 030 時点で有効化済み）

## Consequences

- ✅ Prometheus によるノードリソース・コンテナメトリクスが収集され、Grafana で可視化できる
- ✅ Grafana ダッシュボード（chaos-experiment.json）で CloudWatch / X-Ray / Prometheus を統合表示
- ✅ 実験開始・終了がダッシュボードに自動的に縦線注釈として表示され、メトリクス変化との相関が一目でわかる
- ⚠️ `grafana_url` は VPC 内部 URL（`http://grafana.monitoring.svc.cluster.local:3000`）のため、grafana_annotator Lambda は EKS と同じ VPC 内に配置する必要がある（現構成では Lambda VPC 設定未定。ポートフォリオ範囲では NLB 経由 URL を `grafana_url` に設定することで回避可能）
- ⚠️ Grafana の `adminPassword` は空文字で定義しており、デプロイ時に `--set grafana.adminPassword=<secret>` で注入する運用
- ⚠️ `prometheus-stack-values.yaml` の `${grafana_irsa_role_arn}` プレースホルダーは `terraform output grafana_irsa_role_arn` の値で手動または CI で置換する
