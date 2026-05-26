# ADR 087 - NetworkPolicy カバレッジチェックを PR 段階に前倒し

- Status: Proposed
- Date: 2026-05-27

## Context

ADR 086 で導入した NetworkPolicy カバレッジチェック（`scripts/check-networkpolicy-coverage.py`）は
deploy.yml の `push: main` でのみ実行される。
そのため、NetworkPolicy が欠落した `k8s/service-*.yaml` を含む PR は
**main へ merge されるまで検出されない**。

PR レビュー中に気づく仕組みがなく、merge 後に CI が fail してロールバックが必要になりうる。

## Decision

`pull_request` トリガーを持つ専用ワークフロー（例: `.github/workflows/lint.yml`）を作成し、
`scripts/check-networkpolicy-coverage.py` を PR 段階でも実行する。

```yaml
on:
  pull_request:
    branches: [main]
    paths: ['k8s/**']

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - name: Verify NetworkPolicy coverage
        run: python3 scripts/check-networkpolicy-coverage.py
```

## Rationale

現状は「merge して初めて気づく」。PR チェックにすることで「merge 前に気づく」になり、
ロールバック不要・レビュアーへのフィードバックが速くなる。
スクリプト自体は既に存在するため、ワークフロー追加のみで実現できる。

## Consequences

- `k8s/service-*.yaml` を変更する PR で自動的にチェックが走る
- NetworkPolicy なしの PR は Status Check が fail し、branch protection で merge をブロックできる
- AWS 認証・EKS 接続が不要な pure Python チェックなので CI コストはほぼゼロ
