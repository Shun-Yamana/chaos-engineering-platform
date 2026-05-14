# ADR 031 - 商品詳細デモ設計（レジリエンスのユーザー体験可視化）

- Status: Accepted
- Date: 2026-05-15

## Context

CloudWatch のメトリクスと DynamoDB の実験結果だけでは、「service-b に障害が起きたときユーザーにどう見えるか」が数値だけになり実感が湧きにくい。レジリエンスパターン（stale cache / circuit breaker / fallback）の価値を第三者に説明するには、ユーザー視点の画面があった方が伝わる。

また service-a / service-b の役割が `/items/{id}` の汎用 API のままでは、「なぜ service-b が 500ms 遅延するのか」の文脈が弱い。

## Decision

service-b を「商品詳細プロバイダ」（レビュー要約・レコメンド理由を返す）として再定義し、service-a 経由の商品詳細集約 API と、それを表示するフロントエンドデモ画面を追加する。フロントエンドには運用メトリクスを一切出さず、ユーザーが見る商品詳細ページとして自然な劣化表示のみ見せる。

## Rationale

### 「メトリクスダッシュボード」を外した理由

service-a latency・circuit breaker state・fallback count などを画面に出す案も検討した。しかしこれらは運用者向け情報であり、ユーザー体験のデモという目的に合わない。「障害を隠さず、影響を抑制する」という設計方針（ADR 030）を体現するには、ユーザーが見る画面こそがデモとして適切。運用情報は CloudWatch / ADR / README で説明する。

### 商品詳細という題材を選んだ理由

- レビュー要約・レコメンド理由は「重い処理」として 500ms 遅延の文脈が自然
- fresh / stale / fallback の 3 状態が商品カードとして直感的に表現できる
- 在庫・価格が null になる fallback が「一時的に取得できません」として自然に見える
- DB 不要・固定データで実装できる

### API 設計の判断

- service-b: `GET /products/{product_id}` — 既存の `/items/` `/data/` は変更せずテストを維持
- service-a: `GET /aggregate/products/{product_id}` — timeout 200ms（ADR 030 の network_latency 500ms 注入で必ず発火）、stale cache TTL 30s、circuit breaker（5連続失敗→open、30s後 half-open）
- レスポンスに `resilience` フィールドを含める — フロントでは非表示だが CloudWatch EMF の根拠として残す

## Consequences

- network_latency 実験（`LATENCY_MS=500`）中、Demo 画面は「キャッシュされた情報を表示しています / 最終更新: Xs前」に切り替わる
- service-b 完全停止かつキャッシュ切れ時は「商品情報を一時的に取得できません」になる
- circuit breaker は in-process のため、Pod 再起動でリセットされる（本番 Redis 連携は対象外）
- `/products/` エンドポイントは既存テスト対象外。追加テストは別 ADR または PR で対応する
