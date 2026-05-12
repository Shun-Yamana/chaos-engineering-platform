# ADR 004 - 障害注入シナリオの選定

- Status: Accepted
- Date: 2026-04-28

## Context

Chaos Engineering プラットフォームのポートフォリオとして、SRE視点で現実に発生頻度が高く・インパクトが大きい障害をカバーするシナリオを選定する必要があった。

既存実装は `pod_kill` と `cpu_stress` の2種類のみ。スタックは EKS + FastAPI (service-a / service-b) + Lambda + CloudWatch SLI であり、DBは存在しない。

選定の軸は「頻度 × インパクト」のマトリクスで、現実のSRE現場で踏みやすい障害を優先した。

## Decision

以下5種類の障害注入シナリオを実装する（既存2つ含む）。

| fault_type | 手法 |
|---|---|
| `pod_kill` | Pod強制削除（実装済み） |
| `cpu_stress` | stress-ng sidecar injection（実装済み） |
| `memory_stress` | stress-ng sidecar（`--vm 1 --vm-bytes 80%`） |
| `http_error_inject` | Deployment の `FAULT_RATE` env var をパッチ |
| `network_latency` | `tc netem` sidecar（NET_ADMIN capability） |

## Rationale

### 除外した障害とその理由

- **DB接続プール枯渇**: スタックにDBが存在しないため対象外
- **DNS failure**: コンテナ内からの操作が困難（EKS/Fargate環境での制約）
- **証明書期限切れ**: 環境準備コストが高い割にデモとしての学びが薄い
- **ディスクフル**: デモとして地味でSLI可視化と結びつけにくい

### 採用した理由

**network_latency を最優先にした理由**:
service-b が service-a を `timeout=3.0s` で呼んでいる構造を利用し、2000ms の遅延注入だけでカスケード障害が自然に発生する。「遅いけど死んでいない」部分障害はSREが最も対処する機会が多く、タイムアウト設計・Circuit Breaker の議論に直結する。

**http_error_inject を採用した理由**:
service-a にすでに `FAULT_RATE` 環境変数が実装済みのため、Deployment の env var をパッチするだけでアプリ改修ゼロで動作する。設定ミス（デプロイ起因）シナリオとしても説明できる。

**memory_stress を採用した理由**:
cpu_stress と実装構造が同一（stress-ng sidecar の引数違いのみ）で工数が最小。OOMKill → Pod restart の観測シナリオを追加できる。

### カバレッジ

| 頻度×インパクト | 障害 | 対応 |
|---|---|---|
| 高×大 | カスケード障害 | `network_latency` |
| 高×大 | 設定ミス（デプロイ起因） | `http_error_inject` |
| 高×大 | スケーリング失敗（HPA） | `cpu_stress` |
| 中×大 | OOM / リソース枯渇 | `memory_stress` |
| 高×中 | Pod障害 / Graceful shutdown | `pod_kill` |

## Consequences

- 5シナリオで「頻度高×インパクト大」の3障害をすべてカバーできる
- `network_latency` → service-b 504多発 → SLI検知 → auto-stopper → Slack通知 という end-to-end のデモが1本の流れで作れる
- DB障害シナリオは未カバー（スタック上DBが存在しないため、将来DBを追加した場合は接続プール枯渇シナリオを追加する）
- 追加実装3件の工数は合計半日以内（memory_stress 30分、http_error_inject 1〜2時間、network_latency 2〜3時間）
