# ADR 068 - Tier 1 セキュリティ・品質ハードニング

- Status: Accepted
- Date: 2026-05-24

## Context

コードベース全体のセキュリティ調査により、以下の問題を確認した。
いずれも個別の機能設計には影響しないが、本番運用品質・最小権限原則・再現性に関わる修正対象。

## Decision

以下7項目を一括修正する。

| # | 対象 | 修正内容 |
|---|---|---|
| 1 | `k8s/chaos-agent.yaml` | `readOnlyRootFilesystem: false → true` + `/tmp` emptyDir |
| 2 | `k8s/chaos-agent.yaml` | liveness probe 追加（プロセス死活確認）|
| 3 | `k8s/xray-daemonset.yaml` | securityContext 追加 + `latest → 3.3.13` ピン留め |
| 4 | `terraform/ecr.tf` | 全5リポジトリ `MUTABLE → IMMUTABLE` |
| 5 | `services/service-a/main.py` | CORS フォールバック `["*"]` → 環境変数未設定で起動失敗 |
| 6 | `lambda/api_handler.py` | `limit` パラメータ上限 100 に制限 |
| 7 | `chaos/agent.py` | `_scan_pending` にページネーション追加 |

## Rationale

### 1・2: chaos-agent readOnlyRootFilesystem + probe

他の全コンテナ（service-a/b/c/d）は `readOnlyRootFilesystem: true` を設定しているが、
chaos-agent だけ `false` だった。不要な書き込み権限は攻撃面を広げる。
`/tmp` を emptyDir でマウントすることで Python ランタイムの一時書き込みに対応する。

liveness probe がないと chaos-agent がデッドロック・クラッシュしても K8s が検知できない。
HTTP エンドポイントを持たないポーラーなので exec probe でプロセス死活を確認する。

### 3: xray-daemonset securityContext + バージョン固定

他のコンテナと同様に `runAsNonRoot: true`・`allowPrivilegeEscalation: false`・`capabilities: drop: ALL` を追加。
`latest` タグは意図しないバージョンアップのリスクがあるため `3.3.13` に固定。
X-Ray daemon の内部書き込みを考慮し `readOnlyRootFilesystem` は設定しない。

### 4: ECR IMMUTABLE

`MUTABLE` タグは `latest` の上書きを許可するため、どのイメージが動いているかが追跡不能になる。
ライフサイクルポリシーがすでに `sha-` プレフィックスを前提とした設定になっており、
`IMMUTABLE` への変更はその設計と一致する。

### 5: CORS フォールバック排除

`CORS_ORIGINS` 未設定時に `["*"]`（全オリジン許可）にフォールバックするのは
設定漏れを無声で許容する設計であり危険。未設定なら起動を失敗させて構成エラーを明示する。
`service-a.yaml` では `CORS_ORIGINS` が明示的に設定されているため、本番への影響はない。

### 6: limit 上限バリデーション

`?limit=100000` のような入力を許容すると DynamoDB の大量スキャンが発生しコスト・遅延が増大する。
API の利用上限として 100 件に制限する。

### 7: _scan_pending ページネーション

DynamoDB の `scan` は 1MB を超えるとページネーションが必要。
`LastEvaluatedKey` を処理しないと 1MB 超のテーブルで pending 実験が見落とされる。
現状のテーブルサイズでは発生しないが、実験が蓄積されると問題になる。

## Consequences

- chaos-agent に emptyDir が追加されるため `kubectl apply` で Pod 再起動が発生する
- ECR IMMUTABLE 変更後は既存の `latest` タグの上書きプッシュが拒否される。
  CI/CD は `sha-<commit>` タグのみを使う運用に統一する（deploy.yml は対応済み）
- service-a は `CORS_ORIGINS` 未設定環境（ローカル開発等）で起動しなくなる。
  ローカル開発時は `CORS_ORIGINS=http://localhost:5173` を設定する
