# ADR 047 - memory_stress 実験: MEMORY_STRESS_MB を 150 に下げ、評価基準を ALB エラー率のみに変更

- Status: Accepted
- Date: 2026-05-17

## Context

memory_stress 実験（`MEMORY_STRESS_MB=256`）を実行したところ、evaluator が想定外の PASS を返したが、
実際にはストレスが serving traffic に届いていなかった。

### 問題 1: OOMKill によるローリングアップデートの無限ループ

service-b のメモリ上限は `resources.limits.memory: 256Mi`。
`MEMORY_STRESS_MB=256` を注入すると、Python + ランタイム分のオーバーヘッドで 256Mi を超え、
新しい Pod が即座に OOMKill される。
その結果、ローリングアップデートは新 Pod を起動 → OOMKill → 再起動 を繰り返し、
**旧 Pod（ストレスなし）が serving traffic を処理し続ける**。
evaluator はメトリクスを見て「エラーなし」と判断し PASS するが、
ストレス自体が加わっていないため実験として無意味な結果になっていた。

### 問題 2: `peak_memory_mb` 評価が service-a を計測していた

`get_emf_max("ProcessMemoryMB", "service-a", ...)` は service-a の EMF を参照する。
service-b は `ProcessMemoryMB` EMF を出力しておらず、
service-b のメモリ量はそもそも CloudWatch 経由では取得できない。
peak_memory_mb 基準は常に「データなし」になり評価が不安定だった。

## Decision

1. `chaos/agent.py` の `memory_stress_mb` デフォルト値を **256 → 150** に変更する
2. `experiment_evaluator.py` の memory_stress 評価から `peak_memory_mb` / `memory_recovered_mb` 基準を削除し、
   **ALB エラー率のみ**で評価する
3. Phase B の計測開始を `fault_end + 90s` にオフセットする（ローリングアップデート完了待ち）

## Rationale

### なぜ 150MB か

- service-b の limits: 256Mi。150MB なら Python/ランタイムのオーバーヘッドを足しても 200MB 前後に収まり、OOMKill を回避できる。
- 同時に `bytearray(150MB)` は十分な圧迫量であり、GC 負荷・スラッシング・リクエスト処理遅延として observable。
- 200MB も候補だったが limits の 3/4 未満という安全マージンを優先して 150MB とした。

### なぜ peak_memory_mb 評価を外すか

- service-b は `ProcessMemoryMB` EMF を出力しない（ADR 030 の設計外）。
- service-a の ProcessMemoryMB を誤って参照していたため、この基準は memory_stress の合否と無関係だった。
- 代わりに ALB エラー率で「メモリ圧迫下でもサービスが応答を返せているか」を評価する。

### Phase B オフセット 90s

MEMORY_STRESS_MB 環境変数を削除すると rolling update が走り、完了まで約 90s かかる。
`fault_end` 直後の計測は旧 Pod（ストレスあり）と新 Pod（正常）が混在するため、
90s 後から 240s 後の窓で回復を判定する。

## Consequences

- memory_stress 実験は 150MB の確実なメモリ圧迫を serving traffic に届ける
- evaluator は ALB エラー率 (`error_rate <= 0.05` フォールト中、`<= 0.005` 回復後) のみで PASS/FAIL を判定
- `peak_memory_mb` 基準の削除により「service-a メモリが増えたらいつも FAIL」という誤判定が解消される
