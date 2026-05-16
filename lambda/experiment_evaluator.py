"""
experiment_evaluator — DynamoDB Stream トリガー
実験が completed / stopped になったら 5 分後にメトリクスを取得し
Phase A (Absorb/Contain) + Phase B (Recover/TTR) の 2 フェーズで PASS/FAIL を判定する。

ADR 030 合格基準に対応。
"""
import os
import json
import time
import boto3
import logging
import urllib.request
from datetime import datetime, timezone, timedelta
from decimal import Decimal

logger = logging.getLogger()
logger.setLevel(logging.INFO)

cloudwatch = boto3.client("cloudwatch")
dynamodb = boto3.resource("dynamodb")

EXPERIMENT_TABLE = os.environ["EXPERIMENT_TABLE"]
ALB_ARN_SUFFIX = os.environ.get("ALB_ARN_SUFFIX", "")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
# CloudWatch メトリクス遅延バッファ: MAX(TTR=90s) + 3分 = 約 5 分
_CW_BUFFER_S = int(os.environ.get("CW_BUFFER_SECONDS", "300"))

_FAULT_LABELS = {
    "pod_kill":          "Pod Kill",
    "cpu_stress":        "CPU Stress",
    "memory_stress":     "Memory Stress",
    "http_error_inject": "HTTP Error Inject",
    "network_latency":   "Network Latency",
}


# ---------------------------------------------------------------------------
# CloudWatch helpers
# ---------------------------------------------------------------------------

def _alb_dims() -> list[dict]:
    return [{"Name": "LoadBalancer", "Value": ALB_ARN_SUFFIX}]


