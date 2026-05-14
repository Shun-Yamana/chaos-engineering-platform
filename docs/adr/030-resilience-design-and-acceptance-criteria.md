# ADR 030 - 対障害設計と実験合格基準

- Status: Accepted
- Date: 2026-05-14

## Context

ADR 005〜009 は各実験の「合格基準（数値）」を定義したが、以下の 3 点が未解決のまま残っていた。

1. **レイテンシ P95 の収集経路がない**：`sli_calculator.py` はエラーレートしか計算しておらず、ADR 006・007・009 で定義した「P95 ≤ 1000ms」が自動判定できない。
2. **実験 PASS/FAIL の自動判定がない**：合格基準は数値で定義されているが評価は手動（DynamoDB を目視）。
3. **レジリエンスパターンが未実装**：障害を注入するだけで、それに抵抗する仕組みがない。ポートフォリオとして「障害注入 → 耐える → 証拠を残す」の流れが必要。

加えて Guardian レビューで **service-a SLI 未計算バグ** が発覚した。`sli_calculator.py` は `SERVICES = ["service-b"]` のみ対象だが `auto_stopper.py` は `["service-a", "service-b"]` を対象にしており、service-a の自動停止が機能しない。

## Decision

以下を同時に実装する。

1. 実験タイプごとのレジリエンスパターンを実装する（クラウド側 fail fast + アプリ側 degrade gracefully）
2. ALB `TargetResponseTime` P95 を SLI に追加し、レイテンシを自動収集する
3. 実験完了後に PASS/FAIL を自動判定する Lambda（`experiment_evaluator`）を追加する
4. `sli_calculator.py` の SERVICES を service-a まで拡張し auto_stopper との不一致を解消する

## Rationale

### fail fast と degrade gracefully を分担する理由

クラウド側（インフラ設定）は「障害を素早く検知し、待たない・広げない」ことに特化しており、アプリコードを変えずに設定だけで制御できる。アプリ側は「失敗しても意味のある応答を返す」という振る舞いを担う。この分担を明示することで、どのレイヤーが何を責任として持つかが明確になる。

### 実験評価 Lambda を追加する理由

合格基準が数値で定義されていても自動判定がなければ、実験を回すたびに手動で CloudWatch を確認する必要がある。DynamoDB Stream で `status = "completed"` を検知し、実験タイプごとのチェックロジックを実行することで、実験結果を `pass/fail + 根拠メトリクス` として DynamoDB に残す。

---

## レジリエンスパターンと合格基準

### 1. pod_kill

**目標**：1台 Kill されても **エラーレート ≤ 1%** を維持する

| レイヤー | 実装 | 効果 |
|---|---|---|
| ☁️ | `replicas: 2`（service-a / service-b 両方） | Kill されてももう 1 台がトラフィックを処理。ダウンタイムなし |
| ☁️ | `PodDisruptionBudget` `minAvailable: 1` | rolling update / node drain 時に同時停止を防ぐ（強制 Kill は対象外） |
| ☁️ | `readinessProbe`（`/health`, 5s interval, 2 threshold） | 起動中の Pod にトラフィックが来ないよう制御 |
| ☁️ | `startupProbe`（Fargate 起動 30〜60s を考慮） | 起動完了前のトラフィック流入を防ぐ |
| ☁️ | `topologySpreadConstraints` maxSkew: 1 | AZ 分散。AZ 障害でも全 Pod 同時死にを防ぐ |
| 🖥️ | SIGTERM ハンドリング（graceful shutdown 5s） | Kill 時に処理中リクエストを完了してから終了 |

**合格基準**

| メトリクス | 閾値 | 計測元 |
|---|---|---|
| Kill 後 60s 間のエラーレート | ≤ 1% | ALB `HTTPCode_Target_5XX_Count / RequestCount` |
| Pod 再起動後のトラフィック受信開始 | readiness probe 通過後のみ | K8s readiness probe |
| auto-stopper 発動 | しないこと | DynamoDB `status = "stopped"` の不在 |
| 回復時間（エラーレート 0% に戻るまで） | ≤ 60s | CloudWatch ALB メトリクス |

---

### 2. cpu_stress

**目標**：CPU 高負荷中も **P95 レイテンシ ≤ 1000ms / エラーレート ≤ 5%** を維持する

| レイヤー | 実装 | 効果 |
|---|---|---|
| ☁️ | `resources.limits.cpu: 256m` 明示 | バーナースレッドの暴走に上限。CPU throttling で他 Pod への影響を隔離 |
| ☁️ | HPA `targetCPUUtilizationPercentage: 60` | CPU 高負荷 → Pod スケールアウト → 1 Pod あたりのリクエスト減 → CPU 争奪緩和 |
| ☁️ | KEDA（発展）CloudWatch ALB `RequestCountPerTarget` ベース | HPA より高速な反応。CPU ではなくリクエスト量をトリガーにできる |

