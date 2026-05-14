import os
import time
import logging
import threading
import urllib.request
import urllib.error
import boto3
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from boto3.dynamodb.conditions import Attr

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

dynamodb = boto3.resource(
    "dynamodb",
    region_name=os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION")),
    endpoint_url=os.environ.get("DYNAMODB_ENDPOINT_URL"),
)

TABLE_NAME = os.environ.get("TABLE_NAME", "")


@dataclass
class Experiment:
    experiment_id: str
    name: str
    namespace: str
    service: str
    fault_type: str        # pod_kill | cpu_stress | memory_stress | http_error_inject | network_latency
    duration_seconds: int
    error_rate_threshold: float
    burn_rate_threshold: float
    slack_webhook_url: str | None
    experiment_table: str
    started_at: str | None = None
    latency_ms: int = 500
    fault_rate: float = 0.5
    memory_stress_mb: int = 256


class ChaosAgent:
    def __init__(self, kubeconfig_path: str | None = None):
        if kubeconfig_path:
            config.load_kube_config(config_file=kubeconfig_path)
        else:
            config.load_incluster_config()

        self.core_v1 = client.CoreV1Api()
        self.apps_v1 = client.AppsV1Api()

    def _get_pods(self, namespace: str, service: str) -> list:
        pods = self.core_v1.list_namespaced_pod(
            namespace=namespace,
            label_selector=f"app={service}",
        )
        return [p for p in pods.items if p.status.phase == "Running"]

    def _record_experiment(self, experiment: Experiment, status: str, stop_reason: str = ""):
        table = dynamodb.Table(experiment.experiment_table)
        now = datetime.now(timezone.utc).isoformat()
        expires_at = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp())

        item = {
            "experiment_id": experiment.experiment_id,
            "started_at": experiment.started_at or now,
            "name": experiment.name,
            "target_service": experiment.service,
            "namespace": experiment.namespace,
            "fault_type": experiment.fault_type,
            "duration_seconds": experiment.duration_seconds,
            "status": status,
            "expires_at": expires_at,
        }
        if stop_reason:
            item["stop_reason"] = stop_reason
        if status in ("stopped", "completed"):
            item["stopped_at"] = now

        table.put_item(Item=item)

    def _get_deployment(self, namespace: str, service: str):
        return self.apps_v1.read_namespaced_deployment(name=service, namespace=namespace)

    def _patch_deployment(self, namespace: str, service: str, deployment):
        self.apps_v1.patch_namespaced_deployment(name=service, namespace=namespace, body=deployment)

    # --- pod_kill ---

    def pod_kill(self, experiment: Experiment):
        logger.info(f"[{experiment.experiment_id}] pod_kill: service={experiment.service}")

        pods = self._get_pods(experiment.namespace, experiment.service)
        if not pods:
            raise RuntimeError(f"No running pods found for service={experiment.service}")

        pod_name = pods[0].metadata.name
        try:
            self.core_v1.delete_namespaced_pod(
                name=pod_name,
                namespace=experiment.namespace,
                body=client.V1DeleteOptions(grace_period_seconds=0),
            )
        except ApiException as e:
            if e.status != 404:
                raise
            logger.warning(f"[{experiment.experiment_id}] pod already gone: {pod_name}")

        logger.info(f"[{experiment.experiment_id}] killed pod: {pod_name}")

    # --- cpu_stress ---
    # EKS Fargate はエフェメラルコンテナ非対応のため、
    # Deployment の CPU_STRESS 環境変数でアプリレベル CPU 負荷を注入する。

    def cpu_stress_inject(self, experiment: Experiment):
        deployment = self._get_deployment(experiment.namespace, experiment.service)

        for container in deployment.spec.template.spec.containers:
            if container.name == experiment.service:
                if container.env is None:
                    container.env = []
                container.env = [e for e in container.env if e.name != "CPU_STRESS"]
                container.env.append(client.V1EnvVar(name="CPU_STRESS", value="true"))
                break

        self._patch_deployment(experiment.namespace, experiment.service, deployment)
        logger.info(f"[{experiment.experiment_id}] CPU_STRESS=true patched")

    def cpu_stress_remove(self, experiment: Experiment):
        deployment = self._get_deployment(experiment.namespace, experiment.service)

        for container in deployment.spec.template.spec.containers:
            if container.name == experiment.service:
                if container.env:
                    container.env = [e for e in container.env if e.name != "CPU_STRESS"]
                break

        self._patch_deployment(experiment.namespace, experiment.service, deployment)
        logger.info(f"[{experiment.experiment_id}] CPU_STRESS removed")

    # --- memory_stress ---
    # EKS Fargate はエフェメラルコンテナ非対応のため、
    # Deployment の MEMORY_STRESS_MB 環境変数でアプリレベルメモリ確保を注入する。

    def memory_stress_inject(self, experiment: Experiment):
        deployment = self._get_deployment(experiment.namespace, experiment.service)

        for container in deployment.spec.template.spec.containers:
            if container.name == experiment.service:
                if container.env is None:
                    container.env = []
                container.env = [e for e in container.env if e.name != "MEMORY_STRESS_MB"]
                container.env.append(client.V1EnvVar(name="MEMORY_STRESS_MB", value=str(experiment.memory_stress_mb)))
                break

        self._patch_deployment(experiment.namespace, experiment.service, deployment)
        logger.info(f"[{experiment.experiment_id}] MEMORY_STRESS_MB={experiment.memory_stress_mb} patched")

    def memory_stress_remove(self, experiment: Experiment):
        deployment = self._get_deployment(experiment.namespace, experiment.service)

        for container in deployment.spec.template.spec.containers:
            if container.name == experiment.service:
                if container.env:
                    container.env = [e for e in container.env if e.name != "MEMORY_STRESS_MB"]
                break

        self._patch_deployment(experiment.namespace, experiment.service, deployment)
        logger.info(f"[{experiment.experiment_id}] MEMORY_STRESS_MB removed")

    # --- http_error_inject ---

    def http_error_inject(self, experiment: Experiment):
        deployment = self._get_deployment(experiment.namespace, experiment.service)

        for container in deployment.spec.template.spec.containers:
            if container.name == experiment.service:
                if container.env is None:
                    container.env = []
                container.env = [e for e in container.env if e.name != "FAULT_RATE"]
                container.env.append(client.V1EnvVar(name="FAULT_RATE", value=str(experiment.fault_rate)))
                break

        self._patch_deployment(experiment.namespace, experiment.service, deployment)
        logger.info(f"[{experiment.experiment_id}] FAULT_RATE={experiment.fault_rate} patched")

    def http_error_remove(self, experiment: Experiment):
        deployment = self._get_deployment(experiment.namespace, experiment.service)

        for container in deployment.spec.template.spec.containers:
            if container.name == experiment.service:
                if container.env:
                    container.env = [e for e in container.env if e.name != "FAULT_RATE"]
                break

        self._patch_deployment(experiment.namespace, experiment.service, deployment)
        logger.info(f"[{experiment.experiment_id}] FAULT_RATE removed")

    # --- network_latency ---
    # EKS Fargate は tc netem に必要な NET_ADMIN を禁止しており、
    # aws:eks:pod-network-latency も Fargate 非対応のため、
    # Deployment の LATENCY_MS 環境変数でアプリレベル遅延を注入する。

    def network_latency_inject(self, experiment: Experiment):
        deployment = self._get_deployment(experiment.namespace, experiment.service)

        for container in deployment.spec.template.spec.containers:
            if container.name == experiment.service:
                if container.env is None:
                    container.env = []
                container.env = [e for e in container.env if e.name != "LATENCY_MS"]
                container.env.append(client.V1EnvVar(name="LATENCY_MS", value=str(experiment.latency_ms)))
                break

        self._patch_deployment(experiment.namespace, experiment.service, deployment)
        logger.info(f"[{experiment.experiment_id}] LATENCY_MS={experiment.latency_ms} patched")

    def network_latency_remove(self, experiment: Experiment):
        deployment = self._get_deployment(experiment.namespace, experiment.service)

        for container in deployment.spec.template.spec.containers:
            if container.name == experiment.service:
                if container.env:
                    container.env = [e for e in container.env if e.name != "LATENCY_MS"]
                break

        self._patch_deployment(experiment.namespace, experiment.service, deployment)
        logger.info(f"[{experiment.experiment_id}] LATENCY_MS removed")

    # --- run / stop ---

    def _interruptible_sleep(self, experiment: Experiment, seconds: int, check_interval: int = 5) -> bool:
        """duration_seconds 分スリープしつつ DynamoDB の status を定期確認する。
        外部から status=stopped にされた場合は False を返して早期終了する。"""
        table = dynamodb.Table(experiment.experiment_table)
        elapsed = 0
        while elapsed < seconds:
            time.sleep(min(check_interval, seconds - elapsed))
            elapsed += check_interval

            resp = table.get_item(Key={
                "experiment_id": experiment.experiment_id,
                "started_at": experiment.started_at,
            })
            if resp.get("Item", {}).get("status") == "stopped":
                logger.info(f"[{experiment.experiment_id}] external stop detected")
                return False
        return True

    def run(self, experiment: Experiment):
        experiment.started_at = datetime.now(timezone.utc).isoformat()
        self._record_experiment(experiment, "running")

        try:
            if experiment.fault_type == "pod_kill":
                self.pod_kill(experiment)
                logger.info(f"[{experiment.experiment_id}] waiting {experiment.duration_seconds}s for recovery observation")
                self._interruptible_sleep(experiment, experiment.duration_seconds)

            elif experiment.fault_type == "cpu_stress":
                self.cpu_stress_inject(experiment)
                stopped = self._interruptible_sleep(experiment, experiment.duration_seconds)
                self.cpu_stress_remove(experiment)
                if not stopped:
                    return

            elif experiment.fault_type == "memory_stress":
                self.memory_stress_inject(experiment)
                stopped = self._interruptible_sleep(experiment, experiment.duration_seconds)
                self.memory_stress_remove(experiment)
                if not stopped:
                    return

            elif experiment.fault_type == "http_error_inject":
                self.http_error_inject(experiment)
                stopped = self._interruptible_sleep(experiment, experiment.duration_seconds)
                self.http_error_remove(experiment)
                if not stopped:
                    return

            elif experiment.fault_type == "network_latency":
                self.network_latency_inject(experiment)
                stopped = self._interruptible_sleep(experiment, experiment.duration_seconds)
                self.network_latency_remove(experiment)
                if not stopped:
                    return

            else:
                raise ValueError(f"Unknown fault_type: {experiment.fault_type}")

        except Exception as e:
            logger.error(f"[{experiment.experiment_id}] experiment failed: {e}")
            self._record_experiment(experiment, "failed", stop_reason=str(e))
            raise

        self._record_experiment(experiment, "completed")
        logger.info(f"[{experiment.experiment_id}] experiment completed")

    def stop(self, experiment: Experiment, reason: str = "manual"):  # noqa: D401
        cleanup = {
            "cpu_stress": self.cpu_stress_remove,
            "memory_stress": self.memory_stress_remove,
            "http_error_inject": self.http_error_remove,
            "network_latency": self.network_latency_remove,
        }
        if experiment.fault_type in cleanup:
            try:
                cleanup[experiment.fault_type](experiment)
            except Exception as e:
                logger.warning(f"[{experiment.experiment_id}] cleanup failed: {e}")

        self._record_experiment(experiment, "stopped", stop_reason=reason)
        logger.info(f"[{experiment.experiment_id}] experiment stopped: {reason}")


