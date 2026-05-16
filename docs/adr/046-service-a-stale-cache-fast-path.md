# ADR 046 - service-a aggregate ステールキャッシュをファストパスに変更

- Status: Accepted
- Date: 2026-05-16

## Context

network_latency 実験（service-b に LATENCY_MS=500 を注入）で evaluator が FAIL を返していた。

| Phase | 基準 | 実測値 | 結果 |
|---|---|---|---|
| A: service-b p95 | >= 450ms | 996ms | PASS |
| A: service-a p95 | <= 250ms | 837ms | **FAIL** |
| B: service-a recovery | <= 50ms | 814ms | **FAIL** |

### 原因

`aggregate/{item_id}` エンドポイントのステールキャッシュがリトライループ**後**のフォールバックとして実装されていた。

```
for attempt in range(3):          # 3回 × Envoy timeout 200ms ≈ 750ms 消費
    Envoy → service-b (500ms) → 504 → retry
# 全リトライ失敗後にキャッシュを確認・返却
```

キャッシュが warm であっても 750ms かかってからキャッシュを返すため、
service-a の p95 が service-b の遅延 × リトライ数 に比例して上昇していた。

また `_STALE_CACHE_TTL_S = 30.0` とトラフィック間隔 30s が一致しており、
呼ばれる瞬間にキャッシュがギリギリ失効している可能性があった。

### 設計前提との乖離

実験の合格基準（ADR 009・030）は「Envoy の timeout + stale cache によって
service-a が service-b の遅延を吸収し、p95 <= 250ms を維持する」ことを前提としている。
キャッシュが後回しになっている実装ではこの前提が成立しない。

## Decision

1. **「live 1回 → 失敗したら stale キャッシュ（TTL なし）」** パターンに変更する
2. `_STALE_CACHE_TTL_S` を 30s から 60s に延長する（cold start 境界条件の緩和）

### なぜ「先にキャッシュ確認 + TTL」では足りなかったか

第一案（キャッシュ先行 + TTL=60s）を実装したが効果がなかった。
理由: TTL=60s でも実験期間（5分）の途中でキャッシュが失効し、
その後はリトライループ（3回 × 200ms = 750ms）に戻ってしまった。
p95 が実験後半で再び 800ms まで上昇することが確認された。

## Rationale

### live 1回 → stale フォールバックにした理由

- **通常時**: live call が成功するのでキャッシュは常に新鮮に更新される
- **障害中**: Envoy の 200ms timeout で live call が失敗 → 即座に stale cache 返却（合計 ≈ 200ms）
- **TTL を外した理由**: fault は何分続くかわからない。TTL で切ると障害が長引くほど 750ms リトライに戻る。キャッシュが「古い」より「遅い・エラー」の方が UX が悪い。キャッシュの更新は live call 成功時にのみ行われるため、障害回復後の最初の成功で自動的に鮮度が戻る。
- **リトライを 3 回から 1 回に減らした理由**: TTL なしの stale cache が常にフォールバックとして機能するため、リトライで時間を使う意味がない。失敗を速やかに認識して stale を返す方が p95 を下げられる。

## Consequences

- **Phase A**: live 失敗（200ms）→ stale cache 即返却 → service-a p95 ≈ 200ms < 250ms ✓
- **Phase B**: 障害終了後の最初の live call 成功 → キャッシュ更新 → 以降の p95 が通常値に戻る ✓
- 通常時は常に live call を試みるため、キャッシュが stale になることはない
- キャッシュ cold（初回起動直後）で live も失敗した場合のみ 502 になる