> **注意**: env var パッチによるストレス注入は新たにスケールアウトした Pod にも同じ env が適用されるため、CPU stress は全 Pod で発生する。それでも Pod 数が増えることで 1 Pod あたりのリクエスト密度が下がり p95 の改善は見込める。

**合格基準**

| メトリクス | 閾値 | 計測元 |
|---|---|---|
| P95 レイテンシ（実験中） | ≤ 1000ms | ALB `TargetResponseTime` ExtendedStatistics p95 |
| エラーレート | ≤ 5% | ALB `HTTPCode_Target_5XX_Count / RequestCount` |
| HPA スケールアウト | 5 分以内に Pod 数 ≥ 2 | CloudWatch `ContainerInsights pod_number_of_running_containers` |
| auto-stopper 発動 | しないこと | DynamoDB |

---

### 3. memory_stress

**目標**：OOMKill が発生しても **回復時間 ≤ 60s / エラーレート ≤ 5%** を維持する

| レイヤー | 実装 | 効果 |
|---|---|---|
| ☁️ | `resources.limits.memory: 256Mi` 明示 | メモリ暴走時に OOMKill させ障害を Pod 内に閉じ込める。未設定だとノード全体に影響が広がる可能性 |
| ☁️ | `readinessProbe` | OOMKill 後の再起動中 Pod にトラフィックを流さない |
| ☁️ | `livenessProbe` | OOMKill ではなく **ハングや復旧不能状態**（メモリリークによるプロセス停止）を検知して再起動 |
| ☁️ | `restartPolicy: Always`（デフォルト） | OOMKill 後に kubelet が自動再起動 |

> **注意**: HPA（メモリベース）はスケールアウトした Pod も同じストレスを受けるため根本解決にならない。「緩和策」として位置づける。

**合格基準**

| メトリクス | 閾値 | 計測元 |
|---|---|---|
| OOMKill 後の回復時間 | ≤ 60s | 実験開始〜エラーレート 0% 復帰のタイムスタンプ差分 |
| 高負荷中のエラーレート | ≤ 5% | ALB メトリクス |
| P95 レイテンシ（高負荷中） | ≤ 1000ms | ALB `TargetResponseTime` p95 |
| auto-stopper 発動 | しないこと | DynamoDB |

---

### 4. http_error_inject

**目標**：FAULT_RATE=0.5 で auto-stopper が **5 分以内に発動** し、ユーザーには **branded fallback** を返す

| レイヤー | 実装 | 効果 |
|---|---|---|
| ☁️ | CloudFront custom error response（5xx → 静的フォールバックページ） | ユーザーへの raw 5xx を遮断。origin の 5xx は ALB / CloudWatch で監視継続 |
| ☁️ | CloudFront origin failover（primary: ALB, secondary: S3 fallback） | ALB が全断した場合の最終防衛 |
| 🖥️ | リトライ（最大 2 回、指数バックオフ + jitter） in service-a | 1 回目失敗 → 即リトライ。FAULT_RATE=0.5 で実効エラーレートを 25% に削減 |

> **注意**: CloudFront custom error response はステータスコードを 200 に変換できるが、やりすぎると監視やクライアントの挙動が壊れる。ポートフォリオでは「ユーザー向け劣化表示」として扱い、`origin 5xx は引き続き CloudWatch で観測可能` であることを明示する。

**合格基準**

| メトリクス | 閾値 | 計測元 |
|---|---|---|
| origin エラーレート | ≥ 30%（障害が注入されていること確認） | ALB `HTTPCode_Target_5XX_Count / RequestCount` |
| auto-stopper 発動タイミング | ≤ 5 分（`WINDOW_MINUTES=5` サイクル内） | DynamoDB `status = "stopped"` タイムスタンプ |
| Slack 通知 | auto-stopper 発動と同時 | Slack webhook 受信ログ |
| CloudFront fallback 配信 | 5xx に対して fallback が返ること | CloudFront `5xxErrorRate` vs `TotalErrorRate` の乖離 |

---

### 5. network_latency

**目標**：service-b に 500ms 注入されても **service-a の観測 P95 ≤ 200ms** を維持する

| レイヤー | 実装 | 効果 |
|---|---|---|
| ☁️ | Envoy sidecar（service-a Pod に追加） timeout: 200ms | service-b の 500ms レイテンシを 200ms でカット。service-b の実際のレイテンシは変わらない |
| ☁️ | Envoy circuit breaker（連続 5 回タイムアウトで open） | CB open 後は downstream に転送せず即 503 を返す → P95 → 0ms（fallback） |
| 🖥️ | stale cache（TTL: 30s）in service-a `/aggregate/{id}` | タイムアウト時は前回の成功レスポンスをキャッシュから返す。**キャッシュヒット時のみ** ≤ 10ms |

