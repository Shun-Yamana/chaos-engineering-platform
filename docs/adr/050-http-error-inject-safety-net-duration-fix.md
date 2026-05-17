# ADR 050 — http_error_inject Safety Net FAIL: CloudWatch 遅延・auto_stopper 競合・_remove_env_var 409 修正

## Status
Accepted

## Context

http_error_inject 実験を繰り返したところ、Safety Net（auto_stopper 発動確認）が FAIL し続けた。
DynamoDB を直接確認すると `emergency_stop` フィールドが存在しないことが判明。

### 調査結果

SLI_TABLE のデータを時系列で照合したところ以下の問題が重なっていた。

| 時刻 | SLI error_rate | 状態 |
|------|----------------|------|
| 17:32:07 | — | 実験開始・FAULT_RATE=0.5 パッチ |
| 17:32:48〜17:35:48 | 0.0 | SLI で違反を検知できず |
| 17:36:48 | **0.5** | SLI が初めて違反を検知 |
| 17:37:16 | — | 実験終了（duration=300s） |
| 17:37:48 | 0.5 | auto_stopper が実行 → 実験は既に "completed" |

**根本原因 (1): CloudWatch ALB メトリクス遅延 (~4分)**

ALB の `HTTPCode_Target_5XX_Count` は実際の発生から CloudWatch に現れるまで約 4 分の遅延がある。
`sli_calculator` は `WINDOW_MINUTES=5`（実際は+1=6分窓）で測定するため、
fault 注入直後は窓内の fault 期間が短く error_rate が希薄化される。
結果、SLI で SLO 違反が検知されるのは fault 注入から 4〜5 分後になる。

**根本原因 (2): auto_stopper と sli_calculator の競合**

`sli_calculator` と `auto_stopper` はともに EventBridge で毎分同タイミングに起動する（実測 :48秒台）。
auto_stopper が sli_calculator の書き込みより数百ミリ秒早く SLI を読むと、
前の分のデータ（error_rate=0.0）を参照し、違反を検知できない。

duration=300s では CloudWatch 遅延によって SLI 違反検知が実験終了間際となり、
競合ウィンドウが発生する → auto_stopper が実験終了後にしか走れない → `emergency_stop` 未設定。

**副次問題: Phase A の auto_stopper_latency_s が誤フィールドを参照**

evaluator の `evaluate_http_error_inject` が `stopped_at`（実験終了時刻）を
"auto_stopper 発動時刻" として使用していた。正しくは `emergency_stop_at` を使うべき。
結果として Phase A の `auto_stopper_fired_within_6min` は常に PASS していた（実質無効な check）。

## Decision

### 1. http_error_inject のデフォルト duration を 300s → 600s に変更

`frontend/src/components/ExperimentForm.tsx` の `DEFAULT_DURATION` を修正。

CloudWatch 遅延 4 分 + auto_stopper ラグ 1 分 = 5 分でも、duration=600s なら
実験が依然 running 中に auto_stopper が発動できる余裕が確保される。

Phase B の計測窓 (fault_end+180s〜fault_end+360s) は emergency_recover 完了後の
十分に安定した期間を捉えられる。

### 2. evaluator Phase A を emergency_stop_at フィールドに修正

`lambda/experiment_evaluator.py` の `evaluate_http_error_inject` を修正:

```python
# Before (誤): stopped_at = 実験終了時刻
stopped_at = item.get("stopped_at")

# After (正): emergency_stop_at = auto_stopper 実際の発動時刻
emergency_stop_at = item.get("emergency_stop_at")
```

`emergency_stop_at` が None（auto_stopper 未発動）の場合、`_check` は `pass: null`（データなし）を返す。
Safety Net が FAIL するため overall は FAIL となり、整合性が保たれる。

## Consequences

- http_error_inject 実験は 10 分に延長される（フォームのデフォルト・スライダー上限は元から 600s）
- Phase A `auto_stopper_fired_within_6min` が実際の auto_stopper 発動時刻を反映するようになる
- Safety Net PASS のためには CloudWatch 遅延 < 600s かつ auto_stopper が 1 回以上発動すること

### 3. _remove_env_var に 409 Conflict リトライを追加

emergency_recover の手順:
1. `patch_namespaced_deployment_scale` (replicas=0) → Deployment の resourceVersion が変わる
2. `_remove_env_var` → `_get_deployment`(最新 fetch) → `replace_namespaced_deployment` (PUT)

手順 1 と 2 の間に HPA などが Deployment を再変更すると resourceVersion がずれて 409。
`_remove_env_var` 内で `ApiException(409)` をキャッチし最大 3 回リトライすることで解消。

```python
for _ in range(3):
    deployment = self._get_deployment(namespace, service)
    # ... 環境変数削除 ...
    try:
        self.apps_v1.replace_namespaced_deployment(...)
        return
    except ApiException as e:
        if e.status == 409:
            continue
        raise
```

## Alternatives Considered

- **sli_calculator WINDOW_MINUTES を短縮**: 窓を短くしても CloudWatch 遅延は変わらず効果なし
- **auto_stopper の起動タイミングをずらす**: Terraform 変更が必要。duration 延長で十分なため見送り
- **auto_stopper が CloudWatch を直接参照**: SLI アーキテクチャの意義が失われる
