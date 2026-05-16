# ADR 049 - http_error_inject Phase B 計測オフセットを 120s → 180s に延長

- Status: Accepted
- Date: 2026-05-17

## Context

http_error_inject 実験の評価で Phase B（error_rate_recovery）が繰り返し FAIL していた。

```
✕ error rate recovery  0.056 <= 0.005   TTR 30s / 180s
```

### 原因の分析

http_error_inject では SLO 違反を検知すると auto_stopper が `_emergency_recover` を起動する。

```
auto_stopper 発動 (T_stop)
  → scale to 0       (即時)
  → FAULT_RATE 除去  (即時)
  → wait 30s
  → scale back to 2  (T_stop + 30s)
  → rolling update   (~90s)
  → 回復完了          (T_stop + 120s)
```

duration=300s の実験で auto_stopper が T=300s 付近（実測 300.890s）に発動した場合、
回復完了は `fault_end + 120s` となる。

旧 Phase B 計測ウィンドウは `fault_end + 120s 〜 fault_end + 300s` だったため、
ウィンドウの先頭が rolling update 完了と重なり、
スケールアップ直後の健全性チェック確立フェーズのエラー（0.056）が混入していた。

TTR=30s は scale=0 の期間（トラフィックなし = エラー率 0）を捉えた誤検知であり、
その後のスケールアップで一時的にエラーが再発している。

## Decision

http_error_inject の Phase B 計測ウィンドウを以下に変更する。

| | 旧 | 新 |
|---|---|---|
| 開始 | fault_end + 120s | fault_end + 180s |
| 終了 | fault_end + 300s | fault_end + 360s |
| TTR 上限 | 180s | 240s |

## Rationale

- emergency recover の最悪ケース（auto_stopper が duration 直前に発動）での回復完了は `fault_end + 120s`
- `fault_end + 180s` から計測することで、rolling update 完了後 60s のバッファを確保できる
- TTR 上限も 180s → 240s に合わせて延長（window 開始に対応）

### 他の選択肢を外した理由

- **offset 120s のまま閾値を緩和**: 0.056 ≤ 0.01 などにすると「rolling update 中もエラーが少なければ PASS」になり基準の意味が薄れる
- **experiment duration を短縮（例 180s）**: auto_stopper が早期に発動するなら有効だが、発動タイミングを保証できない。evaluator が正しくあるべき。

## Consequences

- auto_stopper が duration 終了直前に発動しても Phase B が clean なウィンドウを計測できる
- window が 60s 後ろにずれるため、評価完了（Lambda 実行）が 60s 遅くなる
- 実験全体の評価所要時間: 5分バッファ + duration + 360s ≈ 15〜16 分
