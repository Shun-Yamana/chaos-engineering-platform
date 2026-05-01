# ADR 010 - カオス実験注入方式：スポット注入方式の採用

- Status: Accepted
- Date: 2026-05-01

## Context

cpu_stress・memory_stress 実験の注入方式として、当初 Deployment spec に stress-ng sidecar を追加する方式を検討した。しかし以下の連鎖反応が設計上の問題となった。

```
Deployment spec 変更
  → ローリングアップデート（全 Pod 再作成）
  → 全 Pod が stress-ng sidecar 付きで起動
  → CPU/メモリ高負荷が全 Pod に広がる
  → HPA がスケールアウト
  → スケールアウトした新 Pod にも sidecar がつく
  → 高負荷が解消されない → HPA が無限スケールアウト
```

この連鎖は「特定 Pod への負荷集中」という現実の障害とは乖離しており、HPA の合格基準（不要なスケールアウトをしないこと）を原理的に達成不可能にする。

## Decision

カオス実験の注入方式として、**Deployment spec 変更方式ではなくスポット注入方式を採用する**。対象 Pod に直接 `stress-ng` を起動し、Deployment spec を一切変更しない。

## Rationale

### Deployment spec 変更方式を外した理由

- ローリングアップデートが発生し、実験意図と無関係な Pod 再作成が起きる
- HPA スケールアウトした新 Pod も sidecar を持つため HPA が無効化される
- エラーレート上昇時に「負荷が原因」か「Pod 切り替えが原因」か判別できなくなる
- 全 Pod に影響が広がるため爆発半径を制御できない
- 現実の障害（特定 Pod への負荷集中）を再現できない

### スポット注入方式を選んだ理由

- Deployment spec を変更しないためローリングアップデートが発生しない
- HPA でスケールアウトした新 Pod は stress-ng を持たず、正常に負荷を分散できる
- 実験の爆発半径を特定 Pod に限定できる
- 現実の障害を忠実に再現できる（現実でも障害は全 Pod に同時には起きない）

## Consequences

- `cpu_stress_inject`・`memory_stress_inject` はスポット注入方式で実装する
- 実験終了時のクリーンアップは Pod 内プロセスの終了のみで済む（Deployment spec の復元が不要）
- 対象 Pod の選択ロジック（ランダム1台 or 指定）を実装に含める必要がある
