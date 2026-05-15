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
ALB_ARN_SUFFIX = os.environ.get("ALB_ARN_SUFFIX", "")

# service-a は ALB を持たない（ClusterIP 経由で service-b に呼ばれる）。
# ALB メトリクスは service-a のエラーも間接的に反映するため、
# 同じ ALB error_rate を service-a SLI として書き込む（auto_stopper との整合性確保）。
SERVICES = ["service-a", "service-b"]


def get_error_rate(end_time: datetime, window_minutes: int) -> float:
    if not ALB_ARN_SUFFIX:
        logger.warning("ALB_ARN_SUFFIX not set, skipping error_rate query")
        return 0.0

    # バケット境界ずれ対策: end を1分前の :00 に揃え、window+1分遡る
    end_aligned = end_time.replace(second=0, microsecond=0)
    start_time = end_aligned - timedelta(minutes=window_minutes + 1)
    dimensions = [{"Name": "LoadBalancer", "Value": ALB_ARN_SUFFIX}]

    def query_sum(metric_name: str) -> float:
        resp = cloudwatch.get_metric_statistics(
            Namespace="AWS/ApplicationELB",
            MetricName=metric_name,
            Dimensions=dimensions,
            StartTime=start_time,
            EndTime=end_aligned,
            Period=60,
            Statistics=["Sum"],
        )
        return sum(dp["Sum"] for dp in resp.get("Datapoints", []))

    request_count = query_sum("RequestCount")
    if request_count == 0:
        return 0.0

    error_count = query_sum("HTTPCode_Target_5XX_Count")
    return error_count / request_count


def get_latency_p95_ms(end_time: datetime, window_minutes: int) -> float | None:
    """ALB TargetResponseTime p95 を取得する（service-b 経由の end-to-end レイテンシ）。"""
    if not ALB_ARN_SUFFIX:
        return None

    end_aligned = end_time.replace(second=0, microsecond=0)
    start_time = end_aligned - timedelta(minutes=window_minutes + 1)
    dimensions = [{"Name": "LoadBalancer", "Value": ALB_ARN_SUFFIX}]

    resp = cloudwatch.get_metric_statistics(
        Namespace="AWS/ApplicationELB",
        MetricName="TargetResponseTime",
        Dimensions=dimensions,
        StartTime=start_time,
        EndTime=end_aligned,
        Period=60,
        ExtendedStatistics=["p95"],
    )
    datapoints = resp.get("Datapoints", [])
    if not datapoints:
        return None
    return round(max(dp["ExtendedStatistics"]["p95"] for dp in datapoints) * 1000, 2)  # s → ms


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


def save_sli(service: str, error_rate: float, burn_rate: float, timestamp: str,
             latency_p95_ms: float | None = None):
    table = dynamodb.Table(SLI_TABLE)
    expires_at = int((datetime.now(timezone.utc) + timedelta(days=7)).timestamp())

    item = {
        "service_name": service,
        "timestamp": timestamp,
        "error_rate": str(round(error_rate, 6)),
        "burn_rate": str(round(burn_rate, 6)),
        "expires_at": expires_at,
    }
    if latency_p95_ms is not None:
        item["latency_p95_ms"] = str(round(latency_p95_ms, 2))

    table.put_item(Item=item)


def handler(event, context):
    now = datetime.now(timezone.utc)
    timestamp = now.isoformat()

    error_rate = get_error_rate(now, WINDOW_MINUTES)
    latency_p95_ms = get_latency_p95_ms(now, WINDOW_MINUTES)

    results = []
    for service in SERVICES:
        slo = get_slo(service)
        burn_rate = calculate_burn_rate(error_rate, slo)

        save_sli(service, error_rate, burn_rate, timestamp, latency_p95_ms)

        results.append({
            "service": service,
            "error_rate": error_rate,
            "burn_rate": burn_rate,
            "latency_p95_ms": latency_p95_ms,
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
            "latency_p95_ms": latency_p95_ms,
            "timestamp": timestamp,
        }))

    return {"statusCode": 200, "results": results}
