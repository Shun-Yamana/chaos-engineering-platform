# ADR 057 - JMeter + Envoy + FIS による 10 実験マトリクス設計

- Status: Accepted
- Date: 2026-05-23

## Context

4 サービストポロジー（a→{b→c, d}）に対してカオス実験を設計するにあたり、以下の課題があった。

1. 既存の `TrafficGenerator`（30 秒おき 1 req）は負荷がほぼゼロで、Pod Kill しても回復が容易すぎる
2. 障害注入ツールが FIS と Envoy に分散しており、それぞれの適切な役割分担が未定義だった
3. `network_latency` を FIS（`aws:eks:pod-network-latency`）で実装していたが、これは Pod の全通信を遅くする粗い注入であり、実務で最も頻出する「特定依存サービスの応答劣化」を再現できていなかった

## Decision

JMeter・Envoy・FIS の役割を明確に分離し、通常トラフィック × 高負荷トラフィック × 5 障害種別 の 10 実験マトリクスを設計する。フェーズ 1 として通常トラフィック下の 5 実験から着手する。

### ツール役割分担

| ツール | 役割 |
|---|---|
| JMeter | ベーストラフィック確立（通常: ~20 req/s、高負荷: 100+ req/s）+ レイテンシ・429 の限界測定 |
| FIS | インフラ層の障害（Pod Kill / CPU 枯渇 / Memory 枯渇） |
| Envoy | アプリ層の障害（特定サービス間の遅延注入 / HTTP エラー注入） |

### 10 実験マトリクス

| | pod_kill | cpu_stress | memory_stress | network_latency | http_error_inject |
|---|---|---|---|---|---|
| **通常トラフィック** | ① FIS | ② FIS | ③ FIS | ④ Envoy | ⑤ Envoy |
| **高負荷トラフィック** | ⑥ FIS | ⑦ FIS | ⑧ FIS | ⑨ Envoy | ⑩ Envoy |

フェーズ 1: ①〜⑤（通常トラフィック下の 5 実験）

## Rationale

### FIS network_latency を外した理由

`aws:eks:pod-network-latency` は Pod の全送受信通信に遅延を注入する。これは b→c だけでなく b→d や外部 API への通信も同時に遅くするため、「c だけ遅い状態で b がどう振る舞うか」という仮説を単独では検証できない。実務で最も発生しやすい障害は特定依存サービスの応答劣化であり、FIS の粒度では再現が困難。

### Envoy delay filter を採用した理由

Envoy の delay fault filter は `envoy-service-b-egress` 等のルート単位で遅延を注入できるため、b→c パスのみに絞った遅延が可能。既存の abort filter（`http_error_inject`）と同一 ConfigMap に共存でき、実装追加コストは小さい。

### JMeter をベーストラフィックに採用した理由

`TrafficGenerator` は 1 req/30s であり、Pod Kill 時に in-flight リクエストがほぼ存在しない。実本番では Pod 障害は常時トラフィックがある状態で発生するため、JMeter で事前にトラフィックを確立してから FIS・Envoy 実験を実行することで、実践的な耐障害性検証が可能になる。

## 実験別影響範囲詳細

凡例:
- **注入経路**: 障害を注入する箇所
- **service-b 状態**: service-b の動作への直接影響
- **service-a SLO**: service-a（集約エンドポイント）の SLO（P99 < 500ms / エラー率 < 1%）維持可否
- **CloudWatch アラーム**: `service-a-error-rate` / `service-a-latency` アラームの発火有無
- **Envoy 挙動**: outlier_detection / circuit_breaker の動作
- **HPA / PDB**: オートスケールや Pod 中断制限の関与
- **X-Ray**: 分散トレースへの記録内容
- **emergency_stop**: 緊急停止が発動する条件と復旧手段

---

### b×5 実験（service-b 攻撃 → SLO 違反・カスケード障害の検証）

**① pod_kill — service-b Pod 強制削除（FIS）**

