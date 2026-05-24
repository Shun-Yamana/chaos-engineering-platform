# ADR 066 - network_latency アプリ層防御 — 追加実装不要（ADR 064 と同一防衛経路）

- Status: Accepted
- Date: 2026-05-24

## Context

④ network_latency（a→b 遅延注入）・⑨ network_latency（b→c 遅延注入）実験に対して
アプリ層の追加防衛が必要かを検討した。

Envoy delay filter が 250ms の遅延を注入すると：
- per_try_timeout(100ms) が発火するが、`retry_on="connect-failure,reset"` のため timeout は対象外
- リトライは発動しない
- route timeout(200ms) で service-a がエラーを受け取る

## Decision

**network_latency に対するアプリ層の追加実装はしない。**
ADR 064 の `_aggregate_cb` がそのまま適用される。

## Rationale

network_latency の障害モードは timeout であり、cpu_stress（ADR 064）と同一の防衛経路をたどる。

| | cpu_stress | network_latency |
|---|---|---|
| 障害モード | pod が遅い | リクエストが遅い（delay filter）|
| エラー種別 | timeout | timeout |
| `_aggregate_cb` | 5回失敗 → open → stale cache 即返し | 同左 |
| retry | 効かない（retry_on 対象外）| 同左 |

⑨ b→c については `_REVIEW_TIMEOUT_S=0.1s`（service-b のアプリタイムアウト）が
Envoy delay filter より先に発火し、`reviews=null` で 200 を返す。もともと対応済み。

## Consequences

- 実装変更なし
- network_latency 実験は timeout ベースの障害として cpu_stress と同じ枠組みで解釈できる
- retry を timeout に拡張しない判断は ADR 063 で確定済み
