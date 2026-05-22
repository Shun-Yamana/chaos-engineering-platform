# ADR 052 - X-Ray SDK による分散トレーシング実装方針

- Status: Accepted
- Date: 2026-05-22

## Context

EC2 Managed Node Group への移行（ADR 051）により X-Ray DaemonSet が全ノードに配置可能になった。Lambda 側はすでに `tracing_config.mode = Active` で X-Ray が有効になっており（lambda.tf）、EKS Pod 側（service-a / service-b）にもトレーシングを追加してサービスマップを完成させたい。

追加したい機能は2つ:

1. **サービスマップの可視化**: service-a → Envoy → service-b のリクエストチェーンを X-Ray コンソールで確認できること
2. **実験 ID によるトレース絞り込み**: どの実験中に発生したトレースかを X-Ray アノテーションとして埋め込み、フィルタできること

同時に、CloudWatch EMF メトリクスにも `ExperimentId` ディメンションを追加し、実験単位のメトリクス集計を可能にする。

## Decision

`patch_all()` + `XRayMiddleware` を採用し、`experiment_id` を X-Ray アノテーションとして各リクエストに埋め込む。`EXPERIMENT_ID` 環境変数は chaos-agent が実験開始前に Deployment に patch し、終了後に削除する。

## Rationale

### OpenTelemetry SDK を外した理由

- OTel Collector を別途デプロイする必要があり、Day 2 スプリント内での実装コストが高い
- Lambda 側がすでに X-Ray バックエンドを使っているため、OTel に統一するには Lambda 側の変更も伴う
- X-Ray SDK の方が AWS サービスマップとの親和性が高く、追加インフラなしで動く

### X-Ray SDK (patch_all + XRayMiddleware) を選んだ理由

- Lambda 側と同じ X-Ray バックエンドを使うことで、Lambda ↔ EKS Pod の統一されたサービスマップが得られる
- `XRayMiddleware` は FastAPI (ASGI) に対応しており、既存コードへの侵入が最小限
- `patch_all()` で boto3/botocore を自動計装でき、将来 DynamoDB などの呼び出しもトレースに含められる
- X-Ray DaemonSet（ADR 051）が `xray-service:2000` でリッスンしており、SDK の送信先として即座に使える

### experiment_id をアノテーションで埋め込む理由

- X-Ray フィルタ式（`annotation.experiment_id = "xxx"`）で実験単位のトレース一覧が取得できる
- stale cache フォールバック発動のトレースを実験 ID で絞り込み、レジリエンス動作の検証が容易になる
- chaos-agent が Deployment env var として `EXPERIMENT_ID` を管理することで、SDK 側の変更なしに実験ごとに値が切り替わる

## Consequences

- ✅ X-Ray コンソールでサービスマップに service-a / Envoy / service-b が表示される
- ✅ 実験中のトレースを `experiment_id` アノテーションで絞り込める
- ✅ EMF の `ExperimentId` ディメンションにより CloudWatch で実験単位のメトリクス集計が可能
- ⚠️ `httpx` は `patch_all()` の自動計装対象外のため、service-a → service-b のアウトバウンド呼び出しは自動的にサブセグメントにならない。サービスマップの接続線は service-b 側の `XRayMiddleware` が受信ヘッダーを解釈することで補完されるが、手動でのトレースヘッダー注入は未実装
- ⚠️ `EXPERIMENT_ID` patch は Pod の rolling restart を伴う。FIS 実験開始より前に patch するため実験の実効注入時間に影響しないが、X-Ray アノテーションは新 Pod が起動してから有効になる（rolling restart 完了前のリクエストはアノテーションなし）
- ⚠️ chaos-agent の ClusterRole に `deployments: [get, patch]` が必要（実験 ID patch のため。既存権限で対応済み）
