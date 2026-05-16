# ADR 045 - chaos-agent ALB 自動検出：Ingress 優先・env var フォールバック化

- Status: Accepted
- Date: 2026-05-16

## Context

`terraform apply` 後、chaos-agent の traffic generator が `Name or service not known` エラーを出し続け、
service-b へのトラフィックがゼロになった。

### 原因の連鎖

1. `apply_ingress` が ALB 払い出しを待ち、`kubectl set env deployment/chaos-agent ... SERVICE_B_URL=<ALB>` で env var を更新する
2. しかし `k8s/chaos-agent.yaml` に前回 apply 時の ALB ホスト名がハードコードされていた
3. 何らかのタイミング（manifest 再 apply、rolling update）で chaos-agent が yaml から再作成されると古い URL が復元される
4. ALB ホスト名は Ingress UID が変わるたびに末尾ハッシュが変わる（例: `1389829488` → `2074427057`）
5. 古い ALB は DNS から消えているため `[Errno -2] Name or service not known` が発生

### ADR 028 のフォールバックが機能しなかった理由

ADR 028 で `_discover_service_b_url()` による Ingress 自動検出を実装していたが、
ロジックが `if not SERVICE_B_URL: discover()` だったため、
env var に古い値が設定されている場合は Ingress 検出がスキップされた。

さらに `_discover_service_b_url(core_v1)` に `CoreV1Api` を渡していたが、
Ingress は `NetworkingV1Api` の API であるため
`'CoreV1Api' object has no attribute 'read_namespaced_ingress'` で常に例外になっていた。

## Decision

1. **Ingress を常に優先参照**し、失敗した場合のみ env var にフォールバックする
2. **`NetworkingV1Api`** を使って Ingress を取得する
3. **`k8s/chaos-agent.yaml` から `SERVICE_B_URL` env var を削除**し、ハードコードされた古い URL が復元されないようにする

## Rationale

### Ingress 優先にした理由

ALB ホスト名は apply のたびに変わりうる（Ingress 再作成 → 新 UID → 新ハッシュ）。
env var は「その瞬間の apply」の値であり、次の apply 後は stale になる。
Ingress の `.status.loadBalancer.ingress[0].hostname` は常に現在の ALB を指す唯一の信頼できるソース。

### `SERVICE_B_URL` env var を削除した理由

yaml にハードコードすると apply のたびに手動更新が必要になり、更新漏れが原因でトラフィック断が繰り返す。
Ingress 優先検出が正常に動く前提では env var は不要。
env var をフォールバックとして残す理由は「Ingress がまだ存在しない起動直後」への対応だが、
通常は chaos-agent が起動するより先に Ingress と ALB が存在するため実質不要。

## Consequences

- chaos-agent 起動時に毎回 Kubernetes API（`NetworkingV1Api.read_namespaced_ingress`）を 1 回呼ぶ
- ALB が再作成されても次の chaos-agent 再起動（rolling update など）で自動的に正しい URL を取得する
- `k8s/chaos-agent.yaml` に ALB ホスト名が残らなくなるため、manifest の git diff が ALB 変更で汚れない
- Ingress が存在しない環境（chaos-agent だけ先に起動するケース）では env var フォールバックが機能する
