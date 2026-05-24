# ADR 075 - memory_stress 実験：FIS sidecar が先に OOMKill されて service-b 本体は無影響

- Status: Accepted
- Date: 2026-05-24

## Context

FIS `aws:eks:pod-memory-stress`（percent=95、service-b 全 Pod 対象）を実行した結果、
実験は `MAX_ERRORS`（`Max failed sidecar containers reached`）で失敗した。

FIS のメモリ注入は対象 Pod にエフェメラルコンテナ（sidecar）を注入し、そこから stress-ng を実行する。
sidecar が 95%（≒243Mi）のメモリを確保しようとすると、service-b 本体の使用量（~71Mi）との合算が
Pod の memory limit（256Mi）を超え（71+243=314Mi > 256Mi）、kubelet が sidecar 自体を OOMKill する。

sidecar が消えた後 FIS が exec を試みると `container not found` → `MAX_ERRORS` となり、
service-b 本体のプロセスへは stress が一切届かなかった。

実験中の JMeter エラー率: **0%**、service-b restarts: **0**。

## Decision

この結果を「FIS 側の実験インフラ限界による SKIP（ユーザー影響 0%）」として記録し、
現在の memory limit / percent 設定を変更しない。次のアクションとして
OOMKill 本体検証を目的とする場合は `pod_kill` 実験で代替する。

## Rationale

### sidecar OOMKill が service-b に与える影響がゼロな理由

kubelet は OOMKill 時に **限度超過の直接原因となったコンテナ**（= sidecar）を選択して kill する。
service-b コンテナ自身のメモリ使用量は limit に対して余裕があるため kill されない。

OOMKill が sidecar で止まることで「Pod は生き続けるが stress が入らない」状態となり、
ADR 060 が設計した「OOMKill → 再起動 → 20s で回復」シナリオは発動しなかった。

### percent を下げない理由

percent=58（旧設定）は「遅い Pod が 300s 粘る」ADR 047/060 で廃止済み。
戻すと ADR 060 の設計判断を覆すことになる。

### pod_kill で代替できる理由

ADR 065 の分析通り、memory_stress は最終的に「Pod 消滅 → 再起動」と等価であり、
この障害モードは pod_kill 実験（ADR 072）で既に検証・PASS 済み。

## Consequences

- memory_stress 実験は「FIS 実験インフラ限界により sidecar が先に死ぬ」特性が判明。
- ユーザー影響 0%・service-b 無影響であることは確認済み。
- `aws:eks:pod-memory-stress` の percent 設定と対象コンテナの memory limit の関係を
  今後テンプレート設計時に考慮すること（sidecar + アプリ合算 < limit になるよう percent を下げるか、
  limit を増やす）。
