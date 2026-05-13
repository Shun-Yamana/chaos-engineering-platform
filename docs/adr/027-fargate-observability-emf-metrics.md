# ADR 027 - Fargate 上の cpu_stress/memory_stress 可観測性設計（EMF + Fluent Bit）

- Status: Accepted
- Date: 2026-05-13

## Context

cpu_stress・memory_stress 実験を CloudWatch アラームで検知したかった。
当初は Container Insights の `pod_cpu_utilization` / `pod_memory_utilization` メトリクスを使う想定だったが、EKS Fargate では DaemonSet が動作しないため cloudwatch-agent DaemonSet が 0/0 となりメトリクスが届かなかった。

また、Fargate の制約上エフェメラルコンテナも使えないため、stress-ng をサイドカーとして後付けする方式も断念済みだった（ADR 026 参照）。

加えて、cpu_stress の実装自体も Fargate の制約から、FIS の `aws:eks:pod-network-latency` ではなく Deployment 環境変数（`CPU_STRESS=true` / `MEMORY_STRESS_MB=N`）でアプリ内ビジーループ・bytearray 確保する方式に変更済みであり、リソース使用率の変化はプロセスレベルでしか観測できない状況だった。

## Decision

service-b にプロセスレベルのメトリクス計測（psutil）と EMF 出力を追加し、Fargate Fluent Bit ログルーティング経由で CloudWatch Logs に送信。CloudWatch Logs が EMF を自動抽出してカスタムメトリクスを生成し、そのメトリクスに対してアラームを設定する。

## Rationale

### Container Insights DaemonSet を外した理由

EKS Fargate は EC2 ノードを持たないため DaemonSet Pod がスケジュールされない。`cloudwatch-agent` DaemonSet の DESIRED が 0 になっており、メトリクスが一切 CloudWatch に届かない。設定変更では解決できない構造的制約。

### chaos-agent からの「実験中フラグ」メトリクスを外した理由

chaos-agent が `ExperimentActive=1` を送信する方式では「実験が動いているか」しか分からず、「CPU/メモリが実際に上がっているか」を検知できない。可観測性の目的（実際のリソース影響を検知）を満たさない。

### ADOT サイドカー自動注入を外した理由

`amazon-cloudwatch-observability` の controller-manager は動作しているが、Fargate Pod へのサイドカー自動注入を有効にするには名前空間への OpenTelemetry Instrumentation CRD の設定が必要で、さらに OTLP エクスポート先の cloudwatch-agent サービスが Fargate 上で動かないため接続エラーが発生し続ける（実際に init container が注入されて OTEL の接続エラーログが出た）。

### Fargate Fluent Bit ログルーティング + EMF を選んだ理由

- Fargate は `aws-observability` 名前空間に `aws-logging` ConfigMap を置くだけで、Pod 再起動後から全 Pod の stdout/stderr を Fluent Bit で転送し始める
- Fargate pod execution role にはすでに CloudWatch Logs 権限（`logs:PutLogEvents` 等）と `cloudwatch:PutMetricData` が付与されていた
- EMF（Embedded Metrics Format）は stdout に JSON を書くだけで SDK 不要。CloudWatch Logs が `_aws.CloudWatchMetrics` キーを自動検出してメトリクスを抽出する
- service-b に IRSA を追加せずとも、Fluent Bit がログを CloudWatch Logs に送るだけでよい
- 副次効果として全サービスのアプリログが CloudWatch Logs に集約され、デバッグ性が向上する

**Fluent Bit CRI パーサーの設定が必要だった点:**
Fargate Fluent Bit はコンテナ stdout を CRI 形式（`timestamp stream flags message`）のまま `log` フィールドに格納して送信する。この状態では EMF の `_aws` キーが文字列の中に埋まり CloudWatch が抽出できない。`parsers.conf` に正規表現パーサーを追加して `log_key message` で純粋な JSON だけを CloudWatch Logs に送信することで解決した。

## Consequences

- **メトリクス遅延:** EMF 抽出は CloudWatch Logs 経由のため、実験開始からアラーム発火まで 2〜4 分かかる（ALB メトリクスベースのアラームより遅い）
- **閾値設定の根拠:** CPU 30%（ビジーループが Fargate vCPU の ~50% を消費することを実測で確認）、メモリ 300MB（ベース ~100MB + bytearray 256MB ページタッチ済みで ~330MB を実測）
- **bytearray のページタッチが必須:** Linux の遅延割り当てにより `bytearray(n)` だけでは RSS に反映されない。`_memory_buffer[i] = 1` でページタッチしてはじめて物理メモリに確保される
- **全 Pod のログが集約される副作用:** `aws-logging` ConfigMap は Fargate プロファイルが適用されている全名前空間（default, chaos）の Pod に影響する
- **OTEL init container の警告ログ:** `amazon-cloudwatch-observability` addon が Python 自動計装 init container を注入し、cloudwatch-agent への接続エラーを出し続ける。機能には影響しないが、ログノイズとなる（今後 `instrumentation.opentelemetry.io/inject-python: "false"` アノテーションで無効化を検討）
