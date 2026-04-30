# ADR 006 - 実験合格基準：cpu_stress

- Status: Accepted
- Date: 2026-04-30

## Context

cpu_stress 実験の合格基準を定義するにあたり、「サービスへの影響を最小化できたか」を判断するために、エラーレート・レイテンシ・HPA の3軸すべてを検討する必要があった。

現在のインフラ設定（確認済み）：
- service-a: cpu limits=512m、stress-ng sidecar: limits=512m（合計で CPU limits に到達しうる）
- service-b: service-a を timeout=3.0s で呼び出す
- HPA: 未設定

## Decision

cpu_stress 実験の合格基準を以下の通り定める。

| 指標 | 合格基準 | 備考 |
|------|---------|------|
| エラーレート | SLO閾値（5%）以内 | service-b の 504 が発生しないこと |
| レイテンシ（P95） | 1000ms 以内 | SLI 未実装のため今後追加が必要 |
| HPA | 不要なスケールアウトをしないこと | HPA 回復検証はスコープ外 |

## Rationale

### エラーレートの根拠

CPU スロットリングにより service-a のレスポンスが遅延し、service-b の timeout（3.0s）を超えると 504 が発生してエラーレートが上昇する。
SLO 閾値（5%）以内に収まることで「CPU 高負荷でも 3 秒以内に応答できる」ことを証明できる。

### レイテンシ P95 < 1000ms の根拠

service-b の timeout=3.0s に対して 2 秒のバッファを確保するために 1000ms を閾値とした。
ただし現状の `sli_calculator.py` はエラーレートのみを計算しており、レイテンシ SLI は未実装。この基準を自動判定するには SLI の追加実装が必要。

### HPA の合格基準を「不要なスケールアウトなし」に留めた理由

現実装の `cpu_stress_inject` は Deployment の spec に sidecar を追加する。そのため HPA がスケールアウトしても新しい Pod も同じ spec から起動し、stress-ng sidecar が乗った状態になる。結果として HPA では CPU 高負荷が解消されない。

```
HPA スケールアウト → 新 Pod も stress-ng を持つ → CPU 高負荷継続 → HPA は無効
```

HPA の回復能力を正しく検証するには、実際のリクエスト負荷（ロードジェネレーター）で CPU を上昇させる手法が必要であり、それは別の実験スコープとなる。

## Consequences

- レイテンシ P95 の自動判定には `sli_calculator.py` へのレイテンシメトリクス追加が必要
- HPA の回復検証はロードジェネレーター（k6 等）を用いた別実験として今後の拡張に残る
- cpu_stress 実験の本質的な検証内容は「CPU スロットリング下でもタイムアウトを発生させないこと」に絞られる
