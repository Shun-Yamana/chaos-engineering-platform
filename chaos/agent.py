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
    fault_type: str        # pod_kill | cpu_stress
    duration_seconds: int
    error_rate_threshold: float
    burn_rate_threshold: float
    slack_webhook_url: str | None
    experiment_table: str
    started_at: str | None = None


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
        running = [
            p for p in pods.items
            if p.status.phase == "Running"
        ]
        return running

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

    def pod_kill(self, experiment: Experiment):
        logger.info(f"[{experiment.experiment_id}] starting pod_kill: service={experiment.service}")

        pods = self._get_pods(experiment.namespace, experiment.service)
        if not pods:
            raise RuntimeError(f"No running pods found for service={experiment.service}")

        target = pods[0]
        pod_name = target.metadata.name
        logger.info(f"[{experiment.experiment_id}] killing pod: {pod_name}")

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

        logger.info(f"[{experiment.experiment_id}] pod killed: {pod_name}")

    def cpu_stress_inject(self, experiment: Experiment):
        logger.info(f"[{experiment.experiment_id}] starting cpu_stress: service={experiment.service}")

        deployment = self.apps_v1.read_namespaced_deployment(
            name=experiment.service,
            namespace=experiment.namespace,
        )

        containers = deployment.spec.template.spec.containers
        already_injected = any(c.name == "stress-ng" for c in containers)
        if already_injected:
            logger.info(f"[{experiment.experiment_id}] stress-ng sidecar already present, skipping inject")
            return

        stress_container = client.V1Container(
            name="stress-ng",
            image="alexeiled/stress-ng:latest",
            args=["--cpu", "1", "--timeout", str(experiment.duration_seconds)],
            resources=client.V1ResourceRequirements(
                requests={"cpu": "256m", "memory": "64Mi"},
                limits={"cpu": "512m", "memory": "128Mi"},
            ),
        )
        deployment.spec.template.spec.containers.append(stress_container)

        self.apps_v1.patch_namespaced_deployment(
            name=experiment.service,
            namespace=experiment.namespace,
            body=deployment,
        )
        logger.info(f"[{experiment.experiment_id}] stress-ng sidecar injected")

    def cpu_stress_remove(self, experiment: Experiment):
        deployment = self.apps_v1.read_namespaced_deployment(
            name=experiment.service,
            namespace=experiment.namespace,
        )
        containers = deployment.spec.template.spec.containers
        deployment.spec.template.spec.containers = [
            c for c in containers if c.name != "stress-ng"
        ]
        self.apps_v1.patch_namespaced_deployment(
            name=experiment.service,
            namespace=experiment.namespace,
            body=deployment,
        )
        logger.info(f"[{experiment.experiment_id}] stress-ng sidecar removed")

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
                logger.info(f"[{experiment.experiment_id}] waiting {experiment.duration_seconds}s")
                time.sleep(experiment.duration_seconds)
                self.cpu_stress_remove(experiment)

            else:
                raise ValueError(f"Unknown fault_type: {experiment.fault_type}")

        except Exception as e:
            logger.error(f"[{experiment.experiment_id}] experiment failed: {e}")
            self._record_experiment(experiment, "failed", stop_reason=str(e))
            raise

        self._record_experiment(experiment, "completed")
        logger.info(f"[{experiment.experiment_id}] experiment completed")

    def stop(self, experiment: Experiment, reason: str = "manual"):
        if experiment.fault_type == "cpu_stress":
            try:
                self.cpu_stress_remove(experiment)
            except Exception as e:
                logger.warning(f"[{experiment.experiment_id}] failed to remove stress-ng: {e}")

        self._record_experiment(experiment, "stopped", stop_reason=reason)
        logger.info(f"[{experiment.experiment_id}] experiment stopped: {reason}")
