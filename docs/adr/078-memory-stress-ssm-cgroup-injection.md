# ADR 078 - memory_stress SKIP 解消：SSM 経由でノード上から service-b の cgroup にメモリストレスを注入

- Status: Accepted
- Date: 2026-05-26

## Context

ADR 075 で `aws:eks:pod-memory-stress` が service-b の OOMKill に届かないことが判明し、memory_stress 実験を SKIP とした。失敗の構造は以下の通り：

```
FIS ephemeral sidecar（独自 cgroup）
  → stress-ng が自分の cgroup 内でメモリ確保
  → sidecar の cgroup limit に達して sidecar 自身が OOMKill
  → service-b の cgroup は無傷のまま（0% エラー・0 restarts）
```

0% エラーは「service-b の防衛が機能した」のではなく「service-b が一切攻撃されなかった」結果であり、実際のメモリ枯渇時の service-b の挙動は未検証のままだった。

SKIP 解消のため以下の 3 案を検討した。

**案 A：アプリ層 `MEMORY_STRESS_MB` env var**

service-b のコードに env var を読んでメモリを確保するロジックを追加する。問題は 2 つ：env var 変更にローリングアップデートが必要で、そのアップデート自体が pod_kill 相当の disruption になる（OOMKill を試す前に Pod が置き換わる）。また ADR 055 で「障害ロジックをアプリコードに混入させない」と明示的に嫌った設計。

**案 B：Kubernetes cgroup バイパス（`shareProcessNamespace` + `cgroup.procs` 書き込み）**

Pod に `shareProcessNamespace: true` を設定して FIS sidecar から service-b の PID を見えるようにし、cgroup.procs に stress プロセスの PID を書き込んで service-b の cgroup 内で実行する。問題は OOM killer がどのプロセスを kill するかカーネル任せであり service-b が確実に死ぬ保証がない。また Kubernetes が cgroup 分離を設けた設計思想に逆行する。

**案 C：FIS `aws:ssm:send-command` でホストから cgroup 直接操作**

FIS は `aws:ssm:send-command` アクションで EC2 ノード上の SSM ドキュメントを実行できる。ホスト（root）として動作するため、service-b コンテナの cgroup パスを特定して stress プロセスをその cgroup 内で直接起動できる。

ノードの AMI タイプ確認：`ami_type` が未指定のため EKS デフォルトの `AL2_x86_64`（Amazon Linux 2）が使われており、cgroup v1 が動作する。cgroup v1 ではパス形式が `grep memory /proc/$PID/cgroup` で取得可能であり、SSM ドキュメントの実装が単純になる。

SSM を使うには `AmazonSSMManagedInstanceCore` がノードロールに必要だが未設定だったため `terraform/modules/eks/node_group.tf` に追加した。

## Decision

**案 C（SSM 経由ホストから cgroup 直接操作）を採用する。**

SSM ドキュメントで以下を実行し、service-b の cgroup 内からメモリを確保して本物の OOMKill を発生させる。

```bash
# service-b コンテナの PID を取得
CONTAINER_ID=$(crictl ps --name service-b -q | head -1)
PID=$(crictl inspect $CONTAINER_ID | jq -r '.info.pid')

# cgroup v1 パスを取得
CGROUP=$(grep memory /proc/$PID/cgroup | cut -d: -f3)

# service-b の cgroup 内でメモリ確保（自プロセスを cgroup に移動してから実行）
(
  echo $$ > /sys/fs/cgroup/memory${CGROUP}/cgroup.procs
  python3 -c "
data = []
while True:
    data.append(b'x' * 1024 * 1024)
    import time; time.sleep(0.05)
"
)
```

stress プロセスが service-b の cgroup（limit: 256Mi）内で動くため、limit 超過時に kubelet が service-b コンテナを OOMKill する。

## Rationale

### 案 A（アプリ層 env var）を外した理由

ローリングアップデートが先に走るため「メモリ枯渇による OOMKill」ではなく「Pod 置き換えによる disruption」を試すことになる。また production コードへの障害ロジック混入は ADR 055 で明示的に避けた設計。

### 案 B（Kubernetes cgroup バイパス）を外した理由

OOM killer がどのプロセスを kill するかはカーネルが決定するため service-b が確実に OOMKill される保証がない。Kubernetes の cgroup 分離設計に逆行する実装であり、FIS 実験テンプレートとも相性が悪い。

### 案 C（SSM）を選んだ理由

- EC2 ホスト（root）として動作するため cgroup 階層を直接操作できる
- stress プロセスを service-b の cgroup に正確に配置でき、OOMKill のターゲットが service-b になる
- アプリコードへの変更不要。FIS の `aws:ssm:send-command` アクションの範囲内で実現できる
- cgroup v1（AL2 デフォルト）によりパス取得・書き込みの実装が単純

## Consequences

- `AmazonSSMManagedInstanceCore` をノードロールに追加済み（`node_group.tf`）。次回 `terraform apply` で反映される。
- FIS 実験テンプレートに `aws:ssm:send-command` アクションを追加する必要がある。
- FIS 実行ロールに SSM 関連の IAM 権限（`ssm:SendCommand` 等）を追加する必要がある。
- `ami_type` を `AL2_x86_64` として明示的に Terraform に記載することを推奨する（現在は暗黙デフォルト。AL2023 に変更すると cgroup v2 になり SSM ドキュメントのパス取得ロジックが変わる）。
- 実験結果（PASS/FAIL）は別 ADR に記録する。
