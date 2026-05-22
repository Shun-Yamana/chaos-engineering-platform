"""
DynamoDB Streams → Grafana Annotation API
実験の status 変化を検知して Grafana にタイムライン注釈を POST する。
  running   → chaos-start タグで開始注釈
  completed/stopped → chaos-end タグで終了注釈
"""

import json
import os
import urllib.request
import urllib.error

GRAFANA_URL = os.environ["GRAFANA_URL"].rstrip("/")
GRAFANA_TOKEN = os.environ["GRAFANA_TOKEN"]
DASHBOARD_UID = os.environ.get("ANNOTATION_DASHBOARD_UID", "chaos-experiment")

_HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {GRAFANA_TOKEN}",
}


def _post_annotation(tags: list[str], text: str, time_ms: int) -> None:
    payload = json.dumps({
        "dashboardUID": DASHBOARD_UID,
        "tags": tags,
        "text": text,
        "time": time_ms,
    }).encode()

    req = urllib.request.Request(
        f"{GRAFANA_URL}/api/annotations",
        data=payload,
        headers=_HEADERS,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"Grafana API {e.code}: {body}") from e


def _extract(image: dict, key: str) -> str | None:
    wrapper = image.get(key, {})
    return wrapper.get("S") or wrapper.get("N") or None


def handler(event, context):
    for record in event.get("Records", []):
        if record.get("eventName") not in ("INSERT", "MODIFY"):
            continue

        new_img = record["dynamodb"].get("NewImage", {})
        status = _extract(new_img, "status")
        if not status:
            continue

        experiment_id = _extract(new_img, "experiment_id") or "unknown"
        fault_type = _extract(new_img, "fault_type") or ""
        target = _extract(new_img, "target_service") or ""

        # approximate_creation_date_time is epoch seconds (float)
        approx_ms = int(
            record["dynamodb"].get("ApproximateCreationDateTime", 0) * 1000
        )

        label = f"{experiment_id} | {fault_type} | {target}"

        if status == "running":
            _post_annotation(
                tags=["chaos-start"],
                text=f"[START] {label}",
                time_ms=approx_ms,
            )
        elif status in ("completed", "stopped", "failed"):
            _post_annotation(
                tags=["chaos-end"],
                text=f"[END:{status}] {label}",
                time_ms=approx_ms,
            )

    return {"statusCode": 200}
