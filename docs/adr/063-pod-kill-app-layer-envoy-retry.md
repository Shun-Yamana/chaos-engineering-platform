# ADR 063 - pod_kill アプリ層防御 — Envoy retry_policy (a→b connect-failure 限定)

- Status: Accepted
- Date: 2026-05-24

## Context

インフラ層（replicas=2、PDB、multi-AZ）で pod_kill に対する構造的耐性は確保済み（ADR 061）。
しかし Envoy の outlier_detection は **連続5回の local_origin_failure でエンドポイントを eject** する設計であり、
pod 削除の瞬間から eject までの間に最大5リクエストが connect-failure で失敗する窓が存在する。

JMeter は 20 req/s（通常）/ 100+ req/s（高負荷）を流しており、この窓に複数のリクエストが集中する。
アプリ層で connect-failure を即座に救済する手段が未実装であった。

また、①（a→b pod_kill）と ⑥（b→c pod_kill）は同一障害種別だが SLO への影響が異なる（ADR 057）：

- ① service-b 攻撃 → **SLO 違反**（5xx 発生）→ retry で救済すべき
- ⑥ service-c 攻撃 → **SLO 維持**（reviews=null フォールバックで 200 を返す）→ retry 不要

## Decision

**a→b の Envoy route に retry_policy を追加する（connect-failure / reset 限定、1回）。**
**b→c には retry_policy を追加しない。**

```yaml
# envoy-service-b-egress: a→b route
route:
  cluster: service_b_cluster
  timeout: 0.2s
  retry_policy:
    retry_on: "connect-failure,reset"
    num_retries: 1
    per_try_timeout: 0.1s   # route_timeout / (num_retries + 1) = 200ms / 2
```

## Rationale

### per_try_timeout を 100ms に設定した理由

`per_try_timeout` には構造的上限がある：

```
per_try_timeout < route_timeout / (num_retries + 1)
               = 200ms / 2 = 100ms
```

これを超えると retry 試行の残り予算がゼロになり retry が機能しない。
100ms は service-b /data の正常応答（~10–30ms）の 3 倍以上あり、
Fargate のコンテナスケジューリングジッターを十分に吸収できる。

### route timeout を 200ms のまま維持した理由

network_latency 実験（④）は Envoy delay filter で a→b に遅延を注入し、
200ms route timeout の超過を意図的に発生させる設計（ADR 057）。
route timeout を上げると network_latency の検出精度が下がり、
service-a の P99 が悪化する（200ms → 400ms）。

### retry_on を connect-failure,reset に限定した理由

- `5xx` を含めると http_error_inject 実験（⑤）の Envoy abort filter が返す 500 をリトライしてしまう
- `gateway-error` を含めると network_latency のタイムアウト（per_try_timeout 起因）もリトライ対象になり、
  healthy な retry 試行が network_latency 下でも発動してしまう
- connect-failure と reset だけに絞れば pod_kill の TCP 切断だけを対象にできる

### b→c に retry を入れない理由

⑥ の期待結果は「service-c が死んでも reviews=null で SLO を維持する」こと（ADR 057）。
retry を入れるとフォールバックの発動機会が減り、graceful degradation の実証ストーリーが薄れる。
また b→c はアプリ層タイムアウト（`_REVIEW_TIMEOUT_S=0.1s`）が Envoy タイムアウト（500ms）より先に発火するため、
Envoy の per_try_timeout との整合性を別途調整する必要が生じる。

## Consequences

- **pod_kill ① の改善効果**：connect-failure → 即 retry（<5ms）→ healthy pod で成功。
  outlier_detection の eject 前の最大5件の失敗リクエストの大半を救済できる。
- **network_latency ④ への副作用**：per_try_timeout(100ms) が発火 → retry → route timeout(200ms) で失敗。
  upstream 呼び出しが2回になるが最終結果は同じ（stale cache / fallback）。JMeter 上の SLO 判定は変わらない。
- **http_error_inject ⑤ との分離**：retry_on が connect-failure,reset のみのため、
  abort filter の 500 はリトライされず意図した実験挙動を維持する。
- **①⑥ 非対称設計**：同じ pod_kill でも a→b はリトライ、b→c はフォールバックで対処する。
  これは ADR 057 の SLO 影響分析に基づく意図的な非対称性である。
