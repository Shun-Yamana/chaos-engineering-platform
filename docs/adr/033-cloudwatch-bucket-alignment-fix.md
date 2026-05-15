# ADR 033 - SLI Calculator CloudWatch バケット境界ずれの修正

- Status: Accepted
- Date: 2026-05-16

## Context

SLI calculator Lambda は EventBridge により毎分トリガーされるが、実行タイミングは `:07` 秒前後になる。
`WINDOW_MINUTES=1` の場合、クエリ窓は `[T-60s, T]` であり、CloudWatch の 1 分バケット（`:00` 区切り）と常に約 7 秒ずれる。

たとえば T=16:25:07 の場合:
- クエリ窓: [16:24:07, 16:25:07]
- バケット 16:24:00 のタイムスタンプ（16:24:00）は start_time（16:24:07）より前 → **除外**
- バケット 16:25:00 は将来データ → **空**

結果、毎回 0 データポイントになり `error_rate=0.0`・`latency_p95_ms=null` が続いた。
実験中に FAULT_RATE=0.5 で 5xx が多発していても SLO 違反が検知されなかった。

## Decision

`end_time` を分境界（`:00`）に切り捨てた `end_aligned` を基準にし、`period=60` 固定・`window_minutes+1` 分遡ってクエリし、全 datapoint を合算する。

## Rationale

### `period=window_minutes*60` のまま窓を広げる案を外した理由

period を大きくすると 1 バケットの精度が落ちる。短時間のスパイクが平均化されて SLO 違反を見逃すリスクがある。

### end_aligned + period=60 固定 + 全合算を選んだ理由

- バケット境界に揃えることで確実に最低 `window_minutes` 個の完成バケットを取得できる
- `period=60` を固定することで 1 分粒度の精度を維持
- `datapoints[0]` だけ見る実装では複数バケットが返った場合にカウントが抜けるため、`sum(dp["Sum"] for dp in datapoints)` に変更
- コード変更は `get_error_rate` / `get_latency_p95_ms` の 2 関数のみ、Lambda 再デプロイだけで適用可能

## Consequences

- クエリ窓が実質 `window_minutes+1` 分になるため、直近 1 分のスパイクが最大 1 分遅れて SLI に反映される（許容範囲）
- CloudWatch に遅延投入されたデータ（実験終了後の 5xx）が次の窓に混入する場合がある。今回は `error_rate=1.0` が実験終了後に 1 サイクル出た。SLO 違反の誤検知リスクとして認識する
