# ADR 060 - memory_stress 実験で OOMKill を意図的に発動させる（memoryPercentage 58→95）

- Status: Accepted
- Date: 2026-05-23

## Context

③ memory_stress（service-b メモリ枯渇）実験の FIS テンプレートは `memoryPercentage: 58` に設定されていた。
これは ADR 047 の設計値（150MB/256MB ≒ 58%）を踏襲したもので、OOMKill を **発動させないこと** が意図だった。

しかし「遅い Pod が 300 秒間粘り続ける」状態は、service-a に長時間の遅延を与え続けるため SLO 違反の時間が最大化される。インフラ層の防衛策として OOMKill + 自動再起動を活かすなら、「早く死んで早く戻る」設計にすべきという議論になった。

## Decision

`memoryPercentage` を 58 → 95 に変更する。これにより FIS が割り当てるメモリが約 243MB（256Mi の 95%）となり、アプリ自身のベースライン使用量（~50MB）と合算して limits を超え OOMKill が発動する。

## Rationale

### 「粘る」vs「早く死ぬ」のトレードオフ

| 設定 | 挙動 | SLO 違反時間 |
|---|---|---|
| memoryPercentage: 58（旧） | 148MB で停滞。Pod は生きているが遅い状態が 300s 継続 | **最大 300s** |
| memoryPercentage: 95（新） | ~243MB に達し OOMKill → 再起動 ~10-20s → 健全な 2 Pod に戻る | **~20s** |

Kubernetes の自動再起動を防衛策として活かすには、OOMKill を意図的に発動させる必要がある。
「遅い Pod が粘る」状態は Envoy の outlier_detection が eject するまでタイムアウトエラーを出し続けるため、OOMKill → 即離脱の方が影響時間が短い。

### インフラ層防衛策の本質

pod_kill / cpu_stress / memory_stress はすべて最終的に「Pod を立ち上げ直す」ことで復旧する。
その立ち上げ直しを **できるだけ早く起動させる** ことがインフラ層の防衛設計の核心であり、
memory_stress においては OOMKill の発動速度がその鍵になる。

## Consequences

- FIS `memoryPercentage: 95` に変更（`terraform/fis.tf`）→ `terraform apply` が必要
- アプリのベースラインメモリ使用量が 50MB を大きく超える場合は memoryPercentage を下げる調整が必要
- ADR 047 の `memoryPercentage=58`（OOMKill 回避設計）はこの ADR で上書きされる
- 「空白時間 ~20s に service-a が受ける 5xx 件数」を JMeter .jtl で定量計測する（ADR 058 と同じアプローチ）
