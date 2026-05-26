# ADR 081 - http_error_inject を Chaos Mesh HTTPChaos に移行

- Status: Accepted
- Date: 2026-05-26

## Context

http_error_inject は Envoy の abort fault filter を使って実装していた（ADR 055）。

```
旧フロー:
  1. Envoy ConfigMap に numerator パッチ
  2. rollout restart（~35秒待機）
  3. 実験（duration_seconds）
  4. ConfigMap を 0 に戻す
  5. rollout restart（~35秒待機）
```

35秒 × 2 = 70秒のオーバーヘッドが実験ごとに発生する。また agent.py に ConfigMap regex patch・rollout restart・rollback の複雑なロジックが存在し、バグの温床になっていた。

Chaos Mesh への移行（ADR 079）でK8sコンテナ層の全実験を HTTPChaos / NetworkChaos / StressChaos / PodChaos に統一できる。

## Decision

**http_error_inject を Chaos Mesh `HTTPChaos`（target: Response, abort: true）に移行する。**

```
新フロー:
  1. HTTPChaos CR を apply（即時）
  2. AllInjected 確認（数秒）
  3. 実験（duration_seconds）
  4. CR を delete → AllRecovered 確認
```

注入先のマッピング：

| experiment.service | HTTPChaos ターゲット | 意味 |
|---|---|---|
| service-a | service-b (port 8000) | service-a が service-b からエラーを受け取る |
| service-b | service-c (port 8000) | service-b が service-c からエラーを受け取る |

`experiment.service` は「どのサービスの防衛を検証するか」を表す。HTTPChaos はその下流サービスの Response を abort する。

Envoy は service mesh として引き続き稼働する（timeout 200ms・outlier detection による CB は Envoy が担う）。変更するのは Envoy の abort filter 設定だけであり、Envoy 自体は除去しない。

## Rationale

### Envoy abort filter を外した理由

- rollout restart が必須であり 35秒 × 2 のオーバーヘッドが避けられない
- ConfigMap の regex patch は壊れやすく、フォーマット変更で無言でバグになる
- Chaos Mesh に統一することでバックエンドが 1 種類に減り、緊急停止ロジックが単純化する

### HTTPChaos を選んだ理由

- restart 不要で即時注入・即時回復
- Envoy の CB・timeout は HTTPChaos のエラーに対しても正常に発火する（Envoy が受け取るのは abort されたレスポンスであり、CB のカウント対象になる）
- `experiment.service` → 下流サービスのマッピングで既存の API 変更が不要

### Envoy を残す理由

Envoy は fault injection 以外に以下の役割を持つ：
- timeout 200ms（service-b, service-c egress）
- outlier detection による Circuit Breaker
- これらは ADR 059・063・064 の防衛実装の核心であり除去できない

## Consequences

- agent.py から `_patch_envoy_fault` / `_rollout_restart` / `http_error_inject` / `http_error_remove` / `_emergency_recover_http` / `_ENVOY_EGRESS_MAP` が削除される
- envoy-config.yaml の abort fault filter 設定（numerator フィールド）を削除してよい（Envoy は残す）
- ClusterRole に `chaos-mesh.org/httpchaoses` の CRUD を追加する
- 70秒のオーバーヘッドが消えるため実験の合計所要時間が短縮される
