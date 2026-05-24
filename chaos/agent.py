import os
import re
import time
import logging
import threading
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
    memory_stress_mb: int = 150


# fault_type × service → FIS テンプレート ID の環境変数名マッピング
_FIS_FAULT_TEMPLATE_ENV = {
    "pod_kill": {
        "service-b": "FIS_TEMPLATE_POD_KILL_SERVICE_B",
        "service-c": "FIS_TEMPLATE_POD_KILL_SERVICE_C",
    },
    "cpu_stress": {
        "service-b": "FIS_TEMPLATE_CPU_STRESS_SERVICE_B",
        "service-c": "FIS_TEMPLATE_CPU_STRESS_SERVICE_C",
    },
    "memory_stress": {
        "service-b": "FIS_TEMPLATE_MEMORY_STRESS_SERVICE_B",
        "service-c": "FIS_TEMPLATE_MEMORY_STRESS_SERVICE_C",
    },
}
# ADR 057: network_latency は Envoy ベースに移行。将来の全通信遅延テスト用に残置。
_NETWORK_LATENCY_TEMPLATE_ENV = {
    "service-a": "FIS_TEMPLATE_SERVICE_A",
    "service-b": "FIS_TEMPLATE_SERVICE_B",
}
# experiment.service → (Envoy ConfigMap 名) のマッピング
# service-a Envoy = a→b 障害注入, service-b Envoy = b→c 障害注入
_ENVOY_EGRESS_MAP = {
    "service-a": "envoy-service-b-egress",
    "service-b": "envoy-service-c-egress",
}

_FIS_TERMINAL_STATES = {"completed", "stopped", "failed"}


