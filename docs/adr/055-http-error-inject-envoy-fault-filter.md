# ADR 055 - http_error_inject の Envoy fault filter 移行

- Status: Accepted
- Date: 2026-05-22

## Context

FIS 統一化（ADR 054）で4障害タイプを FIS に移行したが、HTTP エラー注入には FIS のネイティブアクションが存在しない。

従来の実装は `FAULT_RATE` 環境変数で service-b の Pod を再起動し、アプリコード内の乱数判定で 500 エラーを返していた。この方式の問題は:

- **アプリへの混入**: service-b の本番コードに `FAULT_RATE` を参照するロジックが必要
- **Pod 再起動コスト**: env var 変更ごとにローリングアップデートが走る
- **注入レイヤーの不透明さ**: アプリレベルの乱数なのでネットワークミドルウェアを経由しない

service-a の Pod にはすでに Envoy サイドカー（`envoy-service-b-egress` ConfigMap）が存在し、service-b へのリクエストを仲介している。Envoy の HTTP fault filter を使えば、アプリコードを変更せずに HTTP 500 を注入できる。

## Decision

`http_error_inject` の障害注入を `FAULT_RATE` env var から Envoy HTTP fault filter（ConfigMap patch + service-a rollout restart）に移行する。

## Rationale

### FAULT_RATE env var 継続を外した理由

- FIS 統一化を進める中で、アプリコードに障害ロジックを残すことは設計の一貫性を損なう
- service-b のコードに `FAULT_RATE` を参照するロジックが存在し続けると、将来的に誤って本番動作に影響するリスクがある
- Pod 再起動（ローリングアップデート）で注入開始まで 30〜60 秒かかる

### Envoy fault filter を選んだ理由

- **アプリ非依存**: service-a / service-b のコードに変更不要。プロキシレイヤーで注入するため責任分離が明確
- **即時性**: ConfigMap パッチ後に service-a のみ rollout restart。service-b は再起動しない
- **設定の可視性**: `numerator: 0`（無効）/ `numerator: 50`（50% エラー）が ConfigMap として git 管理される
- **既存インフラの活用**: Envoy サイドカーはすでに service-a に存在しており、追加インフラが不要

### OpenTelemetry fault injection を外した理由

OTel の fault injection は OpenTelemetry Collector の設定で行うが、現時点で OTel Collector は未導入。追加インフラコストが大きい。

## Consequences

- ✅ service-b のアプリコードから `FAULT_RATE` 参照ロジックを削除できる
- ✅ HTTP 障害注入がネットワークプロキシレイヤーに移動し、設計が明確になる
- ✅ ConfigMap が git 管理されるため、有効/無効の状態が常に可視化される
- ⚠️ service-a の rollout restart に約 30 秒かかる。この待機を `http_error_inject()` 内で吸収しているが、実験の実効注入時間が 35 秒短くなる。600 秒実験では許容範囲（ADR 049 の Phase B offset 180s 内に収まる）
- ⚠️ `_emergency_recover` は scale-to-0 後に `http_error_remove`（ConfigMap リセット + rollout restart）を呼ぶため、回復完了まで約 65 秒かかる（30s scale=0 + 35s rollout）
- ⚠️ chaos-agent ClusterRole に `configmaps: [get, patch, update]` の追加が必要（k8s/chaos-agent.yaml 対応済み）
