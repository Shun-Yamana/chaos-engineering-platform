# ADR 016 - DynamoDB テーブル設計

- Status: Accepted
- Date: 2026-05-06

## Context

Chaos Engineering Platform の DynamoDB テーブル（slo_definitions・experiment_history・sli_metrics）の設定項目を精査し、適用すべき項目を決定する。

現状の問題が 3 つある：

1. **auto_stopper のフルスキャン**: `get_running_experiments` が `table.scan()` で全アイテムを読んだあと FilterExpression で絞っている。実験数が増えると読み取りコストが線形に増加する。
2. **削除保護なし**: `terraform destroy` 一発で SLO 定義・実験履歴が消える。
3. **PITR・テーブルクラス未設定**: SLO しきい値の誤変更を復元できない。実験履歴のアクセスパターンに対してコスト最適化されていない。

## Decision

以下の 4 項目を適用し、その他はスキップする。

### 適用する設定

| 設定 | 対象テーブル | 値 |
|------|------------|-----|
| `deletion_protection_enabled` | 全テーブル | `true` |
| `point_in_time_recovery` | slo_definitions・experiment_history | `enabled = true` |
| GSI `status-target-service-index` | experiment_history | hash=status / range=target_service / ALL |
| `table_class` | experiment_history | `STANDARD_INFREQUENT_ACCESS` |

### 適用しない設定

| 設定 | 外した理由 |
|------|-----------|
| KMS カスタムキー暗号化 | AWS 管理キーで十分。テーブルに機密データなし |
| DynamoDB Streams | Streams のコンシューマー（Lambda 等）を持たない |
| Global Tables（レプリカ） | シングルリージョン構成。DR 要件なし |
| Auto Scaling | 全テーブル PAY_PER_REQUEST のため不要 |
| LSI | テーブル作成後に追加不可。現状のアクセスパターンで不要 |
| sli_metrics の PITR | TTL 7 日かつ CloudWatch から再計算可能。復元価値なし |

## Rationale

### GSI の設計理由

auto_stopper のアクセスパターンは「target_service が X かつ status が running の実験を全件取得」。現状は Scan + FilterExpression で実装されており O(テーブルサイズ)。

GSI（hash_key=status, range_key=target_service）を切ることで、`Query(status=running AND target_service=service-b)` に変換できる。running な実験は常に少数（通常 1 件）なので読み取り効率が大幅に改善する。

hash_key を target_service ではなく status にした理由：status の値は "running" / "stopped" / "pending" の 3 値しかなくカーディナリティが低いが、auto_stopper が最初に status=running で絞る方が取得件数が少ない。

### table_class に STANDARD_INFREQUENT_ACCESS を選んだ理由

experiment_history の読み取りは実験完了後の事後調査のみで頻度が低い。IA クラスはストレージ単価が約 60% 安い代わりに読み取り単価が高いが、このアクセスパターンでは IA が有利。slo_definitions（毎分参照）と sli_metrics（毎分書き込み）は頻繁にアクセスするため STANDARD のまま。

### PITR の対象を絞った理由

slo_definitions と experiment_history は人手で変更する設定・監査証跡であり、誤操作時の復元価値が高い。sli_metrics は Lambda が毎分自動生成するデータで TTL 7 日後に消えるため、復元しても意味がない。

## Consequences

- experiment_history に GSI `status-target-service-index` を追加する ✅
- `auto_stopper.py` の `get_running_experiments` を Scan → GSI Query に変更する ✅
- 全テーブルに `deletion_protection_enabled = true` を追加する ✅
- slo_definitions・experiment_history に `point_in_time_recovery` を追加する ✅
- experiment_history の `table_class` を `STANDARD_INFREQUENT_ACCESS` に変更する ✅
- GSI 追加により experiment_history の `status` と `target_service` を `attribute` ブロックで定義する必要がある
