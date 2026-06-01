# ADR 094 - http_error_inject の fault injection を HTTPChaos から service-b 内蔵機構に移行

- Status: Accepted
- Date: 2026-05-30

## Context

`http_error_inject-service-b` 実験は「service-c が落ちたとき service-b が止血できるか」を
テストする目的で HTTPChaos を使っていた。

一日かけてデバッグした結果、HTTPChaos (Chaos Mesh) によるネットワーク層注入は
このユースケースで構造的に機能しないことが判明した。

**判明した問題の連鎖:**

1. **Envoy keepalive バイパス**  
   service-b Envoy (localhost:9002) は service-c への TCP 接続を keepalive で維持する。
   Chaos Mesh tproxy は新しい TCP 接続しか intercept できないため、
   既存接続は tproxy を素通りして 200 OK が返り続ける。

2. **max_connection_duration を追加しても解決せず**  
   Envoy cluster に `max_connection_duration: 5s` を設定して接続を強制更新したが、
   2 台の service-c Pod のうち片方しか tproxy が機能していない状態が続いた。
   Envoy のリトライが tproxy なしの Pod に到達して成功する。

3. **`path: "*"` がヘルスチェックも abort する**  
   HTTPChaos の `path: "*"` は `/health` も abort するため、
   Kubernetes liveness/readiness/startup probe が失敗し service-c が CrashLoop に入る。
   `path: "/reviews/*"` に絞っても根本的な問題（Envoy バイパス）は残る。

4. **CrashLoop × finalizer のデッドロック**  
   service-c が CrashLoop 中は Chaos Mesh が tproxy cleanup を完了できず
   HTTPChaos CR の finalizer が外れない。CR が残留する間は abort が継続し
   さらに CrashLoop が深まる。scale 0→2 で強制解消が必要だった。

5. **Pod 再起動で tproxy リセット**  
   service-c Pod が何らかの理由で再起動すると新 Pod には tproxy が適用されるまで
   タイムラグがある。その間 Envoy が新 Pod に接続して 200 OK を受け取る。

## Decision

`http_error_inject-service-b` 実験の fault injection を
**service-b 内蔵の `FAULT_RATE` 環境変数**による注入に切り替える。

chaos-agent が実験開始時に `kubectl set env deployment/service-b FAULT_RATE=1.0` し、
終了時に `FAULT_RATE=0.0` に戻す。評価基準も `FallbackCount >= 1`
（service-a が service-b 失敗を検知）に変更する。

## Rationale

### HTTPChaos (Chaos Mesh) を外した理由

- Envoy connection pool + 2 Pod 構成との相性が悪く、確実な注入が困難
- tproxy の残留や CrashLoop デッドロックなど運用コストが高い
- `path` 指定を誤るとヘルスチェックが abort されてインフラが不安定になる

### service-b 内蔵 fault injection を選んだ理由

- service-b/main.py に `FAULT_RATE` 環境変数による 500 応答機構が既に実装済み
- Envoy・tproxy・iptables に依存せず確実に動作する
- `kubectl set env` で即時反映・即時停止できる
- service-a の FallbackCount・StaleCacheHitCount が正確に上昇し評価しやすい

## Consequences

**得るもの**
- 確実に fault injection できる（今日のような一日無駄にならない）
- service-c が影響を受けない（より局所的な障害テスト）

**失うもの・変わること**
- テストするシナリオが変わる：「service-c 障害 → service-b 止血」ではなく
  「service-b 障害 → service-a 止血」になる
- service-b の `reviews: null` は発生しなくなる（service-b 自体が 500 を返す）

**実装時の注意**
- chaos-agent の `_cm_run` / `_cm_emergency_stop` を service-b 向けには呼ばず、
  代わりに `kubectl set env` を使う分岐を追加する
- 実験終了後（正常終了・強制停止どちらも）必ず `FAULT_RATE=0.0` に戻すこと。
  戻し忘れると恒久的に service-b が 500 を返し続ける。
- evaluator の `evaluate_http_error_inject` を
  `FallbackCount >= 1` + `cascade_error_rate <= 0.05` に変更する