# ---------------------------------------------------------------------------
# DynamoDB ポーリングループ
# ---------------------------------------------------------------------------

def _item_to_experiment(item: dict) -> Experiment:
    return Experiment(
        experiment_id=item["experiment_id"],
        name=item["name"],
        namespace=item.get("namespace", "default"),
        service=item["target_service"],
        fault_type=item["fault_type"],
        duration_seconds=int(item["duration_seconds"]),
        error_rate_threshold=float(item.get("error_rate_threshold", 0.05)),
        burn_rate_threshold=float(item.get("burn_rate_threshold", 2.0)),
        slack_webhook_url=item.get("slack_webhook_url"),
        experiment_table=TABLE_NAME,
        started_at=item.get("started_at"),
        latency_ms=int(item.get("latency_ms", 500)),
        fault_rate=float(item.get("fault_rate", 0.5)),
        memory_stress_mb=int(item.get("memory_stress_mb", 256)),
    )


class ChaosAgentPoller:
    def __init__(self, agent: ChaosAgent, table_name: str, poll_interval: int = 10):
        self._agent = agent
        self._table = dynamodb.Table(table_name)
        self._poll_interval = poll_interval
        self._running: dict[str, threading.Thread] = {}

    def _scan_pending(self) -> list[dict]:
        resp = self._table.scan(
            FilterExpression=Attr("status").eq("pending"),
        )
        return resp.get("Items", [])

    def _claim(self, item: dict) -> bool:
        """pending → running へ条件付き更新。競合時は False を返す。"""
        try:
            self._table.update_item(
                Key={
                    "experiment_id": item["experiment_id"],
                    "started_at": item["started_at"],
                },
                UpdateExpression="SET #st = :running",
                ConditionExpression=Attr("status").eq("pending"),
                ExpressionAttributeNames={"#st": "status"},
                ExpressionAttributeValues={":running": "running"},
            )
            return True
        except self._table.meta.client.exceptions.ConditionalCheckFailedException:
            return False

    def _run_experiment(self, experiment: Experiment):
        try:
            self._agent.run(experiment)
        except Exception as e:
            logger.error(f"[{experiment.experiment_id}] thread error: {e}")
        finally:
            self._running.pop(experiment.experiment_id, None)

    def _poll(self):
        # 完了スレッドを整理
        done = [eid for eid, t in self._running.items() if not t.is_alive()]
        for eid in done:
            self._running.pop(eid, None)

        for item in self._scan_pending():
            eid = item["experiment_id"]
            if eid in self._running:
                continue
            if not self._claim(item):
                continue

            experiment = _item_to_experiment(item)
            thread = threading.Thread(
                target=self._run_experiment,
                args=(experiment,),
                daemon=True,
                name=f"experiment-{eid[:8]}",
            )
            self._running[eid] = thread
            thread.start()
            logger.info(f"[{eid}] experiment thread started: {experiment.fault_type}")

    def run_forever(self):
        logger.info(f"chaos-agent poller started (interval={self._poll_interval}s, table={self._table.name})")
        while True:
            try:
                self._poll()
            except Exception as e:
                logger.error(f"poll error: {e}")
            time.sleep(self._poll_interval)


