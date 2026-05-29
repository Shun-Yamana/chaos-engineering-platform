import os
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

# ---------------------------------------------------------------------------
# Chaos Mesh CRD 定数 (ADR 079)
# ---------------------------------------------------------------------------

_CM_GROUP   = "chaos-mesh.org"
_CM_VERSION = "v1alpha1"

# fault_type → Chaos Mesh CRD plural name
_CM_PLURAL = {
    "pod_kill":         "podchaos",
    "cpu_stress":       "stresschaos",
    "memory_stress":    "stresschaos",
    "network_latency":  "networkchaos",
    "http_error_inject": "httpchaos",
}

_CM_FAULT_TYPES = frozenset(_CM_PLURAL.keys())

# http_error_inject: experiment.service（上流）→ HTTPChaos ターゲット（下流）
# service-a の防衛を試す → service-b の Response を abort
_HTTP_CHAOS_TARGET = {
    "service-a": "service-b",
    "service-b": "service-c",
}

_FIS_TERMINAL_STATES = {"completed", "stopped", "failed"}


@dataclass
class Experiment:
    experiment_id: str
    name: str
    namespace: str
    service: str
    fault_type: str        # pod_kill | cpu_stress | memory_stress | http_error_inject | network_latency | node_failure | az_isolation
    duration_seconds: int
    error_rate_threshold: float
    burn_rate_threshold: float
    slack_webhook_url: str | None
    experiment_table: str
    started_at: str | None = None
    latency_ms: int = 500
    fault_rate: float = 0.5
    memory_stress_mb: int = 150


