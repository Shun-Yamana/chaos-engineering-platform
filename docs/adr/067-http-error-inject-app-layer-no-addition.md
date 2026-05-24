# ADR 067 - http_error_inject アプリ層防御 — 追加実装不要（CB が高速 open）

- Status: Accepted
- Date: 2026-05-24

## Context

⑤ http_error_inject（a→b に HTTP 500 注入）・⑩ http_error_inject（b→c に HTTP 500 注入）実験に対して
アプリ層の追加防衛が必要かを検討した。

Envoy abort filter は upstream にリクエストを転送せず即座に 500 を返す。
timeout 系の障害（cpu_stress・network_latency）と異なり、エラーが ~数ms で返ってくる。

## Decision

**http_error_inject に対するアプリ層の追加実装はしない。**
`_aggregate_cb`（ADR 064）と `reviews=null` fallback（ADR 062）で対応する。

## Rationale

### ⑤ a→b: CB が timeout 系より高速に open する

abort filter の 500 は即座に返るため、`_aggregate_cb` が 5 回失敗に達するまでの時間が
timeout 系障害（~1s）に対して ~50ms と大幅に短い。

| 障害種別 | 1失敗あたり | CB open まで（5回）|
|---|---|---|
| cpu_stress / network_latency | ~200ms（timeout 待ち）| ~1s |
| http_error_inject | ~数ms（即返し） | ~50ms |

CB open 後は stale cache を即返すため、JMeter から見た SLO 違反区間が短くなる。

### ⑤ で retry を追加しない理由

`retry_on="connect-failure,reset"` に 5xx を含めていないのは ADR 063 の意図的な設計。
abort filter が返す 500 をリトライすると、同じ Envoy が同じ 500 を返すだけで意味がない。
また http_error_inject 実験の観測対象は「5xx が service-a に伝播するかどうか」であり、
リトライで隠蔽すると実験の意図が失われる（ADR 062 参照）。

### ⑩ b→c: reviews=null fallback で対応済み

service-b の `_fetch_reviews()` はあらゆる例外を catch して `None` を返す（ADR 062）。
abort filter が返す 500 も同様に catch されるため、追加実装は不要。

## Consequences

- 実装変更なし
- ⑤ は SLO 違反するが、CB の高速 open により timeout 系より違反区間が短い
- ⑩ は SLO を維持する（graceful degradation の実証）
- アプリ層防御は全 10 実験で ADR 063〜067 により完結した
