# ADR 019 - ECR リポジトリ設計

- Status: Accepted
- Date: 2026-05-10

## Context

Chaos Engineering Platform は service-a / service-b / chaos-agent の3つのコンテナイメージを ECR で管理する。CI（GitHub Actions）が git SHA タグでイメージをビルド・プッシュし、EKS Fargate がそこから pull する構成。

現状の `ecr.tf` は最小構成（`scan_on_push = true` のみ）で以下の問題がある：

- `image_tag_mutability = "MUTABLE"` のため `latest` タグを後から上書きできる
- lifecycle policy がないため古いイメージが無制限に蓄積する

## Decision

`image_tag_mutability` を IMMUTABLE に変更し、lifecycle policy（untagged 1日削除・tagged 最新10枚保持）を全リポジトリに追加する。

## Rationale

### IMMUTABLE を選んだ理由

MUTABLE のままだと `latest` を別のイメージが上書きできる。CI は常に `${git_sha}` タグを付けてプッシュするため、IMMUTABLE にしても運用上の支障はない。デプロイ済みタグが書き換わることによる「どのイメージが動いているか分からない」問題を排除できる。

### lifecycle policy を追加した理由

イメージは CI が走るたびに積み上がる。untagged（中間レイヤー・ビルドキャッシュ残骸）は1日で削除し、tagged は最新10枚を残す。ポートフォリオ環境でストレージコストが膨らむのを防ぐ。

### スキップした項目

| 項目 | 外した理由 |
|------|-----------|
| KMS 暗号化 | デフォルト AES-256 (SSE-S3) で十分。KMS は追加コストあり |
| `aws_ecr_repository_policy` | 全アクセスが同一アカウント内（GitHub Actions OIDC ロール・EKS ノードロール）。クロスアカウントなし |
| ENHANCED スキャン（Inspector v2）| 有料。`scan_on_push` の BASIC スキャンで CVE 検知は担保できる |
| pull-through cache | EKS は ECR から pull するため DockerHub rate limit 対象外。CI のベースイメージ pull は頻度が低く問題なし |
| cross-region レプリケーション | シングルリージョン構成。DR 要件なし |

## Consequences

- CI は git SHA タグのみでプッシュする（`latest` タグは不要になる）
- 古い tagged イメージが10枚を超えると自動削除される。ロールバック先として保持したいイメージは別途タグ命名規則で管理する
- `force_delete = false`（デフォルト）を維持し、誤 `terraform destroy` による削除を防ぐ
