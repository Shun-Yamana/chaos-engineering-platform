# ADR 090 - traffic-generator の VPC CNI ClusterIP 制限をヘッドレスサービスで回避

- Status: Accepted
- Date: 2026-05-29

## Context

traffic-generator は service-a に 20 req/s を送る Python Pod として実装した。
NetworkPolicy の egress に `podSelector: {app: service-a}` を指定したが、
全リクエストがタイムアウト (`ok=0 err=20 elapsed=60s`) になった。

調査の結果、EKS の VPC CNI (`enableNetworkPolicy: true`) は
NetworkPolicy を kube-proxy の DNAT より**前**に評価する。
このため egress の podSelector は ClusterIP 宛のパケットに一致せず、
iptables でドロップされていた。

ipBlock で Service CIDR (`172.20.0.0/16`) と VPC CIDR (`10.0.0.0/8`) を許可する
NetworkPolicy に変更しても依然タイムアウトが続いた（ClusterIP → DNAT が問題の本質のため）。

## Decision

`clusterIP: None` のヘッドレスサービス `service-a-headless` を追加し、
スクリプトで `socket.gethostbyname(headless_fqdn)` により Pod IP を直接解決して
`http.client.HTTPConnection(pod_ip, 8000)` で接続する。

NetworkPolicy の egress は ipBlock のまま残す（Pod CIDR 直接接続には ipBlock が必要）。

## Rationale

### ヘッドレスサービスを選んだ理由
DNS 解決の結果が ClusterIP ではなく Pod IP になるため、
DNAT の問題を根本から回避できる。
Envoy/istio サイドカーなし・追加コンポーネントなしで実現できる最小構成。

### podSelector egress を外した選択肢
VPC CNI が DNAT 前評価する仕様上、ClusterIP 宛トラフィックでは機能しないため採用不可。

## Consequences

- `ok=20 err=0 elapsed=0.22s` @ 20 req/s を確認
- service-a が複数 Pod に水平スケールした場合、DNS ラウンドロビンではなく
  `gethostbyname` が返す 1 つの Pod IP にしか送れない（負荷分散なし）
- ヘッドレスサービスは service-a の Deployment と selector が一致している必要がある
