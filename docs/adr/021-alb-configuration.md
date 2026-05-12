# ADR 021 - ALB 設定最適化（可観測性・セキュリティ・ヘルスチェック高速化）

- Status: Accepted
- Date: 2026-05-11

## Context

ALB は AWS Load Balancer Controller (LBC) が `k8s/ingress.yaml` のアノテーションから動的に作成しており、`aws_lb` / `aws_lb_target_group` / `aws_lb_listener` の Terraform リソースは存在しない。設定変更は Ingress アノテーションで行う。

`aws_lb` 全設定項目をレビューした結果、当初の Ingress は最小構成（スキーム・ターゲットタイプ・アクセスログ・ポート・ヘルスチェックパス・CloudFront 認証）のみで、以下の課題があった。

- **可観測性**: 接続レベルのログ（`connection_logs`）が未設定で、カオス実験中の接続断の切り分けができなかった
- **セキュリティ**: 不正 HTTP ヘッダーを素通しする設定（`drop_invalid_header_fields=false`）と、Server ヘッダーによるサーバーフィンガープリンティングが未対処だった
- **障害検知速度**: デフォルトのヘルスチェック（30s 間隔・3回）では Pod Kill 実験後の異常判定に最長 90s かかり、カオス実験の合格基準評価に影響していた
- **回復速度**: デフォルトの登録解除待機（300s）により、Pod Kill 後の旧ターゲットに 5分間リクエストが流れ続けていた

## Decision

`k8s/ingress.yaml` のアノテーションに以下を追加する。

| 分類 | アノテーション / 属性 | 変更内容 |
|---|---|---|
| ALB 属性 | `connection_logs.s3.*` | 接続ログを S3 に有効化 |
| ALB 属性 | `routing.http.drop_invalid_header_fields.enabled` | `true`（不正ヘッダー除去） |
| リスナー属性 | `routing.http.response.server.enabled` | `false`（Server ヘッダー非表示） |
| TG 属性 | `deregistration_delay.timeout_seconds` | 300 → 30 |
| ヘルスチェック | `interval` / `unhealthy_threshold` | 30s/3回 → 5s/2回 |

## Rationale

### HTTPS 化・mTLS・WAF を外した理由

ALB の前段に CloudFront が存在し、TLS 終端・WAF・セキュリティヘッダー（HSTS・CSP・X-Frame-Options）は CloudFront 側で対応する。ALB へのダイレクトアクセスは `X-Origin-Verify` ヘッダー認証で制御済みのため、ALB でこれらを二重に設定する必要はない。

### `enable_deletion_protection` を外した理由

LBC が ALB のライフサイクルを管理するため、削除保護を有効にすると LBC が ALB を再作成できなくなる。

### `health_check_logs` を外した理由

`connection_logs` に比べてデバッグ価値が低く、ヘルスチェックの成否は CloudWatch メトリクス（`HealthyHostCount`）で十分追跡できる。ログコストとのトレードオフで不採用。

### ヘルスチェック高速化（5s/2回）を選んだ理由

ADR 005〜009 の合格基準は「実験終了から 60s 以内に SLO 回復」を前提とする。デフォルト（30s×3回=最長 90s）では基準評価前に回復が始まってしまい、実験の精度が下がる。5s×2回=10s の異常判定で合格基準評価のタイムラグを排除する。

### `deregistration_delay` 短縮（30s）を選んだ理由

Pod Kill 実験では新 Pod が EKS の再スケジュール（〜30s）で立ち上がる。旧ターゲットへの待機が 300s では実験の「回復フェーズ」が計測できない。30s に揃えることで、新 Pod の立ち上がりと登録解除が並行して完了する。

## Consequences

- ヘルスチェック間隔を 5s にすると ALB から service-b への `/health` リクエストが増加する（1インスタンスあたり約 720 req/h）。`/health` エンドポイントは軽量実装であることを確認済み
- `deregistration_delay=30s` はデプロイ時のローリングアップデートにも適用される。インフライトリクエストが 30s 以内に完了しない場合は接続が切断されるため、長時間リクエストを扱う場合は再検討が必要
- `connection_logs` は S3 ストレージコストが増加する（アクセスログより詳細）。既存の 30日ライフサイクルポリシーが適用される
- `listener-attributes` アノテーションは LBC v2.5+（Helm chart v1.5+）以降が必要。現在 chart v1.11.0 を使用しており要件を満たしている
