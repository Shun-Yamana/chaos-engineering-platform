# ADR 025 - 認証フロー初回デプロイで発生した修正の記録

- Status: Accepted
- Date: 2026-05-12

## Context

CloudFront + Cognito + API Gateway を組み合わせた認証フローを初めて本番環境で動かした際、
4つの問題が連鎖して発生した。いずれも「ローカル開発では問題ない」が「本番では失敗する」
パターンであり、開発環境と本番環境の差異に起因している。

---

## 修正一覧

### 1. redirectUri に `/callback` を付けていた → Cognito が redirect_uri mismatch でトークン交換を拒否

**何が起きたか**
Cognito の Hosted UI でログイン後、アプリに戻ってくるが認証が完了しない。
ブラウザの sessionStorage にトークンが保存されないまま LoginPage に戻される。

**なぜ起きたか**
`auth.ts` の `redirectUri()` が `window.location.origin + "/callback"` を返していた。
しかし Terraform で Cognito client に登録した callback URL は `https://d1pp2kv5z34rko.cloudfront.net`（`/callback` なし）だった。
Cognito はトークン交換時に redirect_uri が登録済み URL と完全一致しないと拒否する。

**どう直したか**
`redirectUri()` を `window.location.origin`（パスなし）に変更した。
`App.tsx` は `window.location.search` で code を拾う設計のため、
ルートパスへのリダイレクトでも正常に動作する。

---

### 2. `access_token` を Authorization ヘッダーに使っていた → API Gateway が 401

**何が起きたか**
ログインは完了し id_token / access_token の両方が sessionStorage に保存された。
しかし API 呼び出しがすべて 401 になった。

**なぜ起きたか**
`getToken()` が `sessionStorage.getItem("access_token")` を返していた。
Cognito の access_token には `aud` クレームが存在しない（`client_id` クレームのみ）。
API Gateway HTTP API の JWT authorizer は `aud` クレームの存在と一致を必須チェックするため、
access_token を送ると無条件に 401 を返す。
id_token には `aud = client_id` が含まれており、JWT authorizer を通過できる。

**どう直したか**
`getToken()` を `sessionStorage.getItem("id_token")` に変更した。

| token 種別 | `aud` クレーム | API Gateway 通過 |
|---|---|---|
| access_token | なし | ✕ 401 |
| id_token | `aud = client_id` | ✓ |

---

### 3. Vite キャッシュにより本番ビルドのコード変更が反映されなかった

**何が起きたか**
`auth.ts` を修正してビルドしたが、出力 JS のファイル名ハッシュが変わらなかった。
CloudFront に古いコードが残り続け、修正が本番に反映されていなかった。

**なぜ起きたか**
`frontend/.vite/` ディレクトリにビルドキャッシュが残っており、
Vite がキャッシュから前回のビルド結果を再利用した。
コンテンツハッシュが変わらないため `aws s3 sync` もファイル更新をスキップした。

**どう直したか**
`frontend/.vite/` を削除してからビルドし直した。
ファイル名ハッシュが変わり、S3 と CloudFront に新しいファイルが配信された。

---

### 4. `.env.local` の `VITE_DEV_AUTH=skip` が本番ビルドに混入

**何が起きたか**
Network タブで確認すると `Authorization: Bearer dev-token` が送られていた。
`dev-token` は Cognito トークンではないため API Gateway が 401 を返す。

**なぜ起きたか**
ローカル開発用の `.env.local` に `VITE_DEV_AUTH=skip` が設定されていた。
Vite の環境変数読み込み優先順位では `.env.local` は本番ビルドでも読み込まれ、
`.env.production` の設定より `.env.local` が優先される（上書きされない限り）。
そのため `VITE_DEV_AUTH === "skip"` が true になり、`getToken()` が常に `"dev-token"` を返していた。

**どう直したか**
`.env.production` に `VITE_DEV_AUTH=` （空文字）を追加して明示的に上書きした。

Vite の env ファイル優先順位（高 → 低）:
```
.env.[mode].local  >  .env.[mode]  >  .env.local  >  .env
```
`.env.production` で `VITE_DEV_AUTH=` を設定することで `.env.local` の値を打ち消す。

---

## まとめ

4つの問題すべてに共通するパターン：**「ローカルでは動くが本番では失敗する」設定の見落とし**。

| # | 原因分類 | 教訓 |
|---|---|---|
| 1 | Cognito URL 登録と実装の不一致 | redirect_uri は登録値と文字列完全一致が必須 |
| 2 | Cognito token の仕様理解不足 | JWT authorizer には id_token を使う（access_token は aud なし） |
| 3 | Vite ビルドキャッシュ | 本番デプロイ前は `.vite/` を削除してクリーンビルドする |
| 4 | 開発用 env が本番ビルドに混入 | `.env.local` は本番ビルドでも読まれる。本番で無効にすべき値は `.env.production` で明示的に空にする |
