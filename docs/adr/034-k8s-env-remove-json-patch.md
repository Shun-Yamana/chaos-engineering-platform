# ADR 034 - Kubernetes env var 削除に JSON Patch を使用

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

結果として `FAULT_RATE`・`CPU_STRESS`・`LATENCY_MS`・`MEMORY_STRESS_MB` が実験終了後もDeployment に残り続け、後続の実験に混入した。

## Decision

env var の削除操作は `application/json-patch+json` コンテントタイプの JSON Patch（RFC 6902）で実装し、`op: remove` にコンテナ index と env index を指定して確実に削除する。

## Rationale

### strategic merge patch に `$patch: delete` ディレクティブを使う案を外した理由

`{"name": "FAULT_RATE", "$patch": "delete"}` という形式は Kubernetes の strategic merge patch の拡張構文で、ドキュメントが不完全で動作が不安定。Python client での扱いも煩雑。

### `replace_namespaced_deployment`（PUT）を使う案を外した理由

全オブジェクトを置き換えるため、同時に rolling update 中など `resourceVersion` が変わっている状況で 409 Conflict が発生しやすい。リトライ実装が必要になる。

### JSON Patch を選んだ理由

- RFC 6902 準拠で動作が明確
- Python kubernetes client に `content_type="application/json-patch+json"` を渡すだけで使える
- 対象の index を指定するため「意図した env var だけ」を削除できる
- `_remove_env_var(namespace, service, var_name)` ヘルパーに集約し、全 fault type の remove が共通ロジックを使う

## Consequences

- JSON Patch は index ベースのため、複数の env var を同時削除する場合は後ろの index から順に処理しないとずれる（現状は 1 変数ずつ削除するため問題なし）
- inject 側（追加）は strategic merge patch のままでよい（追加は省略問題が発生しない）
- `_remove_env_var` は var_name が存在しない場合は何もしない（冪等）