| 観点 | 詳細 |
|---|---|
| 注入経路 | FIS `aws:eks:pod-network-disruption` で service-b Pod を削除 |
| service-b 状態 | Pod が消滅 → Kubernetes が再スケジュール（Rolling Update / PDB で同時停止 1 Pod に制限） |
| service-a SLO | 再スケジュール完了（〜30s）まで a→b 呼び出しがタイムアウト → **SLO 違反** |
| CloudWatch アラーム | `service-a-error-rate` 発火（エラー率急上昇） |
| Envoy 挙動 | a 側 Envoy: 連続タイムアウト 5 回で service-b endpoint を `outlier_detection` により 30s eject → circuit_breaker が pending_requests 超過で 503 即返し |
| HPA / PDB | PDB `minAvailable=1` により同時削除は 1 Pod まで。HPA は負荷変化なしのため反応しない |
| X-Ray | service-a → service-b セグメントに `fault=true`・`EXPERIMENT_ID` アノテーション記録 |
| emergency_stop | CloudWatch アラーム → DynamoDB `emergency_stop=true` → agent が FIS 実験を中断し rollout restart で回復 |

---

**② cpu_stress — service-b CPU 80% 固定（FIS）**

| 観点 | 詳細 |
|---|---|
| 注入経路 | FIS `aws:eks:pod-cpu-stress` で service-b Pod の CPU を 80% に固定 |
| service-b 状態 | レスポンスタイム増大（処理スロットル）。Pod は生存しているため readinessProbe は通る場合あり |
| service-a SLO | a→b 呼び出しが 200ms タイムアウト（Envoy route timeout）に抵触 → **SLO 違反**（P99 劣化・エラー率上昇） |
| CloudWatch アラーム | `service-a-latency` 発火（P99 急上昇）、続いて `service-a-error-rate` 発火 |
| Envoy 挙動 | タイムアウト増加 → outlier_detection が consecutive_local_origin_failure 5 回で eject。circuit_breaker の max_requests で追加リクエストを 503 返し |
| HPA / PDB | CPU 使用率急上昇 → HPA がスケールアウトをトリガー。スケールアウト完了まで SLO 違反継続 |
| X-Ray | service-b セグメントに高レイテンシ記録・`EXPERIMENT_ID` アノテーション |
| emergency_stop | アラーム発火 → FIS 停止 → CPU 正常化。HPA がスケールイン |

---

**③ memory_stress — service-b メモリ枯渇（FIS）**

| 観点 | 詳細 |
|---|---|
| 注入経路 | FIS `aws:eks:pod-memory-stress` で service-b Pod のメモリを上限まで消費 |
| service-b 状態 | GC 圧力増加 → レスポンス遅延。OOMKill に至ると Pod 再起動 |
| service-a SLO | OOMKill 発生時は ① pod_kill 相当の影響。OOMKill 前は ② cpu_stress 相当の遅延劣化。**SLO 違反** |
| CloudWatch アラーム | `service-a-latency` または `service-a-error-rate` 発火（OOMKill タイミング次第） |
| Envoy 挙動 | OOMKill 前: タイムアウト増加 → outlier_detection eject。OOMKill 後: ① と同じ endpoint 消滅フロー |
| HPA / PDB | メモリ使用率はデフォルト HPA メトリクス外のため、スケールアウトは発動しない場合あり。PDB は OOMKill による強制削除も 1 Pod 制限 |
| X-Ray | OOMKill 発生時に service-b セグメントが途中で切断された trace として記録 |
| emergency_stop | アラーム発火 → FIS 停止でメモリ解放。OOMKill 後は Kubernetes が自動再起動 |

---

**④ network_latency — a→b パスに遅延注入（Envoy）**

