# ADR 095 - 防衛発火証拠の評価出力追加（2軸評価フレームワーク）

- Status: Accepted
- Date: 2026-05-31

## Context

これまでの評価ロジックは「SLO を維持したか（合否）」にのみ注目していた。
合否判定は「障害に耐えた」事実を示すが、「**どの防衛機構が発火して耐えたか**」は
数値として残っていなかった。

カオス実験の本来の目的を整理すると 2 軸ある：

1. **防衛設計軸** — 防衛機構（CB / stale cache / fallback / OOMKill 検知）が設計通りに発火したことをメトリクスとして証明する
2. **可視化軸** — 実験中にどのサービスがやられていてどのサービスが生きているかを X-Ray で追う

合否判定は防衛設計軸の「SLO 維持」しか見ておらず、「発火した証拠」が欠落していた。
可視化軸は X-Ray サービスマップが担うため evaluator の変更対象ではない。

また、「防衛が発火しなかった＝失敗」ではないケースがある：

- cpu_stress: HPA が先に防衛した場合、アプリ層 CB は開かないのが正解
- node_failure / az_isolation: インフラ層（PDB / ALB AZ failover）が主防衛のため
  アプリ層 CB 発火は副次的

## Decision

**全実験の評価出力に `defense_evidence` セクションを追加する。**

`defense_evidence` は `overall_pass` の判定に含めない（informational）。
ただし以下の 2 ケースは例外として pass 条件に組み込む：

- **network_latency**: `StaleCacheHitCount >= 1` を Phase A 必須条件に追加する。
  500ms 注入 + 200ms Envoy timeout の設計上、stale cache 発火は決定論的であり
  「発火しなかった＝timeout が機能していない」ことを意味するため。

- **http_error_inject**: ADR 094 に従い `FallbackCount + StaleCacheHitCount >= 1` を
  Phase A 必須条件とする。これは「service-a が service-b 障害を吸収した」証拠であり
  実験の主目的そのもの。

## 実験ごとの defense_evidence

| 実験 | defense_evidence 項目 | pass 条件に含むか |
|------|----------------------|-----------------|
| pod_kill | stale_cache_hit_count, fallback_count, circuit_breaker_opened | ➖ informational |
| cpu_stress | circuit_breaker_opened, stale_cache_hit_count | ➖ informational（HPA 防衛の場合は不発火が正解） |
| memory_stress | stale_cache_hit_count | ➖ informational（oomkill は Phase A 必須のまま） |
| network_latency | stale_cache_hit_count, circuit_breaker_opened | ✅ stale_cache_fired >= 1 を Phase A に追加 |
| http_error_inject | fallback_count, stale_cache_hit_count, circuit_breaker_opened | ✅ defense_fired >= 1 を Phase A に追加 |
| node_failure | container_restarts_delta | ➖ informational |
| az_isolation | （なし） | ➖ 可視化軸に委ねる |

## http_error_inject の評価ロジック変更（ADR 094 実装）

ADR 094 の方針（HTTPChaos → service-b 内蔵 FAULT_RATE）に対応して評価指標を変更する。

| | 旧（HTTPChaos / ReviewsUnavailableCount） | 新（FAULT_RATE / FallbackCount） |
|--|--|--|
| Phase A | ReviewsUnavailableCount >= 1, cascade_error_rate <= 0.05 | defense_fired (Fallback + StaleCache) >= 1 |
| Phase B | ReviewsUnavailableCount <= 0 after fault | defense_deactivated <= 0 after 120s offset |

Phase B の 120s offset は rolling restart (~90s) + CB half-open (30s) の合計を考慮した値。

## Consequences

- evaluation_details に defense_evidence キーが追加される（後方互換、既存フィールドは変更なし）
- Slack レポートに "Defense Evidence" セクションが追加される
- network_latency の pass 条件が 1 つ増える（stale_cache_fired >= 1）
- http_error_inject の評価ロジックが ReviewsUnavailableCount から FallbackCount ベースに変わる
- chaos/agent.py から http_error_inject の Chaos Mesh 経路が除去される（ADR 094）
