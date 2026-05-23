# ADR 058 - pod_kill 実験：先に定量計測してから防衛策を投入する

- Status: Accepted
- Date: 2026-05-23

## Context

① pod_kill（service-b Pod 強制削除）実験に対して、以下の防衛策候補が存在する。

- replicas を 2 → 3 に増やす
- Envoy retry を追加する（connection_failure 限定・1回）
- service-a 側で b のレスポンスをキャッシュしてフォールバック

これらを事前に全投入してから実験しても「どの対策がどれだけ効いたか」が分からない。

## Decision

**防衛策を投入する前に、現状（replicas=2）のまま FIS 実験を実行し、service-a の 5xx 発生件数を定量計測する。** その数値をベースラインとして、改善策を 1 つずつ追加して再実験し、件数がどう変化するかで効果を評価する。

## Rationale

カオスエンジニアリングの正しいサイクルは「仮定 → 計測 → 改善 → 再計測」である。改善前の数値がなければ「改善の効果」を証明できない。特にポートフォリオの文脈では、「Pod Kill 中に X 件の 5xx が発生した → replicas=3 に変更 → Y 件に減少した」という数値の変化がストーリーの根拠になる。

また、replicas=3 と Envoy retry は目的が異なる。

| 対策 | 狙い |
|---|---|
| replicas=3 | Pod 削除後の容量余裕を確保し、残 Pod が過負荷にならないようにする |
| Envoy retry | Pod 削除の瞬間に in-flight だったリクエストを救済する（1回だけ再送） |

どちらが有効かは計測結果（5xx の発生パターン）を見て判断する。再スケジュール中の容量問題が主因なら replicas 増、削除瞬間の接続断が主因なら retry が有効。

## 計測方法

| ツール | 取得する指標 |
|---|---|
| JMeter `.jtl` | リクエスト単位の成否・レイテンシ（最も直接的な 5xx 件数） |
| CloudWatch | `service-a-error-rate` の時系列グラフ（実験区間の面積＝影響総量） |
| X-Ray | 失敗した trace の span 詳細（どの層で 5xx が発生したか） |
| Envoy stats | `upstream_rq_5xx` / `upstream_rq_timeout` のカウント差分 |

## 実験シーケンス

```
1. JMeter 起動（20 req/s を service-a に流す）
2. 定常状態を 60s 確認（エラー率 0%）
3. FIS pod_kill を起動（service-b の 1 Pod を削除）
4. 300s 待機（Pod 再スケジュール + 実験終了）
5. JMeter の .jtl から実験区間の 5xx 件数を集計
6. CloudWatch / X-Ray で補完確認
```

## Consequences

- 実験①は防衛策なしで実行するため、実験中に service-a の SLO が一時的に違反する
- CloudWatch アラームが発火し emergency_stop が動く可能性があるため、実験①は emergency_stop を無効化した状態で実施するか、発動を許容したうえで 5xx 件数を計測する
- 計測結果を受けて replicas / retry / キャッシュの投入順序を ADR 059 以降で決定する
