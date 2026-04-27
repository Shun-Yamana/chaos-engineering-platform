import os
import uuid
import json
import yaml
import click
import boto3
import httpx
import logging
from datetime import datetime, timezone
from agent import ChaosAgent, Experiment

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

EXPERIMENT_TABLE = os.environ.get("EXPERIMENT_TABLE", "chaos-platform-experiment-history")
SLO_TABLE = os.environ.get("SLO_TABLE", "chaos-platform-slo-definitions")
KUBECONFIG = os.environ.get("KUBECONFIG")
API_ENDPOINT = os.environ.get("CHAOS_API_ENDPOINT", "")


def load_experiment_file(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def _api_request(method: str, path: str, body: dict | None = None) -> dict:
    url = f"{API_ENDPOINT.rstrip('/')}{path}"
    with httpx.Client(timeout=10.0) as client:
        resp = client.request(method, url, json=body)
        resp.raise_for_status()
        return resp.json()


@click.group()
def cli():
    """Chaos Engineering Platform CLI"""


@cli.command()
@click.argument("experiment_file", type=click.Path(exists=True))
@click.option("--local", is_flag=True, default=False, help="Run agent locally without API")
def run(experiment_file: str, local: bool):
    """Run a chaos experiment from a YAML definition file."""
    definition = load_experiment_file(experiment_file)
    slack_webhook = os.path.expandvars(
        definition.get("notify", {}).get("slack_webhook", "")
    )

    if API_ENDPOINT and not local:
        result = _api_request("POST", "/experiments", definition)
        click.echo(f"Experiment started via API: {result['experiment_id']} (status={result['status']})")
        return

    experiment = Experiment(
        experiment_id=str(uuid.uuid4()),
        name=definition["name"],
        namespace=definition["target"]["namespace"],
        service=definition["target"]["service"],
        fault_type=definition["fault"]["type"],
        duration_seconds=int(definition["fault"]["duration"]),
        error_rate_threshold=float(definition["slo"]["error_rate_threshold"]),
        burn_rate_threshold=float(definition["slo"]["burn_rate_threshold"]),
        slack_webhook_url=slack_webhook or None,
        experiment_table=EXPERIMENT_TABLE,
    )

    click.echo(f"Starting experiment: {experiment.name} [{experiment.experiment_id}]")
    click.echo(f"  target : {experiment.namespace}/{experiment.service}")
    click.echo(f"  fault  : {experiment.fault_type} ({experiment.duration_seconds}s)")

    agent = ChaosAgent(kubeconfig_path=KUBECONFIG)
    agent.run(experiment)
    click.echo(f"Experiment completed: {experiment.experiment_id}")


@cli.command()
@click.argument("experiment_id")
@click.option("--reason", default="manual", help="Stop reason")
def stop(experiment_id: str, reason: str):
    """Stop a running experiment."""
    if API_ENDPOINT:
        result = _api_request("DELETE", f"/experiments/{experiment_id}", {"reason": reason})
        click.echo(f"Stopped: {result['experiment_id']}")
        return

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(EXPERIMENT_TABLE)
    resp = table.scan(
        FilterExpression="experiment_id = :id",
        ExpressionAttributeValues={":id": experiment_id},
    )
    items = resp.get("Items", [])
    if not items:
        click.echo(f"Experiment not found: {experiment_id}", err=True)
        raise SystemExit(1)

    item = items[0]
    if item.get("status") != "running":
        click.echo(f"Experiment is not running (status={item.get('status')})", err=True)
        raise SystemExit(1)

    experiment = Experiment(
        experiment_id=item["experiment_id"],
        name=item["name"],
        namespace=item["namespace"],
        service=item["target_service"],
        fault_type=item["fault_type"],
        duration_seconds=int(item["duration_seconds"]),
        error_rate_threshold=0.05,
        burn_rate_threshold=2.0,
        slack_webhook_url=None,
        experiment_table=EXPERIMENT_TABLE,
        started_at=item["started_at"],
    )

    agent = ChaosAgent(kubeconfig_path=KUBECONFIG)
    agent.stop(experiment, reason=reason)
    click.echo(f"Stopped: {experiment_id}")


@cli.command()
@click.option("--service", default=None, help="Filter by service name")
@click.option("--limit", default=10, help="Number of results")
def history(service: str | None, limit: int):
    """Show experiment history."""
    if API_ENDPOINT:
        params = f"?limit={limit}"
        if service:
            params += f"&service={service}"
        result = _api_request("GET", f"/experiments{params}")
        items = result.get("experiments", [])
    else:
        dynamodb = boto3.resource("dynamodb")
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

    if not items:
        click.echo("No experiments found.")
        return

    for item in items:
        status = item.get("status", "unknown")
        color = {"running": "yellow", "completed": "green", "stopped": "cyan", "failed": "red"}.get(status, "white")
        click.echo(
            f"{click.style(status.upper(), fg=color):12s}  "
            f"{item.get('started_at', '')[:19]}  "
            f"{item.get('target_service', ''):12s}  "
            f"{item.get('fault_type', ''):12s}  "
            f"{item.get('experiment_id', '')[:8]}"
        )


if __name__ == "__main__":
    cli()
