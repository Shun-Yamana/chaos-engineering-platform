# ADR 080 - node_failure 実験設計：FIS aws:ec2:terminate-instances による EC2 ノード終了

- Status: Accepted
- Date: 2026-05-26

## Context

ADR 079 で FIS は「AWSインフラ層の障害注入」を担うと決定した。K8sコンテナ層の実験（pod_kill/cpu_stress/memory_stress/network_latency）は Chaos Mesh に移行したため、FIS のテンプレートを整理し、FIS が本来最も得意とする EC2 レベルの障害実験を新規追加する。

EC2 ノード終了は「Kubernetes のセルフヒーリング能力」を検証する実験であり、コンテナ内部を攻める Chaos Mesh 実験とは異なる層を対象とする。

- **テスト対象の防衛**: PDB（PodDisruptionBudget）、Deployment replicas、NodeGroup の ASG によるノード自動補充
- **想定シナリオ**: ノード障害 → Pod が Pending → 残存ノードまたは新ノードに再スケジュール → サービス回復

FIS アクション `aws:ec2:terminate-instances` はノードの EC2 インスタンスを実際に終了させる。`aws:eks:pod-delete`（PodChaos）とは異なり、OS レベルでインスタンスが消えるため kubelet も停止する。これにより Pod の graceful deletion ではなく強制退去が発生し、より本番に近い障害シナリオを再現できる。

## Decision

**FIS `aws:ec2:terminate-instances` で EKS ノード 1 台を終了させる実験テンプレートを追加する。**

合格基準：

| 指標 | 基準 | 根拠 |
|---|---|---|
| Pod 再スケジュール完了時間 | 60 秒以内 | replicas=2・PDB=1 で 1 Pod は常に稼働。新ノードへの配置は通常 30〜60s |
| 実験中のエラーレート P95 | < 10% | ノード喪失直後に一時的なエラーが発生するが PDB が保護 |
| 5 分後のエラーレート | 正常範囲（< 1%） | サービスが完全回復していることの確認 |
| ASG によるノード補充 | 10 分以内 | NodeGroup min_size=2 の ASG がノードを自動補充 |

ターゲット: `eks:cluster-name` タグで EKS ノードを絞り込み、`COUNT(1)` で 1 台のみ終了。2 台同時終了すると PDB の保護が成立しないため単台に限定する。

## Rationale

### なぜ COUNT(1) か

NodeGroup は min_size=2 / desired_size=2 で運用している。2 台同時終了すると全 Pod が Pending になりサービスダウンとなる。COUNT(1) により「1 台落ちても残り 1 台でサービスが維持できるか」を検証する。

### なぜ aws:ec2:terminate-instances か

- `aws:eks:pod-delete`（Chaos Mesh PodChaos）は Pod 単位の障害。ノード障害と Pod 障害では kubelet の停止有無・Pod の退去方法が異なる
- EC2 インスタンス終了によりノードが NotReady になる経路はより現実的な障害シナリオ
- FIS が AWS リソース（EC2）を直接操作するユースケースとして最も自然

### 既存 FIS テンプレートの整理

ADR 079 に基づき以下を削除する：
- `aws_fis_experiment_template.network_latency` → Chaos Mesh NetworkChaos
- `aws_fis_experiment_template.pod_kill` → Chaos Mesh PodChaos
- `aws_fis_experiment_template.cpu_stress` → Chaos Mesh StressChaos
- `aws_fis_experiment_template.memory_stress` → Chaos Mesh StressChaos
- `aws_ssm_document.memory_stress` → 不要

FIS 実行ロール IAM ポリシーも合わせて整理する：
- 削除: `EC2Modify`（network_latency 用 NIC 操作）、`SSMMemoryStress`
- 追加: `ec2:TerminateInstances`（node_failure 用）

## Consequences

- FIS の役割が「AWSインフラ層（EC2）の障害注入」として明確に定義される
- Chaos Mesh（コンテナ層）と FIS（インフラ層）の責任境界が一致する
- `FIS_TEMPLATE_NODE_FAILURE` 環境変数を chaos-agent の Deployment に追加する必要がある
- experiment_evaluator.py に `node_failure` の評価ロジックを追加する必要がある（別 ADR 予定）