| 観点 | 詳細 |
|---|---|
| 注入経路 | service-a 側 Envoy (`envoy-service-b-egress`) の delay filter で a→b に指定 ms の遅延を付加 |
| service-b 状態 | service-b 自体は正常動作。受信リクエストが遅れて届くだけ |
| service-a SLO | a の `/aggregate` レスポンスタイムが注入遅延分増加 → 200ms タイムアウト超過で **SLO 違反** |
| CloudWatch アラーム | `service-a-latency` 発火（P99 急上昇） |
| Envoy 挙動 | delay filter が全リクエストに一律遅延を付加。route timeout 200ms を超えるとタイムアウトエラーになり、outlier_detection が eject を開始 |
| HPA / PDB | Envoy は sidecar のため Pod 自体は正常。HPA・PDB 共に関与しない |
| X-Ray | a→b セグメントに `throttle=true` または `fault=true`・高レイテンシ記録 |
| emergency_stop | アラーム発火 → agent が `_remove_envoy_delay` で delay filter の numerator を 0 に戻して即時復旧（scale-to-0 不要） |

---

**⑤ http_error_inject — a→b パスに 5xx 注入（Envoy）**

| 観点 | 詳細 |
|---|---|
| 注入経路 | service-a 側 Envoy (`envoy-service-b-egress`) の abort filter で a→b 全リクエストに HTTP 500 を返す |
| service-b 状態 | service-b 自体は正常動作。Envoy が手前で 500 を返すため service-b には届かない |
| service-a SLO | a→b 全呼び出しが 500 → `/aggregate` が 5xx → **SLO 違反**（エラー率 100% に近づく） |
| CloudWatch アラーム | `service-a-error-rate` 発火（即時・急峻な上昇） |
| Envoy 挙動 | abort filter が upstream への接続前に 500 を返す。outlier_detection は upstream 障害ではないため動作しないが、circuit_breaker の max_requests は消費される |
| HPA / PDB | Envoy sidecar レベルの障害のため Pod は正常稼働。HPA・PDB 共に関与しない |
| X-Ray | a→b セグメントに `error=true` HTTP 500 記録。service-b へのリクエストは trace に出現しない |
| emergency_stop | アラーム発火 → agent が service-a Envoy を scale-to-0（`_rollout_restart`）して abort filter を無効化 → rollout で Envoy 再起動・numerator を 0 に戻して復旧 |

---

### c×5 実験（service-c 攻撃 → graceful degradation の検証）

**⑥ pod_kill — service-c Pod 強制削除（FIS）**

| 観点 | 詳細 |
|---|---|
| 注入経路 | FIS で service-c Pod を削除 |
| service-b 状態 | b→c 呼び出しがタイムアウト（500ms）。service-b は `reviews=null` でフォールバックし 200 を返す |
| service-a SLO | service-d（クリティカル）は影響なし。service-c（非クリティカル）の欠落は a の SLO に影響しない → **SLO 維持** |
| CloudWatch アラーム | **発火しない**（service-a のエラー率・レイテンシは正常範囲内） |
| Envoy 挙動 | b 側 Envoy: 連続タイムアウト 5 回で service-c endpoint を outlier_detection により 30s eject → 以降は即座にタイムアウトエラーとしてフォールバック処理 |
| HPA / PDB | service-c は再スケジュール。service-b・a は HPA スケールアウト不要 |
| X-Ray | b→c セグメントに `fault=true`・b の fallback パスが記録 |
| emergency_stop | アラーム不発火のため発動しない。実験は正常完了 → graceful degradation が証明される |

---

**⑦ cpu_stress — service-c CPU 80% 固定（FIS）**

| 観点 | 詳細 |
|---|---|
| 注入経路 | FIS で service-c Pod の CPU を 80% に固定 |
| service-b 状態 | b→c 呼び出しが 500ms タイムアウトに抵触。フォールバックで `reviews=null` の 200 返却 |
| service-a SLO | クリティカルパス（a→d）は無影響 → **SLO 維持** |
| CloudWatch アラーム | **発火しない** |
| Envoy 挙動 | b 側 Envoy の outlier_detection がタイムアウト連続で eject → circuit_breaker がリクエストを即遮断し、b のフォールバックが高速に動作 |
| HPA / PDB | service-c の CPU 上昇で HPA がスケールアウト → スケールアウト後は c が応答回復するが b はフォールバック継続 |
| X-Ray | b→c セグメントに高レイテンシ・`EXPERIMENT_ID` 記録 |
| emergency_stop | 不発動。正常完了 |

