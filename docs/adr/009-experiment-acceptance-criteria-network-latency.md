# ADR 009 - 実験合格基準：network_latency

- Status: Accepted
- Date: 2026-04-30

## Context

network_latency 実験の合格基準を定義するにあたり、「auto-stopper が止める」ことを望ましい結果として定義してよいかを議論した。

auto-stopper は実験の安全装置（ブラストラジウス制御）であり、本番障害時には存在しない。カスケード障害への対応として本当に必要なのは、サービス自身が自己防衛する仕組みである。

## Decision

network_latency 実験の合格基準を以下の通り定める。

| 指標 | 望ましい結果 |
|------|------------|
| service-b の挙動 | timeout（3.0s）内で 504 を返す（無限待ちしない） |
| SLI 検知 | エラーレートの上昇を記録する |
| auto-stopper | SLO 違反を検知して実験を安全に停止する（安全装置として機能） |
| 遅延除去後 | エラーレートが正常値に戻る |

## Rationale

### auto-stopper の停止を「望ましい結果」ではなく「安全装置」と位置づけた理由

auto-stopper が止めるのは chaos platform が注入した人工的な障害であり、本番環境でカスケード障害が起きたときに同様の仕組みは存在しない。

```
auto-stopper が止める = 実験（人工障害）を止める
Circuit Breaker が止める = service-b が自律的に service-a への呼び出しを遮断する
```

本当の望ましい結果はサービス自身が自己防衛することだが、現状 service-b には Circuit Breaker が実装されていない。

### service-b の挙動基準を「無限待ちしない」に置いた理由

現状の service-b は `timeout=3.0s` を設定しており、これがカスケード障害防止の最低限の自己防衛である。遅延が timeout を超えた際に 504 を返して処理を打ち切ることで、service-b のスレッドが枯渇するのを防ぐ。

### 注入方式：AWS FIS を採用する理由

network_latency の遅延注入には `tc netem`（Linux Traffic Control）が必要であり、これには **NET_ADMIN Linux Capability** が必要となる。

当初は ADR 010 と同様に ephemeral container によるスポット注入方式を検討したが、EKS Fargate は Pod レベルで NET_ADMIN を封じているため実行不可能である（sidecar・exec・ephemeral container のいずれの方式でも回避できない）。

```
tc netem 実行 → NET_ADMIN 必要
EKS Fargate   → NET_ADMIN 使用不可（AWS がポリシーで封じている）
→ コンテナベースの注入方式はすべて使えない
```

AWS FIS（Fault Injection Service）の `aws:eks:pod-network-latency` アクションは VPC レベルでトラフィックを制御するため、コンテナの Capability に依存しない。Fargate 環境で唯一機能するネットワーク遅延注入手段である。

また、FIS によるスポット注入は Deployment spec を変更しないため、ローリングアップデートも発生しない。

**役割分担の整理：**

| 実験 | 担当 | 理由 |
|------|------|------|
| cpu_stress / memory_stress | カスタム chaos agent（ephemeral container） | FIS の CPU/Memory ストレスは EC2 ノードの SSM エージェント経由であり Fargate 非対応 |
| http_error_inject | カスタム chaos agent（環境変数パッチ） | アプリレベルの挙動は FIS の管轄外 |
| network_latency | AWS FIS | Fargate の NET_ADMIN 制約により、コンテナベースの注入が不可能 |

## Consequences

- Circuit Breaker が未実装のため、service-a へのリクエストを繰り返し続ける問題が残る。これはアーキテクチャ改善フェーズで対処する
- レイテンシ P95 の自動判定には sli_calculator.py へのレイテンシメトリクス追加が必要
- auto-stopper が正しく機能することの確認は http_error_inject 実験（ADR 008）で行う。network_latency 実験では安全装置として期待する
- `network_latency_inject` の実装は AWS FIS API 呼び出しに置き換える（現行の tc-latency sidecar 方式は廃止）
- FIS 実験テンプレートの作成と IAM ロール（FIS 実行権限）の設定が必要
