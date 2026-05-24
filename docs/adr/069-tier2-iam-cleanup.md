# ADR 069 - Tier 2 IAM 整理：未使用権限削除・リソーススコープ縮小・ドキュメント同期

- Status: Accepted
- Date: 2026-05-24

## Context

コードベース調査により、chaos-agent の IAM ポリシー（`iam.tf`）に以下の問題を確認した。

1. **未使用権限が3つ存在**
   - `fis:GetExperimentTemplate`（FISTemplateRead）
   - `fis:CreateExperimentTemplate`（FISTemplateCreate）
   - `fis:DeleteExperimentTemplate`（FISTemplateDelete）
   - `agent.py` を全文検索した結果、これらの API 呼び出しは一切存在しない。
   - FIS テンプレートは Terraform で管理されており、agent は `StartExperiment` / `StopExperiment` / `GetExperiment` のみを使用する。

2. **DynamoDB リソースが `chaos-*` ワイルドカード**
   - agent が実際に読み書きするのは `experiment_history` テーブル（`chaos-platform-experiment-history`）のみ。
   - `chaos-*` は `chaos-sli`・`chaos-slo` テーブルにも Scan 権限を与えてしまう。

3. **`iam-design.md` §2 と実装の乖離**
   - ドキュメントには `dynamodb:Scan` が記載されていないが実装には存在する（`_scan_pending` で使用）。
   - ドキュメントには FISTemplate 権限の記述がなく、実装との対応が不明瞭。

## Decision

1. `iam.tf` から `FISTemplateRead`・`FISTemplateCreate`・`FISTemplateDelete` ステートメントを削除する。
2. DynamoDB の Resource を `chaos-*` から `aws_dynamodb_table.experiment_history.arn` に絞る。
3. `iam-design.md` §2 を実装と一致するよう更新する。

## Rationale

### 未使用権限の削除

最小権限の原則（Principle of Least Privilege）に従い、コードで使われていない権限は攻撃面を広げるだけなので削除する。

FISTemplateCreate/Delete は将来の「動的テンプレート管理」を想定して追加されたと推測されるが、
その設計は採用されておらず（FIS テンプレートは Terraform 管理）、ADR にも記録がない。

### DynamoDB スコープの縮小

`chaos-*` を残すと、将来テーブルを追加したとき chaos-agent が意図せずアクセスできてしまう。
テーブル ARN を直接参照することで、新規テーブルは明示的に権限追加が必要になる。

`dynamodb:Scan` は `_scan_pending()`（pending 実験の一覧取得）で必要なため残す。

### iam-design.md の同期

IAM 設計書と実装が乖離していると、セキュリティレビューや監査時に意図が読めない。
「設計書 = 実装」の状態を維持することで、権限追加時のレビューが機能する。

## Consequences

- `terraform apply` で chaos-agent-policy が更新される（権限が減る方向のため安全）
- 将来 FIS テンプレートを動的に管理する場合は、その時点で権限を追加して ADR に記録する
- DynamoDB の ARN 参照により、テーブル名変更時に `iam.tf` も自動追従する