# ---------------------------------------------------------------------------
# バックグラウンド定期トラフィック生成（CloudWatch アラームをデータ不足にさせない）
# ---------------------------------------------------------------------------

class TrafficGenerator:
    """SERVICE_B_URL に定期的に GET リクエストを送り ALB メトリクスを維持する。"""

    def __init__(self, url: str, interval: int = 30, origin_secret: str = ""):
        self._url = url
        self._interval = interval
        self._origin_secret = origin_secret

    def _send(self):
        try:
            req = urllib.request.Request(self._url)
            if self._origin_secret:
                req.add_header("X-Origin-Verify", self._origin_secret)
            with urllib.request.urlopen(req, timeout=5) as resp:
                logger.debug(f"[traffic-gen] {self._url} -> {resp.status}")
        except urllib.error.HTTPError as e:
            logger.debug(f"[traffic-gen] {self._url} -> {e.code}")
        except Exception as e:
            logger.warning(f"[traffic-gen] request failed: {e}")

    def run_forever(self):
        logger.info(f"traffic-gen started (url={self._url} interval={self._interval}s)")
        while True:
            self._send()
            time.sleep(self._interval)


def _discover_service_b_url(core_v1) -> str | None:
    """Ingress の ALB ホスト名から service-b URL を自動検出する。SERVICE_B_URL 未設定時のフォールバック。"""
    try:
        ingress = core_v1.read_namespaced_ingress(name="service-b", namespace="default")
        lb = ingress.status.load_balancer
        if lb and lb.ingress:
            hostname = lb.ingress[0].hostname
            if hostname:
                return f"http://{hostname}/items/1"
    except Exception as e:
        logger.warning(f"Ingress-based service-b discovery failed: {e}")
    return None


if __name__ == "__main__":
    if not TABLE_NAME:
        raise RuntimeError("TABLE_NAME environment variable is required")

    chaos_agent = ChaosAgent()

    service_b_url = os.environ.get("SERVICE_B_URL")
    if not service_b_url:
        service_b_url = _discover_service_b_url(chaos_agent.core_v1)
        if service_b_url:
            logger.info(f"Auto-discovered service-b URL from Ingress: {service_b_url}")

    if service_b_url:
        traffic_interval = int(os.environ.get("TRAFFIC_GEN_INTERVAL", "30"))
        origin_secret = os.environ.get("ALB_ORIGIN_SECRET", "")
        gen = TrafficGenerator(service_b_url, interval=traffic_interval, origin_secret=origin_secret)
        t = threading.Thread(target=gen.run_forever, daemon=True, name="traffic-gen")
        t.start()
    else:
        logger.info("SERVICE_B_URL not set, traffic generator disabled")

    poller = ChaosAgentPoller(chaos_agent, TABLE_NAME, poll_interval=10)
    poller.run_forever()