---

**⑧ memory_stress — service-c メモリ枯渇（FIS）**

| 観点 | 詳細 |
|---|---|
| 注入経路 | FIS で service-c Pod のメモリを枯渇 |
| service-b 状態 | OOMKill 発生時は ⑥ pod_kill 相当。OOMKill 前は ⑦ cpu_stress 相当の遅延でフォールバック |
| service-a SLO | クリティカルパス無影響 → **SLO 維持** |
| CloudWatch アラーム | **発火しない** |
| Envoy 挙動 | ⑥ / ⑦ と同様。outlier_detection が eject し b のフォールバックが動作 |
| HPA / PDB | メモリ系 HPA が未設定の場合はスケールアウト不発動。OOMKill → Kubernetes 自動再起動 |
| X-Ray | OOMKill 時に b→c trace が途中切断として記録 |
| emergency_stop | 不発動。正常完了 |

---

**⑨ network_latency — b→c パスに遅延注入（Envoy）**

| 観点 | 詳細 |
|---|---|
| 注入経路 | service-b 側 Envoy (`envoy-service-c-egress`) の delay filter で b→c に指定 ms の遅延を付加 |
| service-b 状態 | b→c 呼び出しが 500ms タイムアウトに抵触 → タイムアウトエラーとして `reviews=null` フォールバック |
| service-a SLO | フォールバックによりエラー伝播しない → **SLO 維持** |
| CloudWatch アラーム | **発火しない** |
| Envoy 挙動 | delay filter が b→c のみに遅延付加。route timeout 500ms 超過でタイムアウト。a→b パスは無影響（a 側 Envoy は正常） |
| HPA / PDB | 関与しない（sidecar レベルの障害） |
| X-Ray | b→c セグメントに高レイテンシ・`throttle=true` 記録。a→b セグメントは正常 |
| emergency_stop | 不発動。agent が `_remove_envoy_delay` で delay filter を 0 に戻して実験終了 |

---

**⑩ http_error_inject — b→c パスに 5xx 注入（Envoy）**

| 観点 | 詳細 |
|---|---|
| 注入経路 | service-b 側 Envoy (`envoy-service-c-egress`) の abort filter で b→c 全リクエストに HTTP 500 を返す |
| service-b 状態 | b→c が全件 500 → service-b はフォールバックで `reviews=null` の 200 を返す |
| service-a SLO | フォールバックによりエラー伝播しない → **SLO 維持** |
| CloudWatch アラーム | **発火しない** |
| Envoy 挙動 | abort filter が service-c への接続前に 500 を返す。b のフォールバックが即座に動作するため b→a のレイテンシはほぼ増加しない |
| HPA / PDB | 関与しない |
| X-Ray | b→c セグメントに `error=true` HTTP 500 記録。service-c へのリクエストは trace に出現しない |
| emergency_stop | 不発動。agent が abort filter numerator を 0 に戻して実験終了 |

---

## Consequences

- Envoy ConfigMap に delay filter を追加する実装が必要（`envoy-service-b-egress` の拡張）
- `agent.py` に `_patch_envoy_delay` / `_remove_envoy_delay` メソッドを追加する
- `_NETWORK_LATENCY_TEMPLATE_ENV`（FIS ベース）は network_latency では使用しなくなるが、将来の全通信遅延テストのために残置する
- フェーズ 2（高負荷 × 5 実験）は通常トラフィック実験の結果を見て着手判断する
- 緊急停止機構（CloudWatch アラーム → DynamoDB emergency_stop）はアプリ層障害にも有効であり、Envoy 障害の stop 手段として追加実装は不要
