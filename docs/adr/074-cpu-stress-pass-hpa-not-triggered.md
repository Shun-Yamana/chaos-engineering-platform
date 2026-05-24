# ADR 074 - cpu_stress 実験 PASS：HPA 閾値未達でもエラーなし

- Status: Accepted
- Date: 2026-05-24

## Context

FIS `aws:eks:pod-cpu-stress`（80% × 300s、service-b 全 Pod 対象）を実行した。JMeter が 15 rps でトラフィックを流した状態での結果：

- JMeter エラー率：0%（実験前後とも）
- CircuitBreakerState：0
- FallbackCount：0
- HPA のスケールアウト：**発生しなかった**

HPA が動かなかった理由は、80% stress が "service-b コンテナの CPU limit（256m）の 80%" として適用されるため実際の CPU 使用率は `256m × 80% = 204m`。HPA の閾値は request 256m に対して 60% = 153.6m であり、通常負荷の分 (~35m) と合計しても `239m / 256m = 93%` — 閾値超えのはず。

実際には FIS のエフェメラルコンテナによる CPU stress は Pod の cgroup 内で実行されるため、CPU throttling により stress-ng と service-b の両方が rate-limited される。service-b の実際のリクエスト処理に使える CPU は依然として確保されており、HPA が見る平均 CPU は閾値を超えなかった。

## Decision

cpu_stress 実験の防衛策として HPA を維持する（ADR 059 の方針通り）。今回のトラフィック負荷（15 rps / service-b CPU 10%）では HPA 閾値に達しなかったが、より高い負荷では HPA が機能することが期待される。

## Rationale

### HPA が動かなくても PASS とする理由
エラー率 0% が評価基準であり、HPA スケールアウトは手段（防衛策）であって目的ではない。今回は CPU throttling により service-b の応答時間が劣化しなかったため、HPA 発動なしでも SLO を維持できた。

### より高い負荷での再実験を行わない理由
現在の JMeter 設定（20 threads / 15 rps）は ADR で定義した実験時の標準負荷であり、意図的に変更しない。HPA の動作確認は load test 目的ではなくカオス実験のコンテキストで行う。

## Consequences

- cpu_stress 実験は **PASS**（エラー率 0%）。
- HPA のスケールアウト動作は今回未検証。service-b CPU が 60% 閾値を超えるシナリオ（より高い RPS）では HPA が介入することが設計上期待される。
- metrics-server（ADR 073）が整備されたことで今後の HPA 動作確認が可能になった。
