# ADR 083 - AZアイソレーション実験：FIS aws:network:disrupt-connectivity によるサブネット遮断

- Status: Accepted
- Date: 2026-05-26

## Context

このポートフォリオは ap-northeast-1a / 1c の2AZ構成で運用している。EKSノード(desired_size=2)はAWSが自動的に1ノード/AZで分散配置するため、実質的にマルチAZ構成になっている。

ADR 080 の `node_failure`（EC2ノード終了）との違いを整理する。

| | node_failure | az_isolation |
|---|---|---|
| FISアクション | `aws:ec2:terminate-instances` | `aws:network:disrupt-connectivity` |
| ノードの状態 | EC2が削除される | EC2は生存、ネットワークのみ断 |
| ALBの検知 | ターゲットが消える | health check 失敗（10秒） |
| K8sの検知 | Node削除 | Node NotReady（node-monitor-grace-period: 40s） |
| 回復方法 | ASGによるノード新規作成 | ネットワーク回復でそのまま復旧 |
| 回復時間 | 数分（ノード起動時間を含む） | ~30秒（health check通過後に再登録） |

AZアイソレーションは「ネットワーク障害」シナリオであり、EC2ノード終了とは本質的に異なる経路を検証する。

FIS `aws:network:disrupt-connectivity` はターゲットサブネットのルートテーブルを操作してネットワークを遮断する。ALBはPod IP直接ターゲット（target-type: ip）のため、1aサブネットのPodが10秒でunhealthyと判定されALBルーティングから除外される。

## Decision

**FIS `aws:network:disrupt-connectivity` で ap-northeast-1a のプライベートサブネットを2分間遮断し、ALBとKubernetesが独立して障害を検知・回復する能力を検証する。**

合格基準：

| フェーズ | 指標 | 基準 | 根拠 |
|---|---|---|---|
| Phase A | error_rate_during_fault | ≤ 10% | ALBが10秒でAZ-1aのPodをderegister、1c側に集約 |
| Phase B | error_rate_recovery | ≤ 1% | ネットワーク回復後30秒以内 |
| Phase B | TTR limit | 30秒 | ネットワーク復旧 → health check通過 → 再登録（ノード新規作成不要） |

## Rationale

### node_failure と az_isolation を両方実施する理由

- `node_failure`: EC2が終了 → Podが強制退去 → ASGが新規ノード起動 → 数分かけて回復
- `az_isolation`: ネットワーク断 → ALBが10秒で切り離し → ネットワーク回復後に即座に復旧

「ノード障害」と「ネットワーク障害」は防衛経路が異なる。両方PASSすることで「AWSインフラ層の2種類の障害パターンに対して耐性がある」ことを証明できる。

### target-type: ip が鍵

ALBのtarget-type がnodeではなくipのため、ALBは各PodのIPをhealth checkする。AZ-1aのネットワークが遮断されると、ALBはそのPodのIPへのhealth checkが失敗したことを直接検知してderegisterする。NodePort経由の場合はノードが別AZにいてもルーティングできるが、ip直接の場合はAZ-1aのPodへのルートがなくなる。

## Consequences

- FIS実行ロールに `ec2:DescribeSubnets`、`ec2:DescribeRouteTables`、`ec2:CreateRoute`、`ec2:DeleteRoute`、`ec2:ReplaceRoute` が必要
- `az_isolation` fault_type を chaos-agent に追加する
- `FIS_TEMPLATE_AZ_ISOLATION` 環境変数を chaos-agent に追加する
- node_failure と az_isolation を両方実施することで「AWSインフラ層の障害耐性」を2パターンで証明する
