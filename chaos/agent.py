import os
import re
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
    memory_stress_mb: int = 150


# fault_type → FIS テンプレート ID の環境変数名マッピング
_FIS_TEMPLATE_ENV = {
    "pod_kill":       "FIS_TEMPLATE_POD_KILL",
    "cpu_stress":     "FIS_TEMPLATE_CPU_STRESS",
    "memory_stress":  "FIS_TEMPLATE_MEMORY_STRESS",
}
_NETWORK_LATENCY_TEMPLATE_ENV = {
    "service-a": "FIS_TEMPLATE_SERVICE_A",
    "service-b": "FIS_TEMPLATE_SERVICE_B",
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

    # ---------------------------------------------------------------------------
    # FIS helpers
    # ---------------------------------------------------------------------------

    def _get_fis_template_id(self, experiment: Experiment) -> str:
        if experiment.fault_type == "network_latency":
            env_key = _NETWORK_LATENCY_TEMPLATE_ENV.get(experiment.service, "")
        else:
            env_key = _FIS_TEMPLATE_ENV.get(experiment.fault_type, "")

        if not env_key:
            raise ValueError(f"No FIS template mapping for fault_type={experiment.fault_type}")

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

    def _patch_envoy_fault(self, namespace: str, numerator: int):
        """envoy-service-b-egress ConfigMap の fault filter percentage を更新する"""
        cm = self.core_v1.read_namespaced_config_map(
            name="envoy-service-b-egress", namespace=namespace
        )
        new_yaml = re.sub(
            r"(numerator:\s*)\d+",
            rf"\g<1>{numerator}",
            cm.data["envoy.yaml"],
            count=1,
        )
        cm.data["envoy.yaml"] = new_yaml
        self.core_v1.patch_namespaced_config_map(
            name="envoy-service-b-egress", namespace=namespace, body=cm
        )
        logger.info(f"envoy fault filter patched: numerator={numerator}")

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
        self._patch_envoy_fault(experiment.namespace, numerator)
        self._rollout_restart(experiment.namespace, "service-a")
        # Envoy ローリング再起動が完了するまで待機 (~30s, ADR 055)
        time.sleep(35)
        logger.info(f"[{experiment.experiment_id}] Envoy fault filter active: {numerator}% errors")

    def http_error_remove(self, experiment: Experiment):
        self._patch_envoy_fault(experiment.namespace, 0)
        self._rollout_restart(experiment.namespace, "service-a")
        logger.info(f"[{experiment.experiment_id}] Envoy fault filter disabled")

    def _emergency_recover(self, experiment: Experiment):
        """http_error_inject の自己防衛: scale-to-0 で即断ち切り → fault 除去 → 自動回復"""
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
            if experiment.fault_type == "http_error_inject":
                self.http_error_inject(experiment)
                completed = self._interruptible_sleep(experiment, experiment.duration_seconds)
                self.http_error_remove(experiment)
                if not completed:
                    return

            elif experiment.fault_type in ("pod_kill", "cpu_stress", "memory_stress", "network_latency"):
                template_id = self._get_fis_template_id(experiment)
                fis_exp_id = self._fis_start_experiment(template_id, experiment.experiment_id)
                self._active_fis[experiment.experiment_id] = fis_exp_id
                logger.info(f"[{experiment.experiment_id}] FIS experiment started: {fis_exp_id}")

                result = self._fis_wait_and_monitor(fis_exp_id, experiment)
                self._active_fis.pop(experiment.experiment_id, None)

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


def _discover_service_b_url(networking_v1) -> str | None:
    """Ingress の ALB ホスト名から service-b URL を自動検出する。SERVICE_B_URL 未設定時のフォールバック。"""
    try:
        ingress = networking_v1.read_namespaced_ingress(name="service-b", namespace="default")
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

    service_b_url = _discover_service_b_url(chaos_agent.networking_v1)
    if service_b_url:
        logger.info(f"service-b URL from Ingress: {service_b_url}")
    else:
        service_b_url = os.environ.get("SERVICE_B_URL")
        if service_b_url:
            logger.info(f"service-b URL from env (fallback): {service_b_url}")

    traffic_interval = int(os.environ.get("TRAFFIC_GEN_INTERVAL", "30"))
    origin_secret = os.environ.get("ALB_ORIGIN_SECRET", "")

    if service_b_url:
        gen = TrafficGenerator(service_b_url, interval=traffic_interval, origin_secret=origin_secret)
        t = threading.Thread(target=gen.run_forever, daemon=True, name="traffic-gen-service-b")
        t.start()
    else:
        logger.info("SERVICE_B_URL not set, service-b traffic generator disabled")

    service_a_aggregate_url = os.environ.get("SERVICE_A_AGGREGATE_URL")
    if service_a_aggregate_url:
        gen_a = TrafficGenerator(service_a_aggregate_url, interval=traffic_interval)
        t_a = threading.Thread(target=gen_a.run_forever, daemon=True, name="traffic-gen-service-a")
        t_a.start()
    else:
        logger.info("SERVICE_A_AGGREGATE_URL not set, service-a traffic generator disabled")

    poller = ChaosAgentPoller(chaos_agent, TABLE_NAME, poll_interval=10)
    poller.run_forever()
