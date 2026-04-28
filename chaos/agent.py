import time
import logging
import boto3
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from kubernetes import client, config
from kubernetes.client.rest import ApiException

logger = logging.getLogger(__name__)

dynamodb = boto3.resource("dynamodb")


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
        deployment = self._get_deployment(experiment.namespace, experiment.service)

        if any(c.name == "stress-ng-cpu" for c in deployment.spec.template.spec.containers):
            logger.info(f"[{experiment.experiment_id}] stress-ng-cpu already present, skipping")
            return

        sidecar = client.V1Container(
            name="stress-ng-cpu",
            image="alexeiled/stress-ng:latest",
            args=["--cpu", "1", "--timeout", str(experiment.duration_seconds)],
            resources=client.V1ResourceRequirements(
                requests={"cpu": "256m", "memory": "64Mi"},
                limits={"cpu": "512m", "memory": "128Mi"},
            ),
        )
        deployment.spec.template.spec.containers.append(sidecar)
        self._patch_deployment(experiment.namespace, experiment.service, deployment)
        logger.info(f"[{experiment.experiment_id}] stress-ng-cpu injected")

    def cpu_stress_remove(self, experiment: Experiment):
        deployment = self._get_deployment(experiment.namespace, experiment.service)
        deployment.spec.template.spec.containers = [
            c for c in deployment.spec.template.spec.containers if c.name != "stress-ng-cpu"
        ]
        self._patch_deployment(experiment.namespace, experiment.service, deployment)
        logger.info(f"[{experiment.experiment_id}] stress-ng-cpu removed")

    # --- memory_stress ---

    def memory_stress_inject(self, experiment: Experiment):
        deployment = self._get_deployment(experiment.namespace, experiment.service)

        if any(c.name == "stress-ng-mem" for c in deployment.spec.template.spec.containers):
            logger.info(f"[{experiment.experiment_id}] stress-ng-mem already present, skipping")
            return

        sidecar = client.V1Container(
            name="stress-ng-mem",
            image="alexeiled/stress-ng:latest",
            args=["--vm", "1", "--vm-bytes", "80%", "--timeout", str(experiment.duration_seconds)],
            resources=client.V1ResourceRequirements(
                requests={"cpu": "64m", "memory": "256Mi"},
                limits={"cpu": "128m", "memory": "512Mi"},
            ),
        )
        deployment.spec.template.spec.containers.append(sidecar)
        self._patch_deployment(experiment.namespace, experiment.service, deployment)
        logger.info(f"[{experiment.experiment_id}] stress-ng-mem injected")

    def memory_stress_remove(self, experiment: Experiment):
        deployment = self._get_deployment(experiment.namespace, experiment.service)
        deployment.spec.template.spec.containers = [
            c for c in deployment.spec.template.spec.containers if c.name != "stress-ng-mem"
        ]
        self._patch_deployment(experiment.namespace, experiment.service, deployment)
        logger.info(f"[{experiment.experiment_id}] stress-ng-mem removed")

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

    def network_latency_inject(self, experiment: Experiment):
        deployment = self._get_deployment(experiment.namespace, experiment.service)

        if any(c.name == "tc-latency" for c in deployment.spec.template.spec.containers):
            logger.info(f"[{experiment.experiment_id}] tc-latency already present, skipping")
            return

        sidecar = client.V1Container(
            name="tc-latency",
            image="nicolaka/netshoot",
            command=[
                "sh", "-c",
                f"tc qdisc add dev eth0 root netem delay {experiment.latency_ms}ms && sleep infinity",
            ],
            security_context=client.V1SecurityContext(
                capabilities=client.V1Capabilities(add=["NET_ADMIN"])
            ),
            resources=client.V1ResourceRequirements(
                requests={"cpu": "10m", "memory": "32Mi"},
                limits={"cpu": "50m", "memory": "64Mi"},
            ),
        )
        deployment.spec.template.spec.containers.append(sidecar)
        self._patch_deployment(experiment.namespace, experiment.service, deployment)
        logger.info(f"[{experiment.experiment_id}] tc-latency injected ({experiment.latency_ms}ms)")

    def network_latency_remove(self, experiment: Experiment):
        deployment = self._get_deployment(experiment.namespace, experiment.service)
        deployment.spec.template.spec.containers = [
            c for c in deployment.spec.template.spec.containers if c.name != "tc-latency"
        ]
        self._patch_deployment(experiment.namespace, experiment.service, deployment)
        logger.info(f"[{experiment.experiment_id}] tc-latency removed")

    # --- run / stop ---

    def run(self, experiment: Experiment):
        experiment.started_at = datetime.now(timezone.utc).isoformat()
        self._record_experiment(experiment, "running")

        try:
            if experiment.fault_type == "pod_kill":
                self.pod_kill(experiment)
                logger.info(f"[{experiment.experiment_id}] waiting {experiment.duration_seconds}s for recovery observation")
                time.sleep(experiment.duration_seconds)

            elif experiment.fault_type == "cpu_stress":
                self.cpu_stress_inject(experiment)
                time.sleep(experiment.duration_seconds)
                self.cpu_stress_remove(experiment)

            elif experiment.fault_type == "memory_stress":
                self.memory_stress_inject(experiment)
                time.sleep(experiment.duration_seconds)
                self.memory_stress_remove(experiment)

            elif experiment.fault_type == "http_error_inject":
                self.http_error_inject(experiment)
                time.sleep(experiment.duration_seconds)
                self.http_error_remove(experiment)

            elif experiment.fault_type == "network_latency":
                self.network_latency_inject(experiment)
                time.sleep(experiment.duration_seconds)
                self.network_latency_remove(experiment)

            else:
                raise ValueError(f"Unknown fault_type: {experiment.fault_type}")

        except Exception as e:
            logger.error(f"[{experiment.experiment_id}] experiment failed: {e}")
            self._record_experiment(experiment, "failed", stop_reason=str(e))
            raise

        self._record_experiment(experiment, "completed")
        logger.info(f"[{experiment.experiment_id}] experiment completed")

    def stop(self, experiment: Experiment, reason: str = "manual"):
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