def _query_sum(metric: str, start: datetime, end: datetime) -> float:
    if not ALB_ARN_SUFFIX:
        return 0.0
    raw = max(int((end - start).total_seconds()), 60)
    period = ((raw + 59) // 60) * 60  # CloudWatch requires multiples of 60
    resp = cloudwatch.get_metric_statistics(
        Namespace="AWS/ApplicationELB",
        MetricName=metric,
        Dimensions=_alb_dims(),
        StartTime=start,
        EndTime=end,
        Period=period,
        Statistics=["Sum"],
    )
    dp = resp.get("Datapoints", [])
    return dp[0]["Sum"] if dp else 0.0


def _query_p95(metric: str, namespace: str, dimensions: list[dict],
               start: datetime, end: datetime) -> float | None:
    raw = max(int((end - start).total_seconds()), 60)
    period = ((raw + 59) // 60) * 60  # CloudWatch requires multiples of 60
    resp = cloudwatch.get_metric_statistics(
        Namespace=namespace,
        MetricName=metric,
        Dimensions=dimensions,
        StartTime=start,
        EndTime=end,
        Period=period,
        ExtendedStatistics=["p95"],
    )
    dp = resp.get("Datapoints", [])
    if not dp:
        return None
    return dp[0]["ExtendedStatistics"]["p95"]


def get_alb_error_rate(start: datetime, end: datetime) -> float:
    req = _query_sum("RequestCount", start, end)
    if req == 0:
        return 0.0
    err = _query_sum("HTTPCode_Target_5XX_Count", start, end)
    return err / req


def get_alb_p95_ms(start: datetime, end: datetime) -> float | None:
    p95_s = _query_p95(
        "TargetResponseTime", "AWS/ApplicationELB", _alb_dims(), start, end
    )
    return round(p95_s * 1000, 2) if p95_s is not None else None


def get_emf_p95_ms(metric: str, service: str, start: datetime, end: datetime) -> float | None:
    dims = [{"Name": "Service", "Value": service}]
    p95_ms = _query_p95(metric, "ChaosExperiment", dims, start, end)
    return round(p95_ms, 2) if p95_ms is not None else None


def get_emf_max(metric: str, service: str, start: datetime, end: datetime) -> float | None:
    raw = max(int((end - start).total_seconds()), 60)
    period = ((raw + 59) // 60) * 60  # CloudWatch requires multiples of 60
    dims = [{"Name": "Service", "Value": service}]
    resp = cloudwatch.get_metric_statistics(
        Namespace="ChaosExperiment",
        MetricName=metric,
        Dimensions=dims,
        StartTime=start,
        EndTime=end,
        Period=period,
        Statistics=["Maximum"],
    )
    dp = resp.get("Datapoints", [])
    return dp[0]["Maximum"] if dp else None


# ---------------------------------------------------------------------------
# Phase evaluation
# ---------------------------------------------------------------------------

def _check(name: str, value: float | None, op: str, threshold: float,
           ttr_actual_s: float | None = None, ttr_limit_s: float | None = None) -> dict:
    if value is None:
        return {"criterion": name, "value": None, "threshold": f"{op}{threshold}",
                "pass": None, "note": "no data"}
    if op == "<=":
        passed = value <= threshold
    elif op == ">=":
        passed = value >= threshold
    else:
        passed = False
    result = {"criterion": name, "value": value, "threshold": f"{op}{threshold}", "pass": passed}
    if ttr_actual_s is not None:
        result["ttr_actual_seconds"] = round(ttr_actual_s)
    if ttr_limit_s is not None:
        result["ttr_limit_seconds"] = ttr_limit_s
    return result


def _first_below_threshold(metric_fn, threshold: float, fault_end: datetime,
                           window_s: int, step_s: int = 30) -> float | None:
    """fault_end 以降、metric が threshold を下回るまでの秒数を返す。"""
    for t_s in range(step_s, window_s + step_s, step_s):
        t_start = fault_end + timedelta(seconds=t_s - step_s)
        t_end = fault_end + timedelta(seconds=t_s)
        val = metric_fn(t_start, t_end)
        if val is not None and val <= threshold:
            return float(t_s)
    return None


def evaluate_pod_kill(fault_start: datetime, fault_end: datetime) -> tuple[list, list]:
    phase_a = [
        _check("error_rate_during_fault",
               get_alb_error_rate(fault_start, fault_end),
               "<=", 0.01),
    ]
    ttr_s = _first_below_threshold(get_alb_error_rate, 0.005, fault_end, 120)
    phase_b = [
        _check("error_rate_recovery", get_alb_error_rate(fault_end, fault_end + timedelta(seconds=90)),
               "<=", 0.005, ttr_actual_s=ttr_s, ttr_limit_s=60),
    ]
    return phase_a, phase_b


def evaluate_cpu_stress(fault_start: datetime, fault_end: datetime) -> tuple[list, list]:
    phase_a = [
        _check("p95_latency_ms", get_alb_p95_ms(fault_start, fault_end), "<=", 1000),
        _check("error_rate", get_alb_error_rate(fault_start, fault_end), "<=", 0.05),
    ]
    ttr_s = _first_below_threshold(get_alb_p95_ms, 100, fault_end, 120)
    phase_b = [
        _check("p95_latency_ms_recovery",
               get_alb_p95_ms(fault_end, fault_end + timedelta(seconds=120)),
               "<=", 100, ttr_actual_s=ttr_s, ttr_limit_s=60),
    ]
    return phase_a, phase_b


def evaluate_memory_stress(fault_start: datetime, fault_end: datetime) -> tuple[list, list]:
    phase_a = [
        _check("error_rate", get_alb_error_rate(fault_start, fault_end), "<=", 0.05),
        _check("peak_memory_mb",
               get_emf_max("ProcessMemoryMB", "service-a", fault_start, fault_end),
               "<=", 512),  # OOMKill が 256Mi 制限で発生すること自体は合格
    ]
    ttr_s = _first_below_threshold(get_alb_error_rate, 0.005, fault_end, 150)
    phase_b = [
        _check("error_rate_recovery",
               get_alb_error_rate(fault_end, fault_end + timedelta(seconds=150)),
               "<=", 0.005, ttr_actual_s=ttr_s, ttr_limit_s=90),
        _check("memory_recovered_mb",
               get_emf_max("ProcessMemoryMB", "service-a",
                           fault_end + timedelta(seconds=60),
                           fault_end + timedelta(seconds=150)),
               "<=", 100),
    ]
    return phase_a, phase_b


def evaluate_http_error_inject(item: dict, fault_start: datetime, fault_end: datetime) -> tuple[list, list]:
    origin_error_rate = get_alb_error_rate(fault_start, fault_end)
    # auto_stopper が発動したかは DynamoDB の stop_reason から判断
    stopped_at = item.get("stopped_at")
    if stopped_at:
        stopped_dt = datetime.fromisoformat(stopped_at.replace("Z", "+00:00"))
        auto_stopper_latency_s = (stopped_dt - fault_start).total_seconds()
    else:
        auto_stopper_latency_s = None

    phase_a = [
        _check("origin_error_rate", origin_error_rate, ">=", 0.30),
        _check("auto_stopper_fired_within_5min",
               auto_stopper_latency_s, "<=", 300),
    ]
    ttr_s = _first_below_threshold(get_alb_error_rate, 0.005, fault_end, 180)
    phase_b = [
        _check("error_rate_recovery",
               get_alb_error_rate(fault_end, fault_end + timedelta(seconds=180)),
               "<=", 0.005, ttr_actual_s=ttr_s, ttr_limit_s=120),
    ]
    return phase_a, phase_b


def evaluate_network_latency(fault_start: datetime, fault_end: datetime) -> tuple[list, list]:
    service_b_p95 = get_emf_p95_ms("ResponseTimeMs", "service-b", fault_start, fault_end)
    service_a_p95 = get_emf_p95_ms("AggregateDurationMs", "service-a", fault_start, fault_end)
    # fallback: ALB P95 as proxy for service-b if EMF not available
    if service_b_p95 is None:
        service_b_p95 = get_alb_p95_ms(fault_start, fault_end)

    phase_a = [
        _check("service_b_p95_ms", service_b_p95, ">=", 450),
        _check("service_a_p95_ms", service_a_p95, "<=", 250),
    ]

    def service_a_p95_fn(s, e):
        return get_emf_p95_ms("AggregateDurationMs", "service-a", s, e)

    ttr_s = _first_below_threshold(service_a_p95_fn, 50, fault_end, 150)
    phase_b = [
        _check("service_a_p95_recovery",
               service_a_p95_fn(fault_end, fault_end + timedelta(seconds=150)),
               "<=", 50, ttr_actual_s=ttr_s, ttr_limit_s=90),
    ]
    return phase_a, phase_b


# ---------------------------------------------------------------------------
# Safety net
# ---------------------------------------------------------------------------

def evaluate_safety_net(fault_type: str, item: dict) -> dict:
    """http_error_inject のみ auto_stopper 発動が合格条件。他実験では不発動が合格。
    auto_stopper は実験を止めず emergency_stop フラグを立てるだけ。実験は completed で終わる。
    chaos-agent の _record_experiment は update_item でフラグを保持するため、ここで参照可能。
    """
    auto_stopper_fired = bool(item.get("emergency_stop"))
    if fault_type == "http_error_inject":
        expected = True
        passed = auto_stopper_fired
    else:
        expected = False
        passed = not auto_stopper_fired
    return {
        "auto_stopper_fired": auto_stopper_fired,
        "expected": expected,
        "pass": passed,
    }


# ---------------------------------------------------------------------------
# Main evaluator
# ---------------------------------------------------------------------------

def evaluate(item: dict) -> dict:
    fault_type = item["fault_type"]
    fault_start = datetime.fromisoformat(item["started_at"].replace("Z", "+00:00"))

    if item.get("stopped_at"):
        fault_end = datetime.fromisoformat(item["stopped_at"].replace("Z", "+00:00"))
    else:
        fault_end = fault_start + timedelta(seconds=int(item["duration_seconds"]))

    if fault_type == "pod_kill":
        phase_a, phase_b = evaluate_pod_kill(fault_start, fault_end)
    elif fault_type == "cpu_stress":
        phase_a, phase_b = evaluate_cpu_stress(fault_start, fault_end)
    elif fault_type == "memory_stress":
        phase_a, phase_b = evaluate_memory_stress(fault_start, fault_end)
    elif fault_type == "http_error_inject":
        phase_a, phase_b = evaluate_http_error_inject(item, fault_start, fault_end)
    elif fault_type == "network_latency":
        phase_a, phase_b = evaluate_network_latency(fault_start, fault_end)
    else:
        logger.warning(f"Unknown fault_type: {fault_type}")
        return {}

    safety_net = evaluate_safety_net(fault_type, item)

    def all_pass(checks: list) -> bool:
        return all(c.get("pass") is True for c in checks if c.get("pass") is not None)

    overall_pass = all_pass(phase_a) and all_pass(phase_b) and safety_net["pass"]

    return {
        "evaluation_result": "pass" if overall_pass else "fail",
        "evaluation_details": {
            "phase_a_absorption": phase_a,
            "phase_b_recovery": phase_b,
            "safety_net": safety_net,
        },
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
    }


def _criterion_line(c: dict) -> str:
    icon = "✅" if c.get("pass") is True else "❌" if c.get("pass") is False else "➖"
    label = c["criterion"].replace("_", " ")
    val = f"{c['value']:.3f}" if c.get("value") is not None else (c.get("note") or "no data")
    line = f"{icon} {label}: `{val}` {c.get('threshold', '')}"
    if c.get("ttr_actual_seconds") is not None:
        line += f"  (TTR {c['ttr_actual_seconds']}s / {c['ttr_limit_seconds']}s)"
    return line


def post_slack_report(item: dict, result: dict):
    if not SLACK_WEBHOOK_URL:
        return

    name        = item.get("name") or item["experiment_id"][:8]
    fault_type  = item.get("fault_type", "unknown")
    fault_label = _FAULT_LABELS.get(fault_type, fault_type)
    service     = item.get("target_service", "")
    duration    = item.get("duration_seconds", "?")
    exp_id      = item["experiment_id"][:8]

    passed      = result["evaluation_result"] == "pass"
    result_text = "✅  PASS" if passed else "❌  FAIL"
    color       = "good" if passed else "danger"

    details     = result.get("evaluation_details", {})
    phase_a     = details.get("phase_a_absorption", [])
    phase_b     = details.get("phase_b_recovery", [])
    safety_net  = details.get("safety_net", {})

    lines_a = "\n".join(_criterion_line(c) for c in phase_a) or "—"
    lines_b = "\n".join(_criterion_line(c) for c in phase_b) or "—"

    sn_icon    = "✅" if safety_net.get("pass") else "❌"
    fired      = "fired" if safety_net.get("auto_stopper_fired") else "not fired"
    expected   = "yes" if safety_net.get("expected") else "no"
    sn_line    = f"{sn_icon} auto_stopper: {fired}  (expected: {expected})"

    evaluated_at = result.get("evaluated_at", "")[:19].replace("T", " ") + " UTC"

    payload = {
        "attachments": [
            {
                "color": color,
                "blocks": [
                    {
                        "type": "header",
                        "text": {"type": "plain_text", "text": f"{result_text}  {name}"},
                    },
                    {
                        "type": "section",
                        "fields": [
                            {"type": "mrkdwn", "text": f"*Fault*\n{fault_label}"},
                            {"type": "mrkdwn", "text": f"*Service*\n{service}"},
                            {"type": "mrkdwn", "text": f"*Duration*\n{duration}s"},
                            {"type": "mrkdwn", "text": f"*ID*\n`{exp_id}`"},
                        ],
                    },
                    {"type": "divider"},
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": f"*Phase A — Absorb / Contain*\n{lines_a}"},
                    },
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": f"*Phase B — Recovery / TTR*\n{lines_b}"},
                    },
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": f"*Safety Net*\n{sn_line}"},
                    },
                    {
                        "type": "context",
                        "elements": [
                            {"type": "mrkdwn", "text": f"Evaluated at {evaluated_at}"}
                        ],
                    },
                ],
            }
        ]
    }

    data = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(
        SLACK_WEBHOOK_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            logger.info(f"Slack report sent: HTTP {resp.status}")
    except Exception as exc:
        logger.warning(f"Slack notification failed: {exc}")


def write_evaluation(experiment_id: str, started_at: str, result: dict):
    table = dynamodb.Table(EXPERIMENT_TABLE)

    def _convert(obj):
        if isinstance(obj, float):
            return Decimal(str(obj))
        if isinstance(obj, dict):
            return {k: _convert(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_convert(v) for v in obj]
        return obj

    table.update_item(
        Key={"experiment_id": experiment_id, "started_at": started_at},
        UpdateExpression=(
            "SET evaluation_result = :r, "
            "evaluation_details = :d, "
            "evaluated_at = :t"
        ),
        ExpressionAttributeValues={
            ":r": result["evaluation_result"],
            ":d": _convert(result["evaluation_details"]),
            ":t": result["evaluated_at"],
        },
    )


# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------

def handler(event, context):
    for record in event.get("Records", []):
        if record.get("eventName") not in ("INSERT", "MODIFY"):
            continue

        new_image = record.get("dynamodb", {}).get("NewImage", {})
        status = new_image.get("status", {}).get("S", "")

        if status not in ("completed", "stopped"):
            continue

        # 既に評価済みなら再実行しない
        if new_image.get("evaluation_result"):
            continue

        experiment_id = new_image["experiment_id"]["S"]
        started_at = new_image["started_at"]["S"]

        logger.info(json.dumps({
            "action": "evaluator_triggered",
            "experiment_id": experiment_id,
            "status": status,
        }))

        # CloudWatch メトリクス遅延バッファ待機
        logger.info(f"Waiting {_CW_BUFFER_S}s for CloudWatch metrics to settle...")
        time.sleep(_CW_BUFFER_S)

        # DynamoDB から最新アイテムを取得
        table = dynamodb.Table(EXPERIMENT_TABLE)
        resp = table.get_item(Key={"experiment_id": experiment_id, "started_at": started_at})
        item = resp.get("Item")
        if not item:
            logger.warning(f"Item not found: {experiment_id}")
            continue

        result = evaluate(item)
        if not result:
            continue

        write_evaluation(experiment_id, started_at, result)
        logger.info(json.dumps({
            "action": "evaluation_written",
            "experiment_id": experiment_id,
            "evaluation_result": result["evaluation_result"],
        }))
        post_slack_report(item, result)

    return {"statusCode": 200}