> **注意**: stale cache の P95 ≤ 10ms はキャッシュが温まった後のみ。初回リクエスト・キャッシュ切れ時は Envoy timeout（≤ 200ms）か fallback error になる。service-b の実際のレイテンシは 500ms のまま。service-a の観測レイテンシが改善していることをダッシュボードで可視化することが目的。

**合格基準**

| メトリクス | 閾値 | 計測元 |
|---|---|---|
| service-b P95 レイテンシ | ≈ 500ms（障害確認） | ALB `TargetResponseTime` p95 |
| service-a P95 レイテンシ（Envoy 経由） | ≤ 200ms | EMF カスタムメトリクス `AggregateDurationMs` p95 |
| circuit breaker 発動後の P95 | ≤ 10ms（fallback 返却） | 同上 |
| stale cache ヒット時の P95 | ≤ 10ms | 同上 |

---

## 実験評価 Lambda（experiment_evaluator）の設計

### トリガー

DynamoDB Stream → `status` が `"completed"` または `"stopped"` に変化したとき起動

### 判定ロジック

```python
# 実験タイプごとのチェック項目
CHECKS = {
    "pod_kill": [
        ("error_rate_during_kill", "≤ 0.01"),
        ("recovery_time_seconds", "≤ 60"),
        ("auto_stopper_fired", "== False"),
    ],
    "cpu_stress": [
        ("p95_latency_ms", "≤ 1000"),
        ("error_rate", "≤ 0.05"),
        ("auto_stopper_fired", "== False"),
    ],
    "memory_stress": [
        ("p95_latency_ms", "≤ 1000"),
        ("error_rate", "≤ 0.05"),
        ("oomkill_recovery_seconds", "≤ 60"),
        ("auto_stopper_fired", "== False"),
    ],
    "http_error_inject": [
        ("origin_error_rate", "≥ 0.30"),      # 障害確認
        ("auto_stopper_fired", "== True"),     # 安全装置が機能したこと
        ("auto_stopper_latency_seconds", "≤ 300"),
    ],
    "network_latency": [
        ("service_b_p95_ms", "≥ 450"),        # 障害確認
        ("service_a_p95_ms", "≤ 200"),        # Envoy timeout が機能
        ("auto_stopper_fired", "== False"),
    ],
}
```

### 出力スキーマ（DynamoDB に追記）

```json
{
  "experiment_id": "...",
  "evaluation_result": "pass | fail",
  "evaluation_details": {
    "error_rate": 0.004,
    "p95_latency_ms": 180,
    "auto_stopper_fired": false,
    "checks": [
      {"name": "error_rate_during_kill", "value": 0.004, "threshold": "≤ 0.01", "pass": true}
    ]
  },
  "evaluated_at": "2026-05-14T..."
}
```

---

## sli_calculator.py の拡張

現状: エラーレートのみ収集、service-b のみ対象

追加:
- **ALB `TargetResponseTime` の P95** を `get_metric_statistics` の ExtendedStatistics で収集
- **SERVICES を service-a まで拡張**（auto_stopper.py との不一致解消）
- DynamoDB SLI テーブルに `latency_p95_ms` カラムを追加

---

## ダッシュボード構成（可視化）

| パネル | メトリクス | 目的 |
|---|---|---|
| service-b P95 | ALB `TargetResponseTime` p95 | 注入された障害の確認 |
| service-a P95 | EMF `AggregateDurationMs` p95 | Envoy/cache による軽減の確認 |
| 5xx error rate | ALB `HTTPCode_Target_5XX_Count / RequestCount` | SLO 違反の監視 |
| fallback count | service-a EMF `FallbackCount` | degrade gracefully の実績 |
| circuit state | service-a EMF `CircuitBreakerState` | 0=closed, 1=open |

## Consequences

- service-a に `/aggregate/{id}` エンドポイント（service-b 呼び出し + stale cache）を追加する必要がある
- Envoy sidecar を service-a Pod に追加するため k8s マニフェストと Terraform が変更される
- `experiment_evaluator` Lambda が追加されることで Lambda 関数が 4 本になる
- sli_calculator.py の SERVICES 拡張時、service-a の ALB target group が別途存在しない場合は SLI 計算がスキップされることを考慮すること
- CloudFront custom error response の HTTP ステータスコードは 503（Service Unavailable）を使う。200 への変換は監視・SEO・クライアントを壊すリスクがある
