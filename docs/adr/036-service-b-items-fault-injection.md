# ADR 036 - service-b の /items/ エンドポイントに FAULT_RATE 注入を追加

- Status: Accepted
- Date: 2026-05-16

## Context

chaos-agent の traffic-gen は `SERVICE_B_URL`（`http://<alb>/items/1`）に 30 秒ごとリクエストを送る。
http_error_inject 実験は Deployment に `FAULT_RATE=0.5` を注入し、50% のリクエストを 500 エラーにすることで SLI の error_rate を引き上げ、auto_stopper の動作を検証する設計だった。

しかし実装上の問題: service-b の `FAULT_RATE` チェックは `/products/{product_id}` エンドポイントにのみ実装されており、`/items/{item_id}` には存在しなかった。

```
traffic-gen → /items/1 → FAULT_RATE 適用なし → 5xx 発生せず → error_rate=0.0 → auto_stopper 不発
```

traffic-gen の URL を `/products/p-001` に変更する案もあったが、`/items/` は service-a を呼び出すカスケード障害シナリオの主要エンドポイントであり、SLI 計測対象として適切。

## Decision

service-b の `/items/{item_id}` にも FAULT_RATE チェックを追加し、`/products/` と同様に 500 エラーを返すようにする。

## Rationale

### traffic-gen URL を `/products/` に変える案を外した理由

`/items/` は service-a への呼び出しを含むカスケード障害シナリオを表現する。ポートフォリオとして「service-a のレイテンシが service-b に伝播する」というシナリオを見せるために重要なパスであり、変更すると実験の文脈が薄れる。

### `/items/` に FAULT_RATE を追加する理由

- traffic-gen が叩くパスと fault injection のパスを一致させる
- `/products/` との対称性が保たれ、どのエンドポイントに来ても FAULT_RATE が機能する
- CloudWatch ALB メトリクスの `HTTPCode_Target_5XX_Count` に計上される

## Consequences

- `/items/` で FAULT_RATE が有効になるため、http_error_inject 実験中は `/items/` へのリクエストの約 `fault_rate` 割合が 500 を返す
- service-a への呼び出しより先に FAULT_RATE チェックを行うため、service-a が正常でも service-b がエラーを返す場合がある（意図的な動作）
- service-b のイメージリビルドが必要（設定変更では対応不可）
