import os
import json
import uuid
import boto3
import logging
from datetime import datetime, timezone, timedelta
from decimal import Decimal

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
lambda_client = boto3.client("lambda")

EXPERIMENT_TABLE = os.environ["EXPERIMENT_TABLE"]
SLO_TABLE = os.environ["SLO_TABLE"]
CHAOS_AGENT_FUNCTION = os.environ.get("CHAOS_AGENT_FUNCTION", "")


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, cls=DecimalEncoder),
    }


def start_experiment(body: dict) -> dict:
    required = ["name", "target", "fault", "slo"]
    for field in required:
        if field not in body:
            return response(400, {"error": f"missing field: {field}"})

    experiment_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc).isoformat()
    expires_at = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp())

    item = {
        "experiment_id": experiment_id,
        "started_at": started_at,
        "name": body["name"],
        "target_service": body["target"]["service"],
        "namespace": body["target"].get("namespace", "default"),
        "fault_type": body["fault"]["type"],
        "duration_seconds": int(body["fault"].get("duration", 60)),
        "error_rate_threshold": str(body["slo"].get("error_rate_threshold", 0.05)),
        "burn_rate_threshold": str(body["slo"].get("burn_rate_threshold", 2.0)),
        "status": "pending",
        "expires_at": expires_at,
    }

    table = dynamodb.Table(EXPERIMENT_TABLE)
    table.put_item(Item=item)

    if CHAOS_AGENT_FUNCTION:
        lambda_client.invoke(
            FunctionName=CHAOS_AGENT_FUNCTION,
            InvocationType="Event",
            Payload=json.dumps({"experiment_id": experiment_id, "action": "run"}),
        )
        table.update_item(
            Key={"experiment_id": experiment_id, "started_at": started_at},
            UpdateExpression="SET #st = :running",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={":running": "running"},
        )
        item["status"] = "running"

    logger.info(json.dumps({"action": "experiment_started", "experiment_id": experiment_id}))
    return response(201, {"experiment_id": experiment_id, "status": item["status"]})


def stop_experiment(experiment_id: str, reason: str = "manual") -> dict:
    table = dynamodb.Table(EXPERIMENT_TABLE)
    resp = table.scan(
        FilterExpression="experiment_id = :id",
        ExpressionAttributeValues={":id": experiment_id},
    )
    items = resp.get("Items", [])
    if not items:
        return response(404, {"error": "experiment not found"})

    item = items[0]
    if item.get("status") not in ("pending", "running"):
        return response(409, {"error": f"experiment is not stoppable (status={item.get('status')})"})

    now = datetime.now(timezone.utc).isoformat()
    table.update_item(
        Key={"experiment_id": item["experiment_id"], "started_at": item["started_at"]},
        UpdateExpression="SET #st = :stopped, stopped_at = :now, stop_reason = :reason",
        ExpressionAttributeNames={"#st": "status"},
        ExpressionAttributeValues={
            ":stopped": "stopped",
            ":now": now,
            ":reason": reason,
        },
    )

    logger.info(json.dumps({"action": "experiment_stopped", "experiment_id": experiment_id}))
    return response(200, {"experiment_id": experiment_id, "status": "stopped"})


def list_experiments(service: str | None = None, limit: int = 20) -> dict:
    table = dynamodb.Table(EXPERIMENT_TABLE)

    if service:
        resp = table.scan(
            FilterExpression="target_service = :s",
            ExpressionAttributeValues={":s": service},
        )
    else:
        resp = table.scan()

    items = sorted(
        resp.get("Items", []),
        key=lambda x: x.get("started_at", ""),
        reverse=True,
    )[:limit]

    return response(200, {"experiments": items, "count": len(items)})


def get_experiment(experiment_id: str) -> dict:
    table = dynamodb.Table(EXPERIMENT_TABLE)
    resp = table.scan(
        FilterExpression="experiment_id = :id",
        ExpressionAttributeValues={":id": experiment_id},
    )
    items = resp.get("Items", [])
    if not items:
        return response(404, {"error": "experiment not found"})
    return response(200, items[0])


def handler(event, context):
    method = event.get("httpMethod", "")
    path = event.get("path", "")
    body = {}

    if event.get("body"):
        try:
            body = json.loads(event["body"])
        except json.JSONDecodeError:
            return response(400, {"error": "invalid JSON body"})

    query = event.get("queryStringParameters") or {}

    logger.info(json.dumps({"method": method, "path": path}))

    if method == "POST" and path == "/experiments":
        return start_experiment(body)

    if method == "DELETE" and path.startswith("/experiments/"):
        experiment_id = path.split("/")[-1]
        reason = body.get("reason", "manual")
        return stop_experiment(experiment_id, reason)

    if method == "GET" and path == "/experiments":
        service = query.get("service")
        limit = int(query.get("limit", "20"))
        return list_experiments(service, limit)

    if method == "GET" and path.startswith("/experiments/"):
        experiment_id = path.split("/")[-1]
        return get_experiment(experiment_id)

    return response(404, {"error": "not found"})
