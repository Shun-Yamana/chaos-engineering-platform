# ADR 085 - service-c/d NetworkPolicy 欠落による DNS ブロッキング

- Status: Accepted
- Date: 2026-05-27

## Context

service-c と service-d が `startupProbe` を通過できず CrashLoopBackOff に陥り続けた。
`kubectl describe pod` では `context deadline exceeded (Client.Timeout exceeded while awaiting headers)` と
`connection refused` が交互に現れ、`/health` は 200 を返しているにもかかわらず rollout がタイムアウトした。

調査の過程で以下を発見した：
- `gethostbyname("cloudwatch-agent.amazon-cloudwatch")` を service-c pod 内から実行すると **20 秒ブロック後に失敗** した
- 同じ呼び出しを service-a pod から実行すると 8 ms で成功した
- `default-deny-all` NetworkPolicy が default namespace の全 pod の Ingress/Egress を拒否していた
- service-a/b には個別 NetworkPolicy が存在したが、**service-c/d には存在しなかった**

X-Ray SDK は `xray_recorder.end_segment()` 内で `socket.sendto(data, ("hostname", port))` を呼び出す。
Python の `sendto` はホスト名を渡すと DNS lookup を同期的に実行し asyncio イベントループをブロックする。
DNS port 53 UDP/TCP が NetworkPolicy で遮断されると DNS lookup は最大 20 秒タイムアウトし、
`/health` の HTTP レスポンスがその後に返るため `startupProbe.timeoutSeconds: 1` を超過した。

## Decision

service-c および service-d に NetworkPolicy を追加する。
また service-c/d の main.py で起動時に一度だけホスト名を IP に解決して固定する。
さらに startupProbe/readinessProbe/livenessProbe の `timeoutSeconds` を 1s → 5s に緩和する。

## Rationale

### NetworkPolicy 欠落の原因
初期設計時に `④ service-c` と `⑤ service-d` のポリシーが network-policy.yaml に記載されなかった。
service-a/b のポリシーを追加した際に番号を飛ばした（コメントは ①②③ の次が ⑤⑥）。

### DNS pre-resolve を採用した理由
NetworkPolicy 修正だけでも起動時の最初の DNS lookup は成功するが、
`socket.sendto(hostname, port)` はリクエストのたびに DNS lookup を行う（glibc はキャッシュなし）。
モジュール読み込み時に IP に解決して固定することでリクエストごとの DNS ブロッキングを排除した。

### timeoutSeconds: 5 を採用した理由
OTel init container（Java/Node.js/dotnet）が 3 秒程度実行されてから main container が起動するため、
1s では X-Ray 起動処理が間に合わない。5s は防衛的な余裕として妥当。

## Consequences

- service-c は service-b からの ingress (port 8000) + DNS egress + X-Ray UDP 2000 + HTTPS 443 が許可される
- service-d は service-a からの ingress (port 8000) + DNS egress + X-Ray UDP 2000 + HTTPS 443 が許可される
- 最小権限原則を維持しつつ必要なトラフィックのみ許可する構成となった
- startupProbe の猶予時間が増えたことでロールアウト時間はやや長くなる（最大 100s → 100s 変わらず、ただし timeout が 1s→5s なので各周期の待ち時間が増える）
