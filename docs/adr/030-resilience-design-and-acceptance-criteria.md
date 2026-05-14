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

## 合格基準の設計思想

auto-stopper は SLO 違反時の **安全装置**（緊急停止）であり、レジリエンスの品質を測るものではない。合格基準は以下の 2 フェーズで評価する。

```
Phase A — 吸収（Absorption）: 障害注入中、SLO を守り続けられたか
Phase B — 回復（TTR: Time To Recovery）: 障害除去後、どのスピードでベースラインに戻ったか
```

**ベースライン定義**（実験開始前 5 分間の平均値で測定）

| メトリクス | ベースライン閾値 |
|---|---|
| エラーレート | ≤ 0.5% |
| P95 レイテンシ（ALB） | ≤ 100ms |
| service-a P95（EMF） | ≤ 50ms |

---

## レジリエンスパターンと合格基準

### 1. pod_kill

**目標**：1台 Kill されてもトラフィックを無断絶処理し、冗長性を速やかに復元する

| レイヤー | 実装 | 効果 |
|---|---|---|
| ☁️ | `replicas: 2`（service-a / service-b 両方） | Kill されてももう 1 台がトラフィックを処理。ダウンタイムなし |
| ☁️ | `PodDisruptionBudget` `minAvailable: 1` | rolling update / node drain 時に同時停止を防ぐ（強制 Kill は対象外） |
| ☁️ | `readinessProbe`（`/health`, 5s interval, 2 threshold） | 起動中の Pod にトラフィックが来ないよう制御 |
| ☁️ | `startupProbe`（Fargate 起動 30〜60s を考慮） | 起動完了前のトラフィック流入を防ぐ |
| ☁️ | `topologySpreadConstraints` maxSkew: 1 | AZ 分散。AZ 障害でも全 Pod 同時死にを防ぐ |
| 🖥️ | SIGTERM ハンドリング（graceful shutdown 5s） | Kill 時に処理中リクエストを完了してから終了 |

**Phase A — 吸収**

| メトリクス | 閾値 | 根拠 |
|---|---|---|
| Kill 直後 60s のエラーレート | **≤ 1%** | replicas=2 で他 Pod が全トラフィックを処理。ALB deregistration window（10s）での一時スパイクのみ許容 |

**Phase B — TTR（障害除去 = Kill イベント発生時点から計測）**

| メトリクス | 閾値 | 根拠 |
|---|---|---|
| エラーレートがベースライン（≤ 0.5%）に戻るまで | **≤ 60s** | Fargate 起動 + readiness probe 通過の上限 |
| Pod 数が 2 に回復するまで | **≤ 90s** | 冗長性の復元確認 |

*安全網*: auto-stopper 不発動（エラーレートが SLO の 5% を超えたら設計の失敗）

---

### 2. cpu_stress

**目標**：CPU 高負荷中も P95 ≤ 1000ms を維持し、除去後 60s 以内にベースラインへ回復する

| レイヤー | 実装 | 効果 |
|---|---|---|
| ☁️ | `resources.limits.cpu: 256m` 明示 | バーナースレッドの暴走に上限。CPU throttling で他 Pod への影響を隔離 |
| ☁️ | HPA `targetCPUUtilizationPercentage: 60` | CPU 高負荷 → Pod スケールアウト → 1 Pod あたりのリクエスト密度が下がり P95 緩和 |

> **注意**: env var パッチで注入された CPU ストレスはスケールアウトした新 Pod にも適用される。それでも Pod 数増加で 1 Pod 当たりのリクエスト密度が下がるため P95 改善は見込める。CPU ベース HPA が最初のデモ。リクエスト数ベースは KEDA で発展段階。

**Phase A — 吸収**

| メトリクス | 閾値 | 根拠 |
|---|---|---|
| P95 レイテンシ（実験全体） | **≤ 1000ms** | ADR 006 定義。timeout=3s の 1/3 以内 |
| エラーレート | **≤ 5%** | SLO |
| HPA スケールアウト | **5 分以内に Pod 数 ≥ 2** | HPA デフォルト評価間隔 15s × stabilization |

**Phase B — TTR（CPU_STRESS 環境変数除去時点から計測）**

| メトリクス | 閾値 | 根拠 |
|---|---|---|
| P95 がベースライン（≤ 100ms）に戻るまで | **≤ 60s** | CPU バーナー停止は即時。Pod 再起動は不要なため短い |

*安全網*: auto-stopper 不発動

---

### 3. memory_stress

**目標**：OOMKill を Pod 内に封じ込め、60s 以内に自己回復する

| レイヤー | 実装 | 効果 |
|---|---|---|
| ☁️ | `resources.limits.memory: 256Mi` 明示 | メモリ暴走時に OOMKill させ Pod 内に閉じ込める。未設定だとノード全体に影響が広がる可能性 |
| ☁️ | `readinessProbe` | OOMKill 後の再起動中 Pod にトラフィックを流さない |
| ☁️ | `livenessProbe` | OOMKill ではなく **ハングや復旧不能状態** を検知して再起動（OOMKill 自体は kubelet が処理） |

