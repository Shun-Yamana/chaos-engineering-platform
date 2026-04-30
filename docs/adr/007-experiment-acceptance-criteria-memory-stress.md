# ADR 007 - 実験合格基準：memory_stress

- Status: Accepted
- Date: 2026-04-30

## Context

memory_stress 実験の合格基準を定義するにあたり、pod_kill との違いを明確にする必要があった。

pod_kill は「即死からの回復力」を検証するのに対し、memory_stress は「メモリ大量消費という劣化状態が続く中でサービスが安定して動き続けられるか」を検証する。両者は性質が異なる実験であり、合格基準も別途定義する。

現在のインフラ設定（確認済み）：
- service-a: memory limits=1Gi
- stress-ng-mem sidecar: limits=512Mi、`--vm 1 --vm-bytes 80%`（≒410Mi を消費）
- service-b: service-a を timeout=3.0s で呼び出す

## Decision

memory_stress 実験の合格基準を以下の通り定める。

| フェーズ | 指標 | 合格基準 |
|---------|------|---------|
| メモリ高負荷中 | エラーレート | SLO閾値（5%）以内 |
| メモリ高負荷中 | レイテンシ P95 | 1000ms 以内 |
| OOMKill 後 | 回復時間 | 17秒以内 |

## Rationale

### pod_kill と合格基準を分けた理由

pod_kill はサービスが binary（alive/dead）の状態であり、回復時間が主要メトリクスになる。
memory_stress はサービスが「生きているが劣化中」という状態を作り出す。この劣化状態でのパフォーマンスを測ることが pod_kill にはない固有の価値である。

```
pod_kill    → 即死 → 回復時間を測る
memory_stress → 劣化状態 → レイテンシ・エラーレートを測る → （OOMKill）→ 回復
```

### レイテンシ P95 < 1000ms の根拠

service-b の timeout=3.0s に対して 2 秒のバッファを確保する。
メモリ高負荷中に OS レベルのメモリ圧迫が発生するとアプリの処理速度が低下し、レイテンシが上昇する。これが 1000ms を超えると service-b の timeout に近づきリスクが高まる。

### OOMKill 後の回復基準を pod_kill と同じにした理由

OOMKill 後は K8s が Pod を再起動するため、挙動は pod_kill と同等になる。
回復時間の理論値は同じ計算式（起動2s + initialDelay5s + period10s = 17秒）で導出できるため、別の基準を設ける必要がない。

### 「OOMKill を確実に起こす設定に変更する」を外した理由

sidecar の `--vm-bytes 80%` はコンテナの memory limits（512Mi）の 80% ≒ 410Mi を消費する。OOMKill は発生しない可能性があるが、メモリ高負荷中のパフォーマンス計測という目的は達成できる。OOMKill を必ず起こすために設定を変更することはこの実験のスコープ外とし、メモリ圧力下での安定性検証に絞る。

## Consequences

- memory_stress 固有の検証価値は「劣化状態でのレイテンシ・エラーレート計測」に絞られる
- レイテンシ P95 の自動判定には `sli_calculator.py` へのレイテンシメトリクス追加が必要（cpu_stress と同様）
- OOMKill が発生しない場合でも実験は有効（メモリ高負荷中のパフォーマンスが合格基準を満たせばよい）
