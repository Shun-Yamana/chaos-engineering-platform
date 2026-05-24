# ADR 075 - memory_stress 実験：FIS sidecar が先に OOMKill されて service-b 本体は無影響

- Status: Accepted
- Date: 2026-05-24

## Context

FIS `aws:eks:pod-memory-stress`（percent=95、service-b 全 Pod 対象）を実行した結果、
実験は繰り返し `MAX_ERRORS`（`Max failed sidecar containers reached`）で失敗した。

FIS のメモリ注入は対象 Pod にエフェメラルコンテナ（sidecar）を注入し、そこから stress-ng を実行する。
`container not found` エラーで sidecar が即死するため service-b 本体への stress は届かなかった。

service-b の memory limit を 256Mi → 100Mi に変更して再実験したが同様に失敗。
JMeter エラー率: **0%**、service-b restarts: **0**（全試行を通じて）。

**根本原因の調査結果：** Kubernetes のコンテナは個別の cgroup で動作する。

```
Pod（cgroup 親）
├── service-b container  [cgroup: 100Mi limit]  ← 75Mi 使用
├── envoy container      [cgroup: 128Mi limit]
└── FIS ephemeral sidecar [cgroup: 独自 limit]  ← 95Mi 確保しようとして自分が死ぬ
```

FIS sidecar は service-b とは**別の cgroup**で動作するため、sidecar の OOMKill は
service-b の cgroup に影響を与えない。`percent=95` は sidecar 自身の cgroup 内で
メモリを使い切り → sidecar が OOMKill → FIS が `container not found` → `MAX_ERRORS`。

## Decision

`aws:eks:pod-memory-stress` では service-b 本体を OOMKill できないことが判明したため、
memory_stress 実験を **SKIP** とする。service-b の memory limit は 256Mi に戻す。

## Rationale

### cgroup 分離により service-b を OOMKill できない理由

FIS のエフェメラルコンテナは独立した cgroup で動作する。service-b の cgroup limit を
どれだけ下げても sidecar の cgroup には影響しない。sidecar 自身が自分の cgroup 内で
OOMKill されるだけで service-b プロセスは無傷のまま。

### pod_kill との違い（補足）

pod_kill と OOMKill は異なる障害モードであり代替関係にはない：
- **pod_kill**：Pod ごと削除 → 新 Pod スケジューリング（IP 変更、~20s）
- **OOMKill**：コンテナのみ再起動（Pod IP 維持、~5s）

ただし FIS の実験手段では service-b の OOMKill を誘発できないため、
OOMKill シナリオの検証は今回スコープ外とする。

### 代替手段を採用しない理由

カスタム負荷スクリプトによる Python メモリ枯渇は実装コストが高く、
service-b の通常運用メモリ（75Mi）と limit（256Mi）の余裕が十分あるため
OOMKill が自然発生する可能性は低い。このリスクに対しては kubelet の
自動再起動 + 2 replicas が防衛策として存在する（ADR 072）。

## Consequences

- service-b memory limit を 256Mi に戻す（100Mi は実験目的の一時変更）。
- `aws:eks:pod-memory-stress` は cgroup 分離の制約により service-b 本体の
  OOMKill テストには使用できない。
- FIS でメモリ系のカオス実験を行う場合は、sidecar の cgroup limit が
  対象コンテナの cgroup と分離されていることを前提に設計すること。