> **注意**: HPA（メモリベース）はスケールアウトした Pod も同じストレスを受けるため根本解決にならない。「緩和策」として位置づける。

**Phase A — 吸収**

| メトリクス | 閾値 | 根拠 |
|---|---|---|
| 高負荷中のエラーレート | **≤ 5%** | SLO |
| P95 レイテンシ（高負荷中） | **≤ 1000ms** | ADR 007 定義 |
| OOMKill 後の回復時間（エラーレートが 0% に戻るまで） | **≤ 60s** | readiness probe 設定から算出（ADR 007 と同一） |

**Phase B — TTR（MEMORY_STRESS_MB 除去時点から計測）**

| メトリクス | 閾値 | 根拠 |
|---|---|---|
| メモリ使用量がベースライン（≤ 100MB RSS）に戻るまで | **≤ 90s** | 除去後は rolling restart が発生。Fargate 起動込みの上限 |
| エラーレートがベースラインに戻るまで | **≤ 90s** | 同上 |

*安全網*: auto-stopper 不発動

---

### 4. http_error_inject

**目標**：FAULT_RATE=0.5 の注入で auto-stopper が 5 分以内に発動し、ユーザーには branded fallback を返す。除去後 2 分以内にエラーレートがゼロに戻る

| レイヤー | 実装 | 効果 |
|---|---|---|
| ☁️ | CloudFront custom error response（5xx → 静的フォールバック、HTTP 503） | ユーザーへの raw 5xx を遮断。origin 5xx は ALB / CloudWatch で監視継続 |
| ☁️ | CloudFront origin failover（primary: ALB, secondary: S3 fallback） | ALB 全断時の最終防衛 |
| 🖥️ | リトライ（最大 2 回、指数バックオフ + jitter） in service-a | 実効エラーレートを 25%（0.5²）に削減 |

> **注意**: CloudFront custom error は HTTP 200 に変換しない（503 を維持する）。監視・クライアント挙動・SEO を壊すリスクがある。

> **この実験のみ auto-stopper 発動が主要合格条件**：SLO 違反の観測 → 自動停止のパイプラインが正常動作することそのものを検証する実験であるため。

**Phase A — 吸収**

| メトリクス | 閾値 | 根拠 |
|---|---|---|
| origin エラーレート（障害確認） | **≥ 30%** | FAULT_RATE=0.5 が正しく機能していること |
| auto-stopper 発動タイミング | **≤ 5 分** | `WINDOW_MINUTES=5` サイクル内（ADR 008 定義） |
| CloudFront fallback 配信確認 | 5xx に対し fallback が返ること | CloudFront `5xxErrorRate` vs `TotalErrorRate` の乖離で検証 |

**Phase B — TTR（FAULT_RATE 除去時点から計測）**

| メトリクス | 閾値 | 根拠 |
|---|---|---|
| エラーレートがベースライン（≤ 0.5%）に戻るまで | **≤ 2 分** | SLI 計算ウィンドウ（1 分）× 2 サイクル以内 |

---

### 5. network_latency

**目標**：service-b に 500ms 注入されても service-a の観測 P95 ≤ 200ms を維持し、除去後 90s 以内にベースラインへ回復する

| レイヤー | 実装 | 効果 |
|---|---|---|
| ☁️ | Envoy sidecar（service-a Pod）timeout: 200ms | service-b の 500ms を 200ms でカット。service-b の実際のレイテンシは変わらない |
| ☁️ | Envoy circuit breaker（連続 5 回タイムアウトで open） | CB open 後は downstream に転送せず即 fallback → P95 ≤ 10ms |
| 🖥️ | stale cache（TTL: 30s）in service-a `/aggregate/{id}` | タイムアウト時に前回成功レスポンスを返す。**キャッシュヒット時のみ** ≤ 10ms |

> service-b の実際の P95 は 500ms のまま。service-a の観測 P95 との乖離をダッシュボードで可視化することが目的。stale cache ≤ 10ms はキャッシュヒット時のみ（初回・TTL 切れ後は Envoy timeout ≤ 200ms）。

**Phase A — 吸収**

| メトリクス | 閾値 | 根拠 |
|---|---|---|
| service-b P95（障害確認） | **≥ 450ms** | LATENCY_MS=500 が正しく機能していること |
| service-a P95（Envoy 経由） | **≤ 200ms** | Envoy timeout |
| circuit breaker 発動タイミング | **≤ 30s** | 5 回タイムアウト × 200ms + jitter |
| CB open 後の service-a P95 | **≤ 10ms** | fallback 即返し |

**Phase B — TTR（LATENCY_MS 除去時点から計測）**

| メトリクス | 閾値 | 根拠 |
|---|---|---|
| CB が half-open → closed に遷移するまで | **≤ 60s** | CB recovery timeout（30s）+ 成功リクエスト確認まで |
| service-a P95 がベースライン（≤ 50ms）に戻るまで | **≤ 90s** | CB close + stale cache TTL 切れ（30s）まで込み |

