import os
import json
import boto3
import logging
from datetime import datetime, timezone, timedelta
from decimal import Decimal

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
sns = boto3.client("sns")
k8s_client = None

PROJECT_NAME = os.environ["PROJECT_NAME"]
SLI_TABLE = os.environ["SLI_TABLE"]
SLO_TABLE = os.environ["SLO_TABLE"]
EXPERIMENT_TABLE = os.environ["EXPERIMENT_TABLE"]
SNS_TOPIC_ARN = os.environ["SNS_TOPIC_ARN"]
SERVICES = ["service-a", "service-b"]


def get_latest_sli(service: str) -> dict | None:
    table = dynamodb.Table(SLI_TABLE)
    resp = table.query(
        KeyConditionExpression="service_name = :s",
        ExpressionAttributeValues={":s": service},
        ScanIndexForward=False,
        Limit=1,
    )
    items = resp.get("Items", [])
    return items[0] if items else None


def get_slo(service: str) -> dict:
    table = dynamodb.Table(SLO_TABLE)
    resp = table.get_item(Key={"service_name": service})
    return resp.get("Item", {
        "service_name": service,
        "error_rate_threshold": Decimal("0.05"),
        "burn_rate_threshold": Decimal("2.0"),
    })


def get_running_experiments(service: str) -> list:
    table = dynamodb.Table(EXPERIMENT_TABLE)
    resp = table.scan(
        FilterExpression="target_service = :s AND #st = :running",
        ExpressionAttributeNames={"#st": "status"},
        ExpressionAttributeValues={":s": service, ":running": "running"},
    )
    return resp.get("Items", [])


def stop_experiment(experiment: dict, reason: str):
    table = dynamodb.Table(EXPERIMENT_TABLE)
    now = datetime.now(timezone.utc).isoformat()

    table.update_item(
        Key={
            "experiment_id": experiment["experiment_id"],
            "started_at": experiment["started_at"],
        },
        UpdateExpression="SET #st = :stopped, stopped_at = :now, stop_reason = :reason",
        ExpressionAttributeNames={"#st": "status"},
        ExpressionAttributeValues={
            ":stopped": "stopped",
            ":now": now,
            ":reason": reason,
        },
    )
    logger.info(json.dumps({
        "action": "experiment_stopped",
        "experiment_id": experiment["experiment_id"],
        "reason": reason,
    }))


def publish_alert(service: str, sli: dict, slo: dict, experiment_id: str, reason: str):
    message = {
        "alert": "SLO_VIOLATION",
        "service": service,
        "experiment_id": experiment_id,
        "error_rate": str(sli["error_rate"]),
        "burn_rate": str(sli["burn_rate"]),
        "error_rate_threshold": str(slo["error_rate_threshold"]),
        "burn_rate_threshold": str(slo["burn_rate_threshold"]),
        "reason": reason,
        "timestamp": sli["timestamp"],
    }
    sns.publish(
        TopicArn=SNS_TOPIC_ARN,
        Subject=f"[chaos-platform] SLO violation detected: {service}",
        Message=json.dumps(message, indent=2),
    )


def handler(event, context):
    # SNS トリガー（CloudWatch Alarm 発火）の場合: ALARM 状態のみ処理し OK 回復は無視する
    if "Records" in event:
        for record in event.get("Records", []):
            if record.get("EventSource") == "aws:sns":
                try:
                    sns_message = json.loads(record["Sns"]["Message"])
                except (KeyError, json.JSONDecodeError):
                    logger.warning("Failed to parse SNS message")
                    continue

                state = sns_message.get("NewStateValue")
                if state != "ALARM":
                    logger.info(json.dumps({"action": "sns_skipped", "state": state}))
                    return {"statusCode": 200, "stopped_experiments": []}

                logger.info(json.dumps({
                    "action": "sns_alarm_received",
                    "alarm": sns_message.get("AlarmName"),
                    "reason": sns_message.get("NewStateReason"),
                }))

    # EventBridge（毎分）または SNS（Alarm）どちらのトリガーでも同じ停止ロジックを実行
    stopped = []

    for service in SERVICES:
        sli = get_latest_sli(service)
        if not sli:
            continue

        slo = get_slo(service)
        error_rate = float(sli["error_rate"])
        burn_rate = float(sli["burn_rate"])
        error_rate_threshold = float(slo["error_rate_threshold"])
        burn_rate_threshold = float(slo["burn_rate_threshold"])

        violated = error_rate > error_rate_threshold or burn_rate > burn_rate_threshold
        if not violated:
            continue

        reason = (
            f"error_rate={error_rate:.4f} > threshold={error_rate_threshold}"
            if error_rate > error_rate_threshold
            else f"burn_rate={burn_rate:.4f} > threshold={burn_rate_threshold}"
        )

        experiments = get_running_experiments(service)
        for experiment in experiments:
            stop_experiment(experiment, reason)
            publish_alert(service, sli, slo, experiment["experiment_id"], reason)
            stopped.append(experiment["experiment_id"])

    return {"statusCode": 200, "stopped_experiments": stopped}
