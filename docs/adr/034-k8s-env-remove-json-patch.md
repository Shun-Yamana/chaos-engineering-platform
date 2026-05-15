# ADR 034 - Kubernetes env var 削除に replace_namespaced_deployment を使用

- Status: Accepted
- Date: 2026-05-16

## Context

chaos-agent は fault inject/remove を Deployment の env var パッチで実現している。
inject（追加）は `patch_namespaced_deployment` に全 Deployment オブジェクトを渡す strategic merge patch で動作していた。

しかし remove（削除）が機能しなかった。原因は Kubernetes の strategic merge patch の挙動:

- `env` 配列は `patchMergeKey: name` 戦略を使うため、パッチ側の配列とサーバー側の配列を **name キーでマージ** する
- パッチ側に存在しない項目はサーバー側に**そのまま残る**（省略 ≠ 削除）
- 追加（inject）は「パッチ側に新項目」→ マージで追加 → 動く
- 削除（remove）は「パッチ側から省略」→ サーバー側に残留 → 動かない

結果として `FAULT_RATE`・`CPU_STRESS`・`LATENCY_MS`・`MEMORY_STRESS_MB` が実験終了後も Deployment に残り続け、後続の実験に混入した。

最初に JSON Patch（RFC 6902, `application/json-patch+json`）での実装を試みたが、kubernetes Python client の `patch_namespaced_deployment` が `content_type` キーワード引数を受け付けずエラーになった（バージョン非互換）。

## Decision

env var の削除操作は `replace_namespaced_deployment`（HTTP PUT）で実装し、Deployment オブジェクト全体を置き換える。

## Rationale

### strategic merge patch に `$patch: delete` ディレクティブを使う案を外した理由

`{"name": "FAULT_RATE", "$patch": "delete"}` という形式は Kubernetes の strategic merge patch の拡張構文で、ドキュメントが不完全で動作が不安定。Python client での扱いも煩雑。

### JSON Patch（`application/json-patch+json`）を外した理由

kubernetes Python client の `patch_namespaced_deployment` が `content_type` キーワード引数を受け付けないバージョンが使われており、実行時エラーになった。クライアントの内部 API に依存した回避策は保守コストが高い。

### `replace_namespaced_deployment`（PUT）を選んだ理由

- `_get_deployment` で取得した最新の `resourceVersion` を持つオブジェクトをそのまま PUT するため、env 配列が完全に置き換わる（merge の影響を受けない）
- chaos-agent の env var 操作は実験中に競合する他の更新がほぼないため、409 Conflict リスクは低い
- 実装がシンプルで `_remove_env_var(namespace, service, var_name)` に集約できる
- inject 側（追加）は strategic merge patch のままでよい（追加は省略問題が発生しない）

## Consequences

- `replace_namespaced_deployment` は `resourceVersion` チェックにより、読み取り後にデプロイが変更された場合は 409 Conflict になる。現状はリトライなしだが、実験の自動クリーンアップ用途では実用上問題ない
- inject 後すぐに rolling update が走る Fargate 環境では、remove 時に `resourceVersion` が更新されている場合がある。今のところ実験完了時（rolling update 完了後）に remove するため衝突は少ない
- `_remove_env_var` は var_name が存在しない場合は何もしない（冪等）
