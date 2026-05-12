import os
import time
import logging
import threading
import boto3
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from kubernetes.stream import stream
from boto3.dynamodb.conditions import Attr

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

dynamodb = boto3.resource(
    "dynamodb",
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


class ChaosAgent:
    def __init__(self, kubeconfig_path: str | None = None):
        if kubeconfig_path:
            config.load_kube_config(config_file=kubeconfig_path)
        else:
            config.load_incluster_config()

        self.core_v1 = client.CoreV1Api()
        self.apps_v1 = client.AppsV1Api()
        self._stress_targets: dict[str, str] = {}   # experiment_id -> pod_name
        self._fis_experiments: dict[str, str] = {}  # experiment_id -> FIS experiment id
        self._fis = boto3.client("fis")

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

    def cpu_stress_inject(self, experiment: Experiment):
        pods = self._get_pods(experiment.namespace, experiment.service)
        if not pods:
            raise RuntimeError(f"No running pods found for service={experiment.service}")

        pod_name = pods[0].metadata.name
        self._stress_targets[experiment.experiment_id] = pod_name

        body = {
            "spec": {
                "ephemeralContainers": [{
                    "name": "stress-ng-cpu",
                    "image": "alexeiled/stress-ng:latest",
                    "args": ["--cpu", "1", "--timeout", str(experiment.duration_seconds)],
                    "resources": {
                        "requests": {"cpu": "256m", "memory": "64Mi"},
                        "limits": {"cpu": "512m", "memory": "128Mi"},
                    },
                }]
            }
        }
        self.core_v1.patch_namespaced_pod_ephemeralcontainers(
            name=pod_name, namespace=experiment.namespace, body=body
        )
        logger.info(f"[{experiment.experiment_id}] stress-ng-cpu injected into pod={pod_name}")

    def cpu_stress_remove(self, experiment: Experiment):
        pod_name = self._stress_targets.pop(experiment.experiment_id, None)
        if pod_name:
            self._pkill_stress(pod_name, experiment.namespace, experiment.experiment_id, "stress-ng-cpu")
        else:
            for pod in self._get_pods(experiment.namespace, experiment.service):
                self._pkill_stress(pod.metadata.name, experiment.namespace, experiment.experiment_id, "stress-ng-cpu")

    # --- memory_stress ---

    def memory_stress_inject(self, experiment: Experiment):
        pods = self._get_pods(experiment.namespace, experiment.service)
        if not pods:
            raise RuntimeError(f"No running pods found for service={experiment.service}")

        pod_name = pods[0].metadata.name
        self._stress_targets[experiment.experiment_id] = pod_name

        body = {
            "spec": {
                "ephemeralContainers": [{
                    "name": "stress-ng-mem",
                    "image": "alexeiled/stress-ng:latest",
                    "args": ["--vm", "1", "--vm-bytes", "80%", "--timeout", str(experiment.duration_seconds)],
                    "resources": {
                        "requests": {"cpu": "64m", "memory": "256Mi"},
                        "limits": {"cpu": "128m", "memory": "512Mi"},
                    },
                }]
            }
        }
        self.core_v1.patch_namespaced_pod_ephemeralcontainers(
            name=pod_name, namespace=experiment.namespace, body=body
        )
        logger.info(f"[{experiment.experiment_id}] stress-ng-mem injected into pod={pod_name}")

    def memory_stress_remove(self, experiment: Experiment):
        pod_name = self._stress_targets.pop(experiment.experiment_id, None)
        if pod_name:
            self._pkill_stress(pod_name, experiment.namespace, experiment.experiment_id, "stress-ng-mem")
        else:
            for pod in self._get_pods(experiment.namespace, experiment.service):
                self._pkill_stress(pod.metadata.name, experiment.namespace, experiment.experiment_id, "stress-ng-mem")

    def _pkill_stress(self, pod_name: str, namespace: str, experiment_id: str, container: str):
        try:
            stream(
                self.core_v1.connect_get_namespaced_pod_exec,
                pod_name, namespace,
                command=["pkill", "stress-ng"],
                container=container,
                stderr=True, stdin=False, stdout=True, tty=False,
            )
            logger.info(f"[{experiment_id}] stress-ng killed in pod={pod_name}")
        except ApiException as e:
            logger.warning(f"[{experiment_id}] pkill skipped (already exited?) pod={pod_name}: {e.status}")

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
    # tc netem には NET_ADMIN が必要だが EKS Fargate は Pod レベルで封じているため
    # AWS FIS (aws:eks:pod-network-latency) に委譲する (ADR 009, ADR 010)

    def network_latency_inject(self, experiment: Experiment):
        # FIS テンプレート ID は Terraform output から環境変数で渡す
        # 例: FIS_TEMPLATE_SERVICE_A, FIS_TEMPLATE_SERVICE_B
        env_key = f"FIS_TEMPLATE_{experiment.service.upper().replace('-', '_')}"
        template_id = os.environ.get(env_key)
        if not template_id:
            raise RuntimeError(
                f"FIS template ID not set: env var {env_key} is missing"
            )

        response = self._fis.start_experiment(
            experimentTemplateId=template_id,
            experimentTemplateParameters={
                "duration": f"PT{experiment.duration_seconds}S",
                "delayMilliseconds": str(experiment.latency_ms),
            },
            tags={"Project": "chaos-platform", "ExperimentId": experiment.experiment_id},
        )
        fis_id = response["experiment"]["id"]
        self._fis_experiments[experiment.experiment_id] = fis_id
        logger.info(
            f"[{experiment.experiment_id}] FIS experiment started: {fis_id} "
            f"latency={experiment.latency_ms}ms duration={experiment.duration_seconds}s"
        )

    def network_latency_remove(self, experiment: Experiment):
        fis_id = self._fis_experiments.pop(experiment.experiment_id, None)
        if not fis_id:
            logger.warning(f"[{experiment.experiment_id}] no FIS experiment ID found, skipping stop")
            return
        try:
            self._fis.stop_experiment(id=fis_id)
            logger.info(f"[{experiment.experiment_id}] FIS experiment stopped: {fis_id}")
        except self._fis.exceptions.ConflictException:
            # 実験が duration で自然終了している場合は ConflictException になる
            logger.info(f"[{experiment.experiment_id}] FIS experiment already completed: {fis_id}")

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


if __name__ == "__main__":
    if not TABLE_NAME:
        raise RuntimeError("TABLE_NAME environment variable is required")

    chaos_agent = ChaosAgent()
    poller = ChaosAgentPoller(chaos_agent, TABLE_NAME, poll_interval=10)
    poller.run_forever()