class ChaosAgent:
    def __init__(self, kubeconfig_path: str | None = None):
        if kubeconfig_path:
            config.load_kube_config(config_file=kubeconfig_path)
        else:
            config.load_incluster_config()

        self.core_v1    = client.CoreV1Api()
        self.apps_v1    = client.AppsV1Api()
        self.custom_api = client.CustomObjectsApi()

        self.fis = boto3.client(
            "fis",
            region_name=os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION")),
        )
        self._active_fis: dict[str, str] = {}

    # ---------------------------------------------------------------------------
    # DynamoDB helpers
    # ---------------------------------------------------------------------------

    def _record_experiment(self, experiment: Experiment, status: str, stop_reason: str = ""):
        table = dynamodb.Table(experiment.experiment_table)
        now = datetime.now(timezone.utc).isoformat()
        expires_at = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp())

        if status == "running":
            table.update_item(
                Key={"experiment_id": experiment.experiment_id, "started_at": experiment.started_at or now},
                UpdateExpression="SET #st = :status, expires_at = :exp",
                ExpressionAttributeNames={"#st": "status"},
                ExpressionAttributeValues={":status": status, ":exp": expires_at},
            )
        else:
            update_expr = "SET #st = :status, expires_at = :exp"
            names  = {"#st": "status"}
            values = {":status": status, ":exp": expires_at}
            if stop_reason:
                update_expr += ", stop_reason = :reason"
                values[":reason"] = stop_reason
            if status in ("stopped", "completed", "failed"):
                update_expr += ", stopped_at = :now"
                values[":now"] = now
            table.update_item(
                Key={"experiment_id": experiment.experiment_id, "started_at": experiment.started_at or now},
                UpdateExpression=update_expr,
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )

    def _get_dynamodb_item(self, experiment: Experiment) -> dict:
        table = dynamodb.Table(experiment.experiment_table)
        return table.get_item(Key={
            "experiment_id": experiment.experiment_id,
            "started_at": experiment.started_at,
        }).get("Item", {})

    # ---------------------------------------------------------------------------
    # EXPERIMENT_ID (X-Ray アノテーション用)
    # ---------------------------------------------------------------------------

    def _set_experiment_id(self, namespace: str, service: str, experiment_id: str):
        deployment = self.apps_v1.read_namespaced_deployment(name=service, namespace=namespace)
        for container in deployment.spec.template.spec.containers:
            if container.name == service:
                if container.env is None:
                    container.env = []
                container.env = [e for e in container.env if e.name != "EXPERIMENT_ID"]
                container.env.append(client.V1EnvVar(name="EXPERIMENT_ID", value=experiment_id))
                break
        deployment.metadata.resource_version = None
        self.apps_v1.patch_namespaced_deployment(name=service, namespace=namespace, body=deployment)

    def _clear_experiment_id(self, namespace: str, service: str):
        deployment = self.apps_v1.read_namespaced_deployment(name=service, namespace=namespace)
        for container in deployment.spec.template.spec.containers:
            if container.name == service and container.env:
                before = len(container.env)
                container.env = [e for e in container.env if e.name != "EXPERIMENT_ID"]
                if len(container.env) == before:
                    return
                break
        deployment.metadata.resource_version = None
        self.apps_v1.patch_namespaced_deployment(name=service, namespace=namespace, body=deployment)

    def _set_experiment_ids(self, experiment: Experiment):
        for svc in ("service-a", "service-b", "service-c", "service-d"):
            try:
                self._set_experiment_id(experiment.namespace, svc, experiment.experiment_id)
            except Exception as e:
                logger.warning(f"[{experiment.experiment_id}] EXPERIMENT_ID patch failed on {svc}: {e}")

    def _clear_experiment_ids(self, experiment: Experiment):
        for svc in ("service-a", "service-b", "service-c", "service-d"):
            try:
                self._clear_experiment_id(experiment.namespace, svc)
            except Exception as e:
                logger.warning(f"[{experiment.experiment_id}] EXPERIMENT_ID clear failed on {svc}: {e}")

    # ---------------------------------------------------------------------------
    # Chaos Mesh — CRD 管理 (ADR 079)
    # ---------------------------------------------------------------------------

    def _cm_cr_name(self, experiment: Experiment) -> str:
        return f"chaos-{experiment.experiment_id[:8]}"

    def _build_chaos_cr(self, experiment: Experiment) -> tuple[str, dict]:
        """(plural, manifest) を返す"""
        name   = self._cm_cr_name(experiment)
        plural = _CM_PLURAL[experiment.fault_type]

        base = {
            "apiVersion": f"{_CM_GROUP}/{_CM_VERSION}",
            "metadata": {
                "name": name,
                "namespace": experiment.namespace,
            },
            "spec": {
                "mode": "one" if experiment.fault_type == "pod_kill" else "all",
                "selector": {
                    "namespaces": [experiment.namespace],
                    "labelSelectors": {"app": experiment.service},
                },
            },
        }

        if experiment.fault_type == "pod_kill":
            base["kind"] = "PodChaos"
            base["spec"]["action"] = "pod-kill"

        elif experiment.fault_type == "cpu_stress":
            base["kind"] = "StressChaos"
            base["spec"]["stressors"] = {"cpu": {"workers": 2, "load": 80}}
            base["spec"]["duration"] = f"{experiment.duration_seconds}s"

        elif experiment.fault_type == "memory_stress":
            base["kind"] = "StressChaos"
            base["spec"]["stressors"] = {"memory": {"workers": 1, "size": f"{experiment.memory_stress_mb}MB"}}
            base["spec"]["duration"] = f"{experiment.duration_seconds}s"

        elif experiment.fault_type == "network_latency":
            base["kind"] = "NetworkChaos"
            base["spec"]["action"] = "delay"
            base["spec"]["delay"] = {
                "latency": f"{experiment.latency_ms}ms",
                "correlation": "0",
                "jitter": "0ms",
            }
            base["spec"]["direction"] = "to"
            base["spec"]["duration"] = f"{experiment.duration_seconds}s"

        elif experiment.fault_type == "http_error_inject":
            target_svc = _HTTP_CHAOS_TARGET.get(experiment.service, experiment.service)
            base["kind"] = "HTTPChaos"
            base["spec"]["selector"]["labelSelectors"]["app"] = target_svc
            base["spec"]["target"] = "Response"
            base["spec"]["port"] = 8000
            base["spec"]["path"] = "*"
            base["spec"]["abort"] = True
            base["spec"]["percent"] = int(experiment.fault_rate * 100)
            base["spec"]["duration"] = f"{experiment.duration_seconds}s"

        return plural, base

    def _cm_apply(self, plural: str, manifest: dict, namespace: str):
        self.custom_api.create_namespaced_custom_object(
            group=_CM_GROUP, version=_CM_VERSION,
            namespace=namespace, plural=plural, body=manifest,
        )

    def _cm_delete(self, plural: str, name: str, namespace: str):
        try:
            self.custom_api.delete_namespaced_custom_object(
                group=_CM_GROUP, version=_CM_VERSION,
                namespace=namespace, plural=plural, name=name,
            )
        except ApiException as e:
            if e.status != 404:
                raise

    def _cm_condition(self, plural: str, name: str, namespace: str, condition_type: str) -> bool:
        try:
            obj = self.custom_api.get_namespaced_custom_object(
                group=_CM_GROUP, version=_CM_VERSION,
                namespace=namespace, plural=plural, name=name,
            )
            for c in obj.get("status", {}).get("conditions", []):
                if c.get("type") == condition_type and c.get("status") == "True":
                    return True
        except ApiException:
            pass
        return False

    def _cm_wait_condition(self, plural: str, name: str, namespace: str,
                           condition_type: str, timeout: int = 60) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._cm_condition(plural, name, namespace, condition_type):
                return True
            time.sleep(3)
        return False

    def _cm_run(self, experiment: Experiment):
        """Chaos Mesh 実験の実行・監視・クリーンアップ"""
        plural, manifest = self._build_chaos_cr(experiment)
        name = self._cm_cr_name(experiment)
        namespace = experiment.namespace

        self._cm_apply(plural, manifest, namespace)
        logger.info(f"[{experiment.experiment_id}] ChaosMesh CR applied: {manifest['kind']}/{name}")

        # 注入確認（最大60秒）
        if not self._cm_wait_condition(plural, name, namespace, "AllInjected", timeout=60):
            logger.warning(f"[{experiment.experiment_id}] AllInjected not reached within 60s, continuing")

        if experiment.fault_type == "pod_kill":
            # pod-kill は instantaneous — 回復確認して終了
            self._cm_wait_condition(plural, name, namespace, "AllRecovered", timeout=30)
            self._cm_delete(plural, name, namespace)
            return

        # duration ベースの実験：DynamoDB 停止フラグを監視しながら待機
        completed = self._interruptible_sleep(experiment, experiment.duration_seconds)

        # CR を削除して回復をトリガー（duration 自然期限の場合も明示的に削除）
        self._cm_delete(plural, name, namespace)

        if not self._cm_wait_condition(plural, name, namespace, "AllRecovered", timeout=30):
            logger.warning(f"[{experiment.experiment_id}] AllRecovered not confirmed within 30s after delete")

        if not completed:
            logger.info(f"[{experiment.experiment_id}] ChaosMesh experiment stopped early")

    def _cm_emergency_stop(self, experiment: Experiment):
        """Chaos Mesh CR を即時削除して障害注入を解除"""
        plural = _CM_PLURAL.get(experiment.fault_type, "")
        if not plural:
            return
        name = self._cm_cr_name(experiment)
        try:
            self._cm_delete(plural, name, experiment.namespace)
            logger.info(f"[{experiment.experiment_id}] emergency: ChaosMesh CR deleted")
        except Exception as e:
            logger.error(f"[{experiment.experiment_id}] emergency_cm_delete failed: {e}")

    # ---------------------------------------------------------------------------
    # FIS — node_failure 用（将来実装: EC2 ノード終了）
    # ---------------------------------------------------------------------------

    def _fis_start(self, template_id: str, experiment_id: str) -> str:
        resp = self.fis.start_experiment(
            experimentTemplateId=template_id,
            tags={"experiment_id": experiment_id},
        )
        return resp["experiment"]["id"]

    def _fis_wait_and_monitor(self, fis_exp_id: str, experiment: Experiment) -> str:
        table = dynamodb.Table(experiment.experiment_table)
        while True:
            time.sleep(10)
            resp  = self.fis.get_experiment(id=fis_exp_id)
            state = resp["experiment"]["state"]["status"]
            if state in _FIS_TERMINAL_STATES:
                return state
            item = table.get_item(Key={
                "experiment_id": experiment.experiment_id,
                "started_at": experiment.started_at,
            }).get("Item", {})
            if item.get("status") == "stopped":
                try:
                    self.fis.stop_experiment(id=fis_exp_id)
                except Exception:
                    pass
                return "stopped"

    # ---------------------------------------------------------------------------
    # _interruptible_sleep: DynamoDB 停止フラグ監視付き待機
    # ---------------------------------------------------------------------------

    def _interruptible_sleep(self, experiment: Experiment, seconds: int, check_interval: int = 5) -> bool:
        """False を返した場合は外部停止。呼び出し元が cleanup する。"""
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
                return False

            if item.get("emergency_stop") and not _emergency_triggered:
                _emergency_triggered = True
                threading.Thread(
                    target=self._cm_emergency_stop, args=(experiment,),
                    daemon=True, name=f"emergency-{experiment.experiment_id[:8]}",
                ).start()

        return True

    # ---------------------------------------------------------------------------
    # run / stop
    # ---------------------------------------------------------------------------

    def run(self, experiment: Experiment):
        if not experiment.started_at:
            experiment.started_at = datetime.now(timezone.utc).isoformat()
        self._record_experiment(experiment, "running")

        try:
            self._set_experiment_ids(experiment)

            if experiment.fault_type in _CM_FAULT_TYPES:
                self._cm_run(experiment)

            elif experiment.fault_type == "node_failure":
                template_id = os.environ.get("FIS_TEMPLATE_NODE_FAILURE", "")
                if not template_id:
                    raise RuntimeError("FIS_TEMPLATE_NODE_FAILURE is not set")
                fis_exp_id = self._fis_start(template_id, experiment.experiment_id)
                self._active_fis[experiment.experiment_id] = fis_exp_id
                result = self._fis_wait_and_monitor(fis_exp_id, experiment)
                self._active_fis.pop(experiment.experiment_id, None)
                if result == "failed":
                    raise RuntimeError(f"FIS experiment {fis_exp_id} failed")
                if result == "stopped":
                    self._clear_experiment_ids(experiment)
                    return

            elif experiment.fault_type == "az_isolation":
                template_id = os.environ.get("FIS_TEMPLATE_AZ_ISOLATION", "")
                if not template_id:
                    raise RuntimeError("FIS_TEMPLATE_AZ_ISOLATION is not set")
                fis_exp_id = self._fis_start(template_id, experiment.experiment_id)
                self._active_fis[experiment.experiment_id] = fis_exp_id
                result = self._fis_wait_and_monitor(fis_exp_id, experiment)
                self._active_fis.pop(experiment.experiment_id, None)
                if result == "failed":
                    raise RuntimeError(f"FIS experiment {fis_exp_id} failed")
                if result == "stopped":
                    self._clear_experiment_ids(experiment)
                    return

            else:
                raise ValueError(f"Unknown fault_type: {experiment.fault_type}")

        except Exception as e:
            logger.error(f"[{experiment.experiment_id}] experiment failed: {e}")
            self._record_experiment(experiment, "failed", stop_reason=str(e))
            self._clear_experiment_ids(experiment)
            raise

        self._clear_experiment_ids(experiment)
        self._record_experiment(experiment, "completed")
        logger.info(f"[{experiment.experiment_id}] experiment completed")

    def stop(self, experiment: Experiment, reason: str = "manual"):
        if experiment.fault_type in _CM_FAULT_TYPES:
            self._cm_emergency_stop(experiment)
        elif experiment.fault_type in ("node_failure", "az_isolation"):
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
        self._agent         = agent
        self._table         = dynamodb.Table(table_name)
        self._poll_interval = poll_interval
        self._running: dict[str, threading.Thread] = {}

    def _scan_pending(self) -> list[dict]:
        items  = []
        kwargs: dict = {"FilterExpression": Attr("status").eq("pending")}
        while True:
            resp = self._table.scan(**kwargs)
            items.extend(resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        return items

    def _claim(self, item: dict) -> bool:
        try:
            self._table.update_item(
                Key={"experiment_id": item["experiment_id"], "started_at": item["started_at"]},
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

    def _check_traffic_control(self):
        """DynamoDB の TRAFFIC_CONTROL 項目に基づき traffic-generator の replicas を同期する"""
        try:
            item = self._table.get_item(
                Key={"experiment_id": "TRAFFIC_CONTROL", "started_at": "SINGLETON"}
            ).get("Item", {})
            want = 1 if item.get("running") else 0
            scale = self._agent.apps_v1.read_namespaced_deployment_scale(
                name="traffic-generator", namespace="default"
            )
            if scale.spec.replicas != want:
                scale.spec.replicas = want
                self._agent.apps_v1.patch_namespaced_deployment_scale(
                    name="traffic-generator", namespace="default", body=scale
                )
                logger.info(f"traffic-generator scaled to {want}")
        except Exception as e:
            logger.warning(f"traffic control check failed: {e}")

    def _poll(self):
        done = [eid for eid, t in self._running.items() if not t.is_alive()]
        for eid in done:
            self._running.pop(eid, None)

        self._check_traffic_control()

        for item in self._scan_pending():
            eid = item["experiment_id"]
            if eid in self._running:
                continue
            if not self._claim(item):
                continue
            experiment = _item_to_experiment(item)
            thread = threading.Thread(
                target=self._run_experiment, args=(experiment,),
                daemon=True, name=f"experiment-{eid[:8]}",
            )
            self._running[eid] = thread
            thread.start()
            logger.info(f"[{eid}] experiment thread started: {experiment.fault_type}")

    def _startup_cleanup(self):
        """起動時: 前回の agent クラッシュ/再起動で残った running 実験を stopped に遷移させる。
        Chaos Mesh CR が残っていれば削除して障害注入を解除する。"""
        items = []
        kwargs: dict = {"FilterExpression": Attr("status").eq("running")}
        while True:
            resp = self._table.scan(**kwargs)
            items.extend(resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

        for item in items:
            eid = item.get("experiment_id", "")
            if eid == "TRAFFIC_CONTROL":
                continue
            logger.warning(f"[{eid}] orphaned running experiment found on startup — cleaning up")
            try:
                experiment = _item_to_experiment(item)
                plural = _CM_PLURAL.get(experiment.fault_type)
                if plural:
                    name = self._agent._cm_cr_name(experiment)
                    self._agent._cm_delete(plural, name, experiment.namespace)
            except Exception as e:
                logger.warning(f"[{eid}] CR cleanup failed (may not exist): {e}")
            try:
                self._table.update_item(
                    Key={"experiment_id": eid, "started_at": item["started_at"]},
                    UpdateExpression="SET #st = :stopped, stop_reason = :reason, stopped_at = :now",
                    ConditionExpression=Attr("status").eq("running"),
                    ExpressionAttributeNames={"#st": "status"},
                    ExpressionAttributeValues={
                        ":stopped": "stopped",
                        ":reason": "agent_restart",
                        ":now": datetime.now(timezone.utc).isoformat(),
                    },
                )
                logger.info(f"[{eid}] marked as stopped (agent_restart)")
            except Exception as e:
                logger.warning(f"[{eid}] DynamoDB update failed: {e}")

    def run_forever(self):
        logger.info(f"chaos-agent poller started (interval={self._poll_interval}s, table={self._table.name})")
        self._startup_cleanup()
        while True:
            try:
                self._poll()
            except Exception as e:
                logger.error(f"poll error: {e}")
            time.sleep(self._poll_interval)


if __name__ == "__main__":
    if not TABLE_NAME:
        raise RuntimeError("TABLE_NAME environment variable is required")
    ChaosAgentPoller(ChaosAgent(), TABLE_NAME, poll_interval=10).run_forever()
