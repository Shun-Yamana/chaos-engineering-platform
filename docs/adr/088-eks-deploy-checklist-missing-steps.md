# ADR 088 - EKS ローカルデプロイの定番ミス3点と対処

- Status: Accepted
- Date: 2026-05-28

## Context

CI（GitHub Actions）経由のデプロイは `deploy.yml` が手順を完全に定義しているため漏れがない。
一方、EKS を terraform destroy → apply で再構築した後にローカルから手動デプロイする場面では、
毎回同じ3点でエラーが発生することが判明した。

1. **image プレースホルダー未置換**：`k8s/*.yaml` の `SERVICE_A_IMAGE` 等が ECR URL に置換されないまま `kubectl apply` される。CI では `sed` で置換しているが、ローカルでは素の `kubectl apply` を使うため発生。
2. **`envoy-config.yaml` apply 漏れ**：`service-a`/`service-b` Pod が `envoy-service-b-egress` ConfigMap をマウントしようとして `FailedMount` で起動不能になる。apply 順序の暗黙的な依存が原因。
3. **xray-daemon に `AWS_REGION` 未設定**：EC2 メタデータエンドポイント（`169.254.169.254`）が NetworkPolicy でブロックされているため、リージョン自動取得が timeout し CrashLoopBackOff になる。

## Decision

`xray-daemonset.yaml` に `AWS_REGION: ap-northeast-1` を明示追加し、恒久修正とする。
image 置換と apply 順序はデプロイ手順として明文化する。

## Rationale

### xray-daemon を恒久修正した理由
EC2 メタデータ依存は「NetworkPolicy がある限り必ず失敗する」構造的な問題であり、
毎回の運用で回避するより manifest を修正する方が根本解決になる。

### image 置換・apply 順序をスクリプト化しなかった理由
ローカルデプロイは EKS 再構築時の一時的な作業であり、CI が本線。
スクリプト追加によるメンテナンスコストより、手順メモとして残す方が軽量。

## Consequences

- xray-daemon は `AWS_REGION` を環境変数で固定するため、リージョン変更時は manifest 更新が必要
- ローカルデプロイ時の apply 順序：`namespace` → `network-policy` → `envoy-config` → `metrics-server` → `xray-daemonset` → `pdb/hpa` → `services（sed 置換後）` → `chaos-agent（sed 置換後）`
- image 置換コマンド例：`(Get-Content k8s/service-a.yaml -Raw) -replace 'SERVICE_A_IMAGE','<ECR_URL>:<SHA>' | kubectl apply -f -`
