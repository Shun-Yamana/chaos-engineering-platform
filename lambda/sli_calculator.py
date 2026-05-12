import os
import json
import boto3
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger()
logger.setLevel(logging.INFO)

cloudwatch = boto3.client("cloudwatch")
dynamodb = boto3.resource("dynamodb")

PROJECT_NAME = os.environ["PROJECT_NAME"]
SLI_TABLE = os.environ["SLI_TABLE"]
SLO_TABLE = os.environ["SLO_TABLE"]
WINDOW_MINUTES = int(os.environ.get("WINDOW_MINUTES", "5"))
# ALB ARN suffix: app/<name>/<hex> — kubectl get ingress で取得後に設定
ALB_ARN_SUFFIX = os.environ.get("ALB_ARN_SUFFIX", "")

# service-a は ALB を持たず service-b 経由でのみ外部公開される。
# ALB メトリクスで service-b のエンドツーエンド健全性を監視し、
# service-a の障害は service-b のエラーレート上昇として観測する。
SERVICES = ["service-b"]


def get_error_rate(end_time: datetime, window_minutes: int) -> float:
    if not ALB_ARN_SUFFIX:
        logger.warning("ALB_ARN_SUFFIX not set, skipping metric query")
        return 0.0

    start_time = end_time - timedelta(minutes=window_minutes)
    period = window_minutes * 60
    dimensions = [{"Name": "LoadBalancer", "Value": ALB_ARN_SUFFIX}]

    def query_sum(metric_name: str) -> float:
        resp = cloudwatch.get_metric_statistics(
            Namespace="AWS/ApplicationELB",
            MetricName=metric_name,
            Dimensions=dimensions,
            StartTime=start_time,
            EndTime=end_time,
            Period=period,
            Statistics=["Sum"],
        )
        datapoints = resp.get("Datapoints", [])
        return datapoints[0]["Sum"] if datapoints else 0.0

    request_count = query_sum("RequestCount")
    if request_count == 0:
        return 0.0

    error_count = query_sum("HTTPCode_Target_5XX_Count")
    return error_count / request_count


def get_slo(service: str) -> dict:
    table = dynamodb.Table(SLO_TABLE)
    resp = table.get_item(Key={"service_name": service})
    return resp.get("Item", {
        "service_name": service,
        "error_rate_threshold": 0.05,
        "burn_rate_threshold": 2.0,
        "slo_target": 0.999,
    })


def calculate_burn_rate(error_rate: float, slo: dict) -> float:
    error_budget = 1.0 - float(slo["slo_target"])
    if error_budget == 0:
        return 0.0
    return error_rate / error_budget


def save_sli(service: str, error_rate: float, burn_rate: float, timestamp: str):
    table = dynamodb.Table(SLI_TABLE)
    expires_at = int((datetime.now(timezone.utc) + timedelta(days=7)).timestamp())

    table.put_item(Item={
        "service_name": service,
        "timestamp": timestamp,
        "error_rate": str(round(error_rate, 6)),
        "burn_rate": str(round(burn_rate, 6)),
        "expires_at": expires_at,
    })


def handler(event, context):
    now = datetime.now(timezone.utc)
    timestamp = now.isoformat()

    results = []
    for service in SERVICES:
        slo = get_slo(service)
        error_rate = get_error_rate(now, WINDOW_MINUTES)
        burn_rate = calculate_burn_rate(error_rate, slo)

        save_sli(service, error_rate, burn_rate, timestamp)

        results.append({
            "service": service,
            "error_rate": error_rate,
            "burn_rate": burn_rate,
            "error_rate_threshold": float(slo["error_rate_threshold"]),
            "burn_rate_threshold": float(slo["burn_rate_threshold"]),
            "slo_violated": (
                error_rate > float(slo["error_rate_threshold"])
                or burn_rate > float(slo["burn_rate_threshold"])
            ),
        })

        logger.info(json.dumps({
            "service": service,
            "error_rate": error_rate,
            "burn_rate": burn_rate,
            "timestamp": timestamp,
        }))

    return {"statusCode": 200, "results": results}
