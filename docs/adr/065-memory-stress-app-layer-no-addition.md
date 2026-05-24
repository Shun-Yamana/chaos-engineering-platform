# ADR 065 - memory_stress アプリ層防御 — 追加実装不要（ADR 063・064 の組み合わせで対応）

- Status: Accepted
- Date: 2026-05-24

## Context

③ memory_stress（service-b メモリ枯渇）・⑧ memory_stress（service-c メモリ枯渇）実験に対して
アプリ層の追加防衛が必要かを検討した。

ADR 060 で `memoryPercentage=95` に設定済みであり、FIS がメモリを約 243MB（limit 256MB の 95%）まで
消費させることで OOMKill が数秒以内に発動する。「遅い Pod が長時間粘る」フェーズはほぼ消えた設計になっている。

## Decision

**memory_stress に対するアプリ層の追加実装はしない。**
ADR 063（Envoy retry）と ADR 064（_aggregate_cb）の組み合わせで対応する。

## Rationale

memory_stress の障害は2フェーズに分解できる。

```
FIS memoryPercentage=95%
  ↓ 数秒（メモリ上昇中・GC 圧力で若干遅延）
  ↓ OOMKill 発動
  ↓ pod_kill 相当（connect-failure → 再起動 ~20s）
```

| フェーズ | 継続時間 | 有効な防衛 | 根拠 ADR |
|---|---|---|---|
| メモリ上昇中（遅い） | 数秒 | `_aggregate_cb` 5回失敗 → stale cache 即返し | ADR 064 |
| OOMKill 以降 | ~20s | Envoy retry `connect-failure,reset` → healthy pod | ADR 063 |
| ⑧ service-c | どちらも | `reviews=null` fallback | もともと実装済み |

ADR 060 の「早く死んで早く戻る」設計判断が、アプリ層の防衛実装コストを下げる効果をもたらした。
cpu_stress（ADR 064）は「粘る Pod」への対策であり、memory_stress でもメモリ上昇中の数秒に適用される。
その後の OOMKill は pod_kill と同一の障害モードであり ADR 063 が対応する。

## Consequences

- 実装変更なし
- memory_stress 実験は pod_kill と cpu_stress の複合として扱えるため、
  実験結果の解釈も ADR 058・063・064 の枠組みで統一できる
