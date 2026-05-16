# ADR 041 - フロントエンドへの実験評価結果（PASS/FAIL + Phase A/B）表示

- Status: Accepted
- Date: 2026-05-16

## Context

従来のフロントエンドは `status` と `stop_reason` から推測した PASSED / BREACHED を表示していたが、
`experiment_evaluator` が実装する ADR 030 合格基準（Phase A Absorb/Contain + Phase B Recovery/TTR + Safety Net）
との対応が不明瞭で、ポートフォリオとしての説明力に欠けていた。

evaluator は実験終了後に DynamoDB の `evaluation_result`（"pass" / "fail"）と
`evaluation_details`（各 criterion の値・閾値・合否）を書き込む。
これらをフロントエンドで表示することで、カオスエンジニアリングの合格基準を直接可視化できる。

実験終了から評価完了まで最大 5 分程度かかる（CloudWatch メトリクスの収集待ち）。

## Decision

- `ExperimentDetail`: 実験終了後も `evaluation_result` が設定されるまでポーリングを継続し、
  `EvaluationCard` コンポーネントで評価結果を表示する。
- `ExperimentList`: 各実験カードに PASS / FAIL バッジを追加する。
- `api.ts`: `Experiment` 型に `evaluation_result` / `evaluation_details` / `evaluated_at` を追加する。

### 表示ステート

| 状態 | 表示 |
|---|---|
| 実験終了・評価未完了 | "Awaiting Evaluation" スピナー（5 分以内に自動評価） |
| evaluation_result = "pass" | 緑ヘッダー（✓ PASS）+ Phase A/B/Safety Net 各 criterion 行 |
| evaluation_result = "fail" | 赤ヘッダー（✕ FAIL）+ Phase A/B/Safety Net 各 criterion 行 |

各 criterion 行: criterion 名 / 測定値 / 閾値 / ✓✕– アイコン。TTR 系は実績秒数と上限秒数を併記。

## Rationale

### OutcomeCard（旧）を完全に置き換える理由
`stop_reason` からの推測表示は evaluator の判定と一致しないケースがあり、
「なぜ PASS/FAIL なのか」を説明できない。evaluator の出力をそのまま表示することで
ADR 005〜009 の合格基準との 1:1 対応を保証できる。

### ポーリングを評価完了まで継続する理由
評価は CloudWatch メトリクスの収集完了後に実行されるため、実験終了時点では未確定。
ユーザーが画面を開いたまま待てるよう、スピナーを表示しながらポーリングを継続する。

## Consequences

- 評価結果の待ち時間（最大 5 分）の間、ユーザーにスピナーが表示される。
- `evaluation_details` が null の実験（evaluator 実装前のデータ）では criterion 行が表示されないが、
  PASS/FAIL ヘッダーのみ表示され後方互換性を保つ。
- フロントエンドの ECR イメージ再ビルドと S3 への再デプロイが必要。