class ChaosAgent:
    def __init__(self, kubeconfig_path: str | None = None):
        if kubeconfig_path:
            config.load_kube_config(config_file=kubeconfig_path)
        else:
            config.load_incluster_config()

        self.core_v1 = client.CoreV1Api()
        self.apps_v1 = client.AppsV1Api()
        self.networking_v1 = client.NetworkingV1Api()
        self.fis = boto3.client(
            "fis",
            region_name=os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION")),
        )
        # 実行中の FIS 実験 ID を保持 (experiment_id → fis_experiment_id)
        self._active_fis: dict[str, str] = {}

    def _record_experiment(self, experiment: Experiment, status: str, stop_reason: str = ""):
        table = dynamodb.Table(experiment.experiment_table)
        now = datetime.now(timezone.utc).isoformat()
        expires_at = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp())

        if status == "running":
            table.update_item(
                Key={
                    "experiment_id": experiment.experiment_id,
                    "started_at": experiment.started_at or now,
                },
                UpdateExpression="SET #st = :status, expires_at = :exp",
                ExpressionAttributeNames={"#st": "status"},
                ExpressionAttributeValues={":status": status, ":exp": expires_at},
            )
        else:
            update_expr = "SET #st = :status, expires_at = :exp"
            names = {"#st": "status"}
            values = {":status": status, ":exp": expires_at}
            if stop_reason:
                update_expr += ", stop_reason = :reason"
                values[":reason"] = stop_reason
            if status in ("stopped", "completed", "failed"):
                update_expr += ", stopped_at = :now"
                values[":now"] = now
            table.update_item(
                Key={
                    "experiment_id": experiment.experiment_id,
                    "started_at": experiment.started_at or now,
                },
                UpdateExpression=update_expr,
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )

    def _get_deployment(self, namespace: str, service: str):
        return self.apps_v1.read_namespaced_deployment(name=service, namespace=namespace)

    def _set_experiment_id(self, namespace: str, service: str, experiment_id: str):
        """X-Ray アノテーション用に EXPERIMENT_ID を Deployment env にセットする"""
        deployment = self._get_deployment(namespace, service)
        for container in deployment.spec.template.spec.containers:
            if container.name == service:
                if container.env is None:
                    container.env = []
                container.env = [e for e in container.env if e.name != "EXPERIMENT_ID"]
                container.env.append(client.V1EnvVar(name="EXPERIMENT_ID", value=experiment_id))
                break
        deployment.metadata.resource_version = None
        self.apps_v1.patch_namespaced_deployment(name=service, namespace=namespace, body=deployment)
        logger.info(f"EXPERIMENT_ID={experiment_id} set on {service}")

    def _clear_experiment_id(self, namespace: str, service: str):
        """実験終了後に EXPERIMENT_ID env var を削除する"""
        deployment = self._get_deployment(namespace, service)
        for container in deployment.spec.template.spec.containers:
            if container.name == service and container.env:
                before = len(container.env)
                container.env = [e for e in container.env if e.name != "EXPERIMENT_ID"]
                if len(container.env) == before:
                    return
                break
        deployment.metadata.resource_version = None
        self.apps_v1.patch_namespaced_deployment(name=service, namespace=namespace, body=deployment)
        logger.info(f"EXPERIMENT_ID cleared on {service}")

    # ---------------------------------------------------------------------------
    # FIS helpers
    # ---------------------------------------------------------------------------

    def _get_fis_template_id(self, experiment: Experiment) -> str:
        service_map = _FIS_FAULT_TEMPLATE_ENV.get(experiment.fault_type, {})
        env_key = service_map.get(experiment.service, "")
        if not env_key:
            raise ValueError(f"No FIS template mapping for fault_type={experiment.fault_type}, service={experiment.service}")
        template_id = os.environ.get(env_key, "")
        if not template_id:
            raise RuntimeError(f"Environment variable {env_key} is not set")
        return template_id

    def _fis_start_experiment(self, template_id: str, experiment_id: str) -> str:
        resp = self.fis.start_experiment(
            experimentTemplateId=template_id,
            tags={"experiment_id": experiment_id},
        )
        return resp["experiment"]["id"]

    def _fis_wait_and_monitor(self, fis_exp_id: str, experiment: Experiment) -> str:
        """FIS 実験の完了を待機しつつ DynamoDB の停止フラグを監視する。
        戻り値: "completed" | "stopped" | "failed"
        """
        table = dynamodb.Table(experiment.experiment_table)
        _emergency_triggered = False

        while True:
            time.sleep(10)

            resp = self.fis.get_experiment(id=fis_exp_id)
            state = resp["experiment"]["state"]["status"]

            if state in _FIS_TERMINAL_STATES:
                logger.info(f"[{experiment.experiment_id}] FIS experiment {state}: {fis_exp_id}")
                return state

            item = table.get_item(Key={
                "experiment_id": experiment.experiment_id,
                "started_at": experiment.started_at,
            }).get("Item", {})

            if item.get("status") == "stopped":
                logger.info(f"[{experiment.experiment_id}] external stop detected, stopping FIS")
                try:
                    self.fis.stop_experiment(id=fis_exp_id)
                except Exception as e:
                    logger.warning(f"[{experiment.experiment_id}] FIS stop error: {e}")
                return "stopped"

            if item.get("emergency_stop") and not _emergency_triggered:
                _emergency_triggered = True
                threading.Thread(
                    target=self._emergency_recover_fis,
                    args=(fis_exp_id, experiment),
                    daemon=True,
                    name=f"emergency-{experiment.experiment_id[:8]}",
                ).start()

    def _emergency_recover_fis(self, fis_exp_id: str, experiment: Experiment):
        """FIS 管理実験の緊急回復: FIS 停止で障害注入を即時解除"""
        try:
            self.fis.stop_experiment(id=fis_exp_id)
            logger.info(f"[{experiment.experiment_id}] emergency: FIS experiment stopped")
        except Exception as e:
            logger.error(f"[{experiment.experiment_id}] emergency_recover_fis failed: {e}")

    # ---------------------------------------------------------------------------
    # http_error_inject — Envoy HTTP fault filter (FIS未対応)
    # ---------------------------------------------------------------------------

    def _patch_envoy_fault(self, namespace: str, service: str, numerator: int):
        """対象サービスの Envoy egress ConfigMap の abort filter percentage を更新する"""
        configmap_name = _ENVOY_EGRESS_MAP[service]
        cm = self.core_v1.read_namespaced_config_map(name=configmap_name, namespace=namespace)
        new_yaml = re.sub(
            r"(numerator:\s*)\d+",
            rf"\g<1>{numerator}",
            cm.data["envoy.yaml"],
            count=1,
        )
        cm.data["envoy.yaml"] = new_yaml
        self.core_v1.patch_namespaced_config_map(name=configmap_name, namespace=namespace, body=cm)
        logger.info(f"envoy abort filter patched on {configmap_name}: numerator={numerator}")

    def _patch_envoy_delay(self, namespace: str, service: str, latency_ms: int):
        """対象サービスの Envoy egress ConfigMap の delay filter を更新する"""
        configmap_name = _ENVOY_EGRESS_MAP[service]
        cm = self.core_v1.read_namespaced_config_map(name=configmap_name, namespace=namespace)
        yaml_str = cm.data["envoy.yaml"]
        delay_s = f"{latency_ms / 1000:.3f}s"
        yaml_str = re.sub(r"(fixed_delay:\s*)[\d.]+s", rf"\g<1>{delay_s}", yaml_str, count=1)
        yaml_str = re.sub(
            r"(fixed_delay:.*?numerator:\s*)\d+",
            r"\g<1>100",
            yaml_str,
            count=1,
            flags=re.DOTALL,
        )
        cm.data["envoy.yaml"] = yaml_str
        self.core_v1.patch_namespaced_config_map(name=configmap_name, namespace=namespace, body=cm)
        logger.info(f"envoy delay filter patched on {configmap_name}: {latency_ms}ms")

    def _remove_envoy_delay(self, namespace: str, service: str):
        """対象サービスの Envoy egress ConfigMap の delay filter を無効化する"""
        configmap_name = _ENVOY_EGRESS_MAP[service]
        cm = self.core_v1.read_namespaced_config_map(name=configmap_name, namespace=namespace)
        yaml_str = cm.data["envoy.yaml"]
        yaml_str = re.sub(r"(fixed_delay:\s*)[\d.]+s", r"\g<1>0s", yaml_str, count=1)
        yaml_str = re.sub(
            r"(fixed_delay:.*?numerator:\s*)\d+",
            r"\g<1>0",
            yaml_str,
            count=1,
            flags=re.DOTALL,
        )
        cm.data["envoy.yaml"] = yaml_str
        self.core_v1.patch_namespaced_config_map(name=configmap_name, namespace=namespace, body=cm)
        logger.info(f"envoy delay filter disabled on {configmap_name}")

    def _rollout_restart(self, namespace: str, service: str):
        """Deployment を rolling restart して新 ConfigMap を反映する"""
        now = datetime.now(timezone.utc).isoformat()
        self.apps_v1.patch_namespaced_deployment(
            name=service,
            namespace=namespace,
            body={"spec": {"template": {"metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": now}}}}},
        )

    def http_error_inject(self, experiment: Experiment):
        numerator = int(experiment.fault_rate * 100)
        self._patch_envoy_fault(experiment.namespace, experiment.service, numerator)
        self._rollout_restart(experiment.namespace, experiment.service)
        # Envoy ローリング再起動が完了するまで待機 (~30s, ADR 055)
        time.sleep(35)
        logger.info(f"[{experiment.experiment_id}] Envoy abort filter active on {experiment.service}: {numerator}% errors")

    def http_error_remove(self, experiment: Experiment):
        self._patch_envoy_fault(experiment.namespace, experiment.service, 0)
        self._rollout_restart(experiment.namespace, experiment.service)
        logger.info(f"[{experiment.experiment_id}] Envoy abort filter disabled on {experiment.service}")

    def network_latency_inject(self, experiment: Experiment):
        self._patch_envoy_delay(experiment.namespace, experiment.service, experiment.latency_ms)
        self._rollout_restart(experiment.namespace, experiment.service)
        time.sleep(35)
        logger.info(f"[{experiment.experiment_id}] Envoy delay filter active on {experiment.service}: {experiment.latency_ms}ms")

    def network_latency_remove(self, experiment: Experiment):
        self._remove_envoy_delay(experiment.namespace, experiment.service)
        self._rollout_restart(experiment.namespace, experiment.service)
        logger.info(f"[{experiment.experiment_id}] Envoy delay filter disabled on {experiment.service}")

    def _emergency_recover(self, experiment: Experiment):
        """Envoy 系実験の緊急回復。network_latency は delay 除去のみ、http_error_inject は scale-to-0 後に fault 除去。"""
        if experiment.fault_type == "network_latency":
            try:
                self.network_latency_remove(experiment)
                logger.info(f"[{experiment.experiment_id}] emergency: delay filter disabled")
            except Exception as e:
                logger.error(f"[{experiment.experiment_id}] emergency_recover_latency failed: {e}")
            return

        namespace = experiment.namespace
        service = experiment.service
        original_replicas = 2
        try:
            deployment = self._get_deployment(namespace, service)
            original_replicas = deployment.spec.replicas or 2

            self.apps_v1.patch_namespaced_deployment_scale(
                name=service, namespace=namespace,
                body={"spec": {"replicas": 0}},
            )
            logger.info(f"[{experiment.experiment_id}] emergency: {service} scaled to 0 — sorry page active")

            self.http_error_remove(experiment)

            logger.info(f"[{experiment.experiment_id}] emergency: holding scale=0 for 30s")
            time.sleep(30)

            self.apps_v1.patch_namespaced_deployment_scale(
                name=service, namespace=namespace,
                body={"spec": {"replicas": original_replicas}},
            )
            logger.info(f"[{experiment.experiment_id}] emergency: {service} scaled back to {original_replicas} — recovering")

        except Exception as e:
            logger.error(f"[{experiment.experiment_id}] emergency_recover failed: {e}")
            try:
                self.apps_v1.patch_namespaced_deployment_scale(
                    name=service, namespace=namespace,
                    body={"spec": {"replicas": original_replicas}},
                )
            except Exception:
                pass

    # ---------------------------------------------------------------------------
    # run / stop
    # ---------------------------------------------------------------------------

    def _interruptible_sleep(self, experiment: Experiment, seconds: int, check_interval: int = 5) -> bool:
        """http_error_inject の duration 監視。FIS 実験は _fis_wait_and_monitor を使う。
        False を返した場合は呼び出し元が cleanup を行う。
        """
        table = dynamodb.Table(experiment.experiment_table)
        elapsed = 0
        _emergency_triggered = False
        while elapsed < seconds:
            time.sleep(min(check_interval, seconds - elapsed))
            elapsed += check_interval

            item = table.get_item(Key={
                "experiment_id": experiment.experiment_id,
                "started_at": experiment.started_at,
            }).get("Item", {})

            if item.get("status") == "stopped":
                logger.info(f"[{experiment.experiment_id}] external stop detected")
                return False

            if item.get("emergency_stop") and not _emergency_triggered:
                _emergency_triggered = True
                threading.Thread(
                    target=self._emergency_recover,
                    args=(experiment,),
                    daemon=True,
                    name=f"emergency-{experiment.experiment_id[:8]}",
                ).start()

        return True

    def run(self, experiment: Experiment):
        if not experiment.started_at:
            experiment.started_at = datetime.now(timezone.utc).isoformat()
        self._record_experiment(experiment, "running")

        try:
            if experiment.fault_type in ("http_error_inject", "network_latency"):
                for svc in ("service-a", "service-b", "service-c", "service-d"):
                    try:
                        self._set_experiment_id(experiment.namespace, svc, experiment.experiment_id)
                    except Exception as e:
                        logger.warning(f"[{experiment.experiment_id}] EXPERIMENT_ID patch failed on {svc}: {e}")

                if experiment.fault_type == "http_error_inject":
                    self.http_error_inject(experiment)
                else:
                    self.network_latency_inject(experiment)

                completed = self._interruptible_sleep(experiment, experiment.duration_seconds)

                if experiment.fault_type == "http_error_inject":
                    self.http_error_remove(experiment)
                else:
                    self.network_latency_remove(experiment)

                for svc in ("service-a", "service-b", "service-c", "service-d"):
                    try:
                        self._clear_experiment_id(experiment.namespace, svc)
                    except Exception as e:
                        logger.warning(f"[{experiment.experiment_id}] EXPERIMENT_ID clear failed on {svc}: {e}")

                if not completed:
                    return

            elif experiment.fault_type in ("pod_kill", "cpu_stress", "memory_stress"):
                # X-Ray アノテーション用に EXPERIMENT_ID を全サービスにセット
                for svc in ("service-a", "service-b", "service-c", "service-d"):
                    try:
                        self._set_experiment_id(experiment.namespace, svc, experiment.experiment_id)
                    except Exception as e:
                        logger.warning(f"[{experiment.experiment_id}] EXPERIMENT_ID patch failed on {svc}: {e}")

                template_id = self._get_fis_template_id(experiment)
                fis_exp_id = self._fis_start_experiment(template_id, experiment.experiment_id)
                self._active_fis[experiment.experiment_id] = fis_exp_id
                logger.info(f"[{experiment.experiment_id}] FIS experiment started: {fis_exp_id}")

                result = self._fis_wait_and_monitor(fis_exp_id, experiment)
                self._active_fis.pop(experiment.experiment_id, None)

                for svc in ("service-a", "service-b", "service-c", "service-d"):
                    try:
                        self._clear_experiment_id(experiment.namespace, svc)
                    except Exception as e:
                        logger.warning(f"[{experiment.experiment_id}] EXPERIMENT_ID clear failed on {svc}: {e}")

                if result == "failed":
                    raise RuntimeError(f"FIS experiment {fis_exp_id} failed")
                if result == "stopped":
                    return

            else:
                raise ValueError(f"Unknown fault_type: {experiment.fault_type}")

        except Exception as e:
            logger.error(f"[{experiment.experiment_id}] experiment failed: {e}")
            self._record_experiment(experiment, "failed", stop_reason=str(e))
            raise

        self._record_experiment(experiment, "completed")
        logger.info(f"[{experiment.experiment_id}] experiment completed")

    def stop(self, experiment: Experiment, reason: str = "manual"):
        if experiment.fault_type == "http_error_inject":
            try:
                self.http_error_remove(experiment)
            except Exception as e:
                logger.warning(f"[{experiment.experiment_id}] http_error cleanup failed: {e}")
        elif experiment.fault_type == "network_latency":
            try:
                self.network_latency_remove(experiment)
            except Exception as e:
                logger.warning(f"[{experiment.experiment_id}] network_latency cleanup failed: {e}")
        else:
            fis_exp_id = self._active_fis.get(experiment.experiment_id)
            if fis_exp_id:
                try:
                    self.fis.stop_experiment(id=fis_exp_id)
                except Exception as e:
                    logger.warning(f"[{experiment.experiment_id}] FIS stop failed: {e}")

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
        memory_stress_mb=int(item.get("memory_stress_mb", 150)),
    )


class ChaosAgentPoller:
    def __init__(self, agent: ChaosAgent, table_name: str, poll_interval: int = 10):
        self._agent = agent
        self._table = dynamodb.Table(table_name)
        self._poll_interval = poll_interval
        self._running: dict[str, threading.Thread] = {}

    def _scan_pending(self) -> list[dict]:
        items = []
        kwargs: dict = {"FilterExpression": Attr("status").eq("pending")}
        while True:
            resp = self._table.scan(**kwargs)
            items.extend(resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        return items

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
