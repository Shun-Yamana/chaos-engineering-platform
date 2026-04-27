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

SERVICES = ["service-a", "service-b"]


def get_error_rate(service: str, end_time: datetime, window_minutes: int) -> float:
    start_time = end_time - timedelta(minutes=window_minutes)

    namespace = "ContainerInsights"
    dimensions = [
        {"Name": "ClusterName", "Value": f"{PROJECT_NAME}-cluster"},
        {"Name": "ServiceName", "Value": service},
    ]

    def query_metric(metric_name: str) -> float:
        resp = cloudwatch.get_metric_statistics(
            Namespace=namespace,
            MetricName=metric_name,
            Dimensions=dimensions,
            StartTime=start_time,
            EndTime=end_time,
            Period=window_minutes * 60,
            Statistics=["Sum"],
        )
        datapoints = resp.get("Datapoints", [])
        return datapoints[0]["Sum"] if datapoints else 0.0

    total_requests = query_metric("pod_network_rx_bytes")
    if total_requests == 0:
        return 0.0

    # 5xxエラーをCloudWatch Logsのメトリクスフィルターから取得
    error_resp = cloudwatch.get_metric_statistics(
        Namespace=f"{PROJECT_NAME}/SLI",
        MetricName="5xxErrors",
        Dimensions=[{"Name": "ServiceName", "Value": service}],
        StartTime=start_time,
        EndTime=end_time,
        Period=window_minutes * 60,
        Statistics=["Sum"],
    )
    error_datapoints = error_resp.get("Datapoints", [])
    error_count = error_datapoints[0]["Sum"] if error_datapoints else 0.0

    request_resp = cloudwatch.get_metric_statistics(
        Namespace=f"{PROJECT_NAME}/SLI",
        MetricName="TotalRequests",
        Dimensions=[{"Name": "ServiceName", "Value": service}],
        StartTime=start_time,
        EndTime=end_time,
        Period=window_minutes * 60,
        Statistics=["Sum"],
    )
    request_datapoints = request_resp.get("Datapoints", [])
    request_count = request_datapoints[0]["Sum"] if request_datapoints else 0.0

    if request_count == 0:
        return 0.0

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
        error_rate = get_error_rate(service, now, WINDOW_MINUTES)
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