*安全網*: auto-stopper 不発動

---

## 実験評価 Lambda（experiment_evaluator）の設計

### 合格基準のまとめ（evaluator が参照する定義）

| 実験 | Phase A（吸収） | Phase B（TTR） |
|---|---|---|
| pod_kill | error_rate ≤ 1% | error_rate → baseline within **60s**、pod_count = 2 within **90s** |
| cpu_stress | P95 ≤ 1000ms、error_rate ≤ 5%、HPA pods ≥ 2 in 5min | P95 → baseline within **60s** |
| memory_stress | error_rate ≤ 5%、P95 ≤ 1000ms、OOMKill recovery ≤ 60s | error_rate + memory → baseline within **90s** |
| http_error_inject | origin error ≥ 30%、auto-stopper ≤ 5min、fallback served | error_rate → baseline within **2min** |
| network_latency | service-b P95 ≥ 450ms、service-a P95 ≤ 200ms、CB opens ≤ 30s | service-a P95 → baseline within **90s** |

### トリガーと待機

```
DynamoDB Stream (status → "completed" / "stopped")
  └→ Lambda 起動
       └→ MAX(TTR) + 3分（CloudWatch メトリクス遅延バッファ）待機後に評価
```

最大 TTR は 90s。CloudWatch バッファ 3 分を加えた **約 5 分後** にメトリクスを取得・判定する。
Lambda のタイムアウトは 10 分に設定する（デフォルト 3 分から変更）。

### 判定ロジック

```python
CRITERIA = {
    "pod_kill": {
        "absorption": [
            Criterion("error_rate_during_fault", op="<=", threshold=0.01,
                      window="fault_start_at → fault_end_at"),
        ],
        "recovery": [
            Criterion("error_rate", op="<=", threshold=0.005,
                      ttr_limit_seconds=60, window="fault_end_at → fault_end_at+90s"),
            Criterion("pod_count", op=">=", threshold=2,
                      ttr_limit_seconds=90, window="fault_end_at → fault_end_at+120s"),
        ],
    },
    "cpu_stress": {
        "absorption": [
            Criterion("p95_latency_ms", op="<=", threshold=1000,
                      window="fault_start_at → fault_end_at"),
            Criterion("error_rate",     op="<=", threshold=0.05,
                      window="fault_start_at → fault_end_at"),
        ],
        "recovery": [
            Criterion("p95_latency_ms", op="<=", threshold=100,
                      ttr_limit_seconds=60, window="fault_end_at → fault_end_at+120s"),
        ],
    },
    "memory_stress": {
        "absorption": [
            Criterion("error_rate",    op="<=", threshold=0.05,
                      window="fault_start_at → fault_end_at"),
            Criterion("p95_latency_ms", op="<=", threshold=1000,
                      window="fault_start_at → fault_end_at"),
        ],
        "recovery": [
            Criterion("error_rate", op="<=", threshold=0.005,
                      ttr_limit_seconds=90, window="fault_end_at → fault_end_at+150s"),
            Criterion("memory_rss_mb", op="<=", threshold=100,
                      ttr_limit_seconds=90, window="fault_end_at → fault_end_at+150s"),
        ],
    },
    "http_error_inject": {
        "absorption": [
            Criterion("origin_error_rate",         op=">=", threshold=0.30,
                      window="fault_start_at → fault_end_at"),
            Criterion("auto_stopper_latency_s",    op="<=", threshold=300,
                      window="fault_start_at → fault_end_at"),
        ],
        "recovery": [
            Criterion("error_rate", op="<=", threshold=0.005,
                      ttr_limit_seconds=120, window="fault_end_at → fault_end_at+180s"),
        ],
    },
    "network_latency": {
        "absorption": [
            Criterion("service_b_p95_ms", op=">=", threshold=450,
                      window="fault_start_at → fault_end_at"),
            Criterion("service_a_p95_ms", op="<=", threshold=200,
                      window="fault_start_at → fault_end_at"),
        ],
        "recovery": [
            Criterion("service_a_p95_ms", op="<=", threshold=50,
                      ttr_limit_seconds=90, window="fault_end_at → fault_end_at+150s"),
        ],
    },
}
```

### 出力スキーマ（DynamoDB に追記）

```json
{
  "experiment_id": "...",
  "evaluation_result": "pass | fail",
  "evaluation_details": {
    "phase_a_absorption": [
      {"criterion": "error_rate_during_fault", "value": 0.004, "threshold": "<=0.01", "pass": true}
    ],
    "phase_b_recovery": [
      {"criterion": "error_rate", "ttr_actual_seconds": 18, "ttr_limit_seconds": 60, "pass": true},
      {"criterion": "pod_count",  "ttr_actual_seconds": 52, "ttr_limit_seconds": 90, "pass": true}
    ],
    "safety_net": {
      "auto_stopper_fired": false,
      "expected": false,
      "pass": true
    }
  },
  "evaluated_at": "2026-05-14T..."
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
