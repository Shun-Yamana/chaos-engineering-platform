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

### network_latency を ephemeral container 方式で扱えない理由

スポット注入方式の延長として `network_latency` も ephemeral container で実装することを検討したが、EKS Fargate の制約により断念した。

`tc netem` による遅延注入には NET_ADMIN Linux Capability が必要だが、EKS Fargate は Pod レベルで NET_ADMIN を封じている。sidecar・exec・ephemeral container のいずれの方式でも回避できない。

`network_latency` は AWS FIS（`aws:eks:pod-network-latency`）に委譲する（詳細は ADR 009 参照）。

**実験別の注入方式まとめ：**

| 実験 | 方式 | 理由 |
|------|------|------|
| cpu_stress | ephemeral container（本 ADR） | Fargate 対応、Deployment spec 変更不要 |
| memory_stress | ephemeral container（本 ADR） | 同上 |
| http_error_inject | Deployment spec 変更（env var パッチ） | アプリレベルの操作のため FIS 不可、ローリングアップデートは許容 |
| network_latency | AWS FIS | Fargate の NET_ADMIN 制約により ephemeral container 不可 |
| pod_kill | Kubernetes API（Pod 直接削除） | 変更なし |

## Consequences

- `cpu_stress_inject`・`memory_stress_inject` はスポット注入方式（ephemeral container）で実装する
- 実験終了時のクリーンアップは Pod 内プロセスの終了のみで済む（Deployment spec の復元が不要）
- 対象 Pod の選択ロジック（ランダム1台 or 指定）を実装に含める必要がある
- `network_latency_inject` は AWS FIS API 呼び出しとして実装し、tc-latency sidecar 方式は廃止する
