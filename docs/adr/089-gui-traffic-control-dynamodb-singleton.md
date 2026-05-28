# ADR 089 - GUI からトラフィック ON/OFF を制御する機能

- Status: Accepted
- Date: 2026-05-29

## Context

カオス実験の評価には実トラフィックが継続的に流れている状態が必要だが、
`kubectl scale` を手動で叩く運用は手間で再現性も低い。
実験開始前後でトラフィックをワンクリックで切り替えられる仕組みが求められた。

## Decision

DynamoDB テーブルに `TRAFFIC_CONTROL` シングルトン項目を置き、
chaos-agent が 10 秒ごとにポーリングして `traffic-generator` Deployment をスケールする。
フロントエンドはヘッダーのトグル UI から PUT /traffic を叩く。

```
React toggle → API Gateway (JWT) → Lambda put_traffic
    → DynamoDB {running: bool}
chaos-agent poll → read DynamoDB → kubectl scale (0 or 1)
```

## Rationale

### FIS 実験と同じ DynamoDB テーブルを使う理由
新たなストレージを追加せず既存の `chaos-platform-experiment-history` テーブルに
PK=`TRAFFIC_CONTROL` / SK=`SINGLETON` で同居させることで、IAM・コードの変更を最小化できる。

### chaos-agent が直接スケールする理由
別途 Lambda/EventBridge を作るより、すでにクラスタ内権限を持つ chaos-agent に
RBAC `deployments/scale: [get, patch]` を付与する方が簡潔。
ただし chaos-agent の RBAC 変更後は `kubectl apply` がプレースホルダ image を上書きするため
`kubectl set image` で ECR タグを再設定する手順が必要（ADR 088 参照）。

## Consequences

- フロントエンドからトラフィックの on/off をワンクリックで制御できる
- chaos-agent のポーリング周期 10s 分の遅延が生じる
- `TRAFFIC_CONTROL` 項目を誤削除すると traffic-generator が停止したまま再起動されない
