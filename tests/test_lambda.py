"""
Lambda 関数の主要ロジックテスト。
boto3 を moto でモックし、CloudWatch クエリ・DynamoDB 更新・評価ロジックを確認する。
"""
import pytest
import os
import sys
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "lambda"))

# Lambda 環境変数
os.environ.setdefault("PROJECT_NAME", "chaos-platform-test")
os.environ.setdefault("SLI_TABLE", "test-sli-metrics")
os.environ.setdefault("SLO_TABLE", "test-slo-definitions")
os.environ.setdefault("EXPERIMENT_TABLE", "test-experiment-history")
os.environ.setdefault("ALB_ARN_SUFFIX", "app/test-alb/abc123")
os.environ.setdefault("SNS_TOPIC_ARN", "arn:aws:sns:ap-northeast-1:123456789:test")
os.environ.setdefault("WINDOW_MINUTES", "1")
os.environ.setdefault("CW_BUFFER_SECONDS", "0")   # テスト中は待機なし

import experiment_evaluator as evaluator
import sli_calculator as sli_calc


# ---------------------------------------------------------------------------
# sli_calculator テスト
# ---------------------------------------------------------------------------

class TestSliCalculator:
    def test_get_error_rate_no_alb_suffix(self):
        """ALB_ARN_SUFFIX が空なら error_rate=0.0 を返す。"""
        import importlib
        # 一時的にALB_ARN_SUFFIXを空にする
        original = sli_calc.ALB_ARN_SUFFIX
        sli_calc.ALB_ARN_SUFFIX = ""
        try:
            rate = sli_calc.get_error_rate(datetime.now(timezone.utc), 1)
            assert rate == 0.0
        finally:
            sli_calc.ALB_ARN_SUFFIX = original

    def test_calculate_burn_rate_normal(self):
        """エラーレートが 1% のとき burn rate が正しく計算される。"""
        slo = {"slo_target": 0.999, "error_rate_threshold": 0.05, "burn_rate_threshold": 2.0}
        rate = sli_calc.calculate_burn_rate(0.01, slo)
        # error_budget = 1 - 0.999 = 0.001
        # burn_rate = 0.01 / 0.001 = 10.0
        assert abs(rate - 10.0) < 0.001

    def test_calculate_burn_rate_zero_error_budget(self):
        """SLO target が 1.0 なら burn rate は 0。"""
        slo = {"slo_target": 1.0, "error_rate_threshold": 0.0, "burn_rate_threshold": 1.0}
        rate = sli_calc.calculate_burn_rate(0.01, slo)
        assert rate == 0.0

    def test_get_latency_p95_no_suffix(self):
        """ALB_ARN_SUFFIX が空なら None を返す。"""
        original = sli_calc.ALB_ARN_SUFFIX
        sli_calc.ALB_ARN_SUFFIX = ""
        try:
            result = sli_calc.get_latency_p95_ms(datetime.now(timezone.utc), 1)
            assert result is None
        finally:
            sli_calc.ALB_ARN_SUFFIX = original


# ---------------------------------------------------------------------------
# experiment_evaluator テスト
# ---------------------------------------------------------------------------

class TestEvaluator:
    def _make_fault_window(self, minutes=5):
        start = datetime.now(timezone.utc) - timedelta(minutes=minutes + 2)
        end = start + timedelta(minutes=minutes)
        return start, end

    def _check_result(self, name, value, op, threshold, ttr_limit=None):
        """_check ヘルパーの動作確認。"""
        result = evaluator._check(name, value, op, threshold, ttr_limit_s=ttr_limit)
        return result

    def test_check_pass(self):
        r = self._check_result("error_rate", 0.005, "<=", 0.01)
        assert r["pass"] is True
        assert r["value"] == 0.005

    def test_check_fail(self):
        r = self._check_result("error_rate", 0.02, "<=", 0.01)
        assert r["pass"] is False

    def test_check_gte_pass(self):
        r = self._check_result("origin_error_rate", 0.40, ">=", 0.30)
        assert r["pass"] is True

    def test_check_no_data(self):
        r = self._check_result("p95", None, "<=", 1000)
        assert r["pass"] is None
        assert r["note"] == "no data"

    def test_safety_net_http_error_inject_stopped(self):
        """http_error_inject で status=stopped → pass (auto-stopper 発動が期待動作)。"""
        item = {"fault_type": "http_error_inject", "status": "stopped", "stop_reason": "slo_violation"}
        result = evaluator.evaluate_safety_net("http_error_inject", item)
        assert result["expected"] is True
        assert result["pass"] is True
        assert result["auto_stopper_fired"] is True

    def test_safety_net_pod_kill_stopped(self):
        """pod_kill で status=stopped → fail (auto-stopper 発動は想定外)。"""
        item = {"fault_type": "pod_kill", "status": "stopped", "stop_reason": "slo_violation"}
        result = evaluator.evaluate_safety_net("pod_kill", item)
        assert result["expected"] is False
        assert result["pass"] is False

    def test_safety_net_pod_kill_completed(self):
        """pod_kill で status=completed → pass (auto-stopper 不発動)。"""
        item = {"fault_type": "pod_kill", "status": "completed"}
        result = evaluator.evaluate_safety_net("pod_kill", item)
        assert result["expected"] is False
        assert result["pass"] is True

    @patch("experiment_evaluator.get_alb_error_rate")
    @patch("experiment_evaluator.get_alb_p95_ms")
    def test_evaluate_pod_kill_pass(self, mock_p95, mock_error):
        """pod_kill: Phase A error ≤ 1%, Phase B TTR ≤ 60s → PASS。"""
        mock_error.return_value = 0.005  # 0.5% < 1%
        mock_p95.return_value = 80.0

        start, end = self._make_fault_window()
        phase_a, phase_b = evaluator.evaluate_pod_kill(start, end)

        assert phase_a[0]["pass"] is True  # error_rate 0.5% ≤ 1%
        # Phase B: error_rate ≤ 0.5% (same 0.5%) → pass
        assert phase_b[0]["pass"] is True

    @patch("experiment_evaluator.get_alb_error_rate")
    @patch("experiment_evaluator.get_alb_p95_ms")
    def test_evaluate_cpu_stress_pass(self, mock_p95, mock_error):
        """cpu_stress: P95 ≤ 1000ms, error ≤ 5%, recovery ≤ 60s → PASS。"""
        mock_error.return_value = 0.02  # 2% < 5%
        mock_p95.return_value = 800.0   # 800ms < 1000ms

        start, end = self._make_fault_window()
        phase_a, phase_b = evaluator.evaluate_cpu_stress(start, end)

        assert phase_a[0]["pass"] is True  # p95 800ms ≤ 1000ms
        assert phase_a[1]["pass"] is True  # error 2% ≤ 5%

    @patch("experiment_evaluator.get_alb_error_rate")
    @patch("experiment_evaluator.get_alb_p95_ms")
    def test_evaluate_cpu_stress_fail_on_high_latency(self, mock_p95, mock_error):
        """cpu_stress: P95 > 1000ms → Phase A FAIL。"""
        mock_error.return_value = 0.01
        mock_p95.return_value = 1500.0  # 1500ms > 1000ms: FAIL

        start, end = self._make_fault_window()
        phase_a, phase_b = evaluator.evaluate_cpu_stress(start, end)

        assert phase_a[0]["pass"] is False

    @patch("experiment_evaluator.get_alb_error_rate")
    def test_evaluate_http_error_inject_auto_stopper_timing(self, mock_error):
        """http_error_inject: auto-stopper が 5 分以内に発動 → Phase A PASS。"""
        mock_error.return_value = 0.45  # 45% origin error rate

        now = datetime.now(timezone.utc)
        fault_start = now - timedelta(minutes=10)
        fault_end = fault_start + timedelta(minutes=3)  # 3分で停止

        item = {
            "status": "stopped",
            "stop_reason": "error_rate_violation",
            "stopped_at": fault_end.isoformat(),
        }
        phase_a, phase_b = evaluator.evaluate_http_error_inject(item, fault_start, fault_end)

        # origin_error_rate ≥ 30% → pass
        assert phase_a[0]["pass"] is True
        # auto_stopper in 3min ≤ 5min → pass
        assert phase_a[1]["pass"] is True

    @patch("experiment_evaluator.get_emf_p95_ms")
    @patch("experiment_evaluator.get_alb_p95_ms")
    def test_evaluate_network_latency_pass(self, mock_alb_p95, mock_emf_p95):
        """network_latency: service-b P95 ≥ 450ms, service-a P95 ≤ 250ms → Phase A PASS。"""
        # service-b EMF あり
        mock_emf_p95.side_effect = lambda metric, service, s, e: (
            500.0 if service == "service-b" else 180.0  # service-a: 180ms ≤ 250ms
        )
        mock_alb_p95.return_value = None  # ALB フォールバック不要

        start, end = self._make_fault_window()
        phase_a, phase_b = evaluator.evaluate_network_latency(start, end)

        assert phase_a[0]["pass"] is True   # service-b P95 500ms ≥ 450ms
        assert phase_a[1]["pass"] is True   # service-a P95 180ms ≤ 250ms

    @patch("experiment_evaluator.get_emf_p95_ms")
    @patch("experiment_evaluator.get_alb_p95_ms")
    def test_evaluate_network_latency_fail_service_a_too_slow(self, mock_alb_p95, mock_emf_p95):
        """network_latency: service-a P95 > 250ms → Envoy/cache が機能していない → FAIL。"""
        mock_emf_p95.side_effect = lambda metric, service, s, e: (
            500.0 if service == "service-b" else 400.0  # service-a: 400ms > 250ms: FAIL
        )
        mock_alb_p95.return_value = None

        start, end = self._make_fault_window()
        phase_a, phase_b = evaluator.evaluate_network_latency(start, end)

        assert phase_a[1]["pass"] is False

    @patch("experiment_evaluator.get_alb_error_rate")
    @patch("experiment_evaluator.get_alb_p95_ms")
    @patch("experiment_evaluator.time")
    def test_write_evaluation_schema(self, mock_time, mock_p95, mock_error):
        """evaluate() の出力スキーマが ADR 030 定義に準拠していること。"""
        mock_error.return_value = 0.003
        mock_p95.return_value = 80.0
        mock_time.sleep = lambda s: None  # 待機スキップ

        now = datetime.now(timezone.utc)
        item = {
            "fault_type": "pod_kill",
            "status": "completed",
            "started_at": (now - timedelta(minutes=5)).isoformat(),
            "stopped_at": (now - timedelta(minutes=1)).isoformat(),
            "duration_seconds": "300",
        }
        result = evaluator.evaluate(item)

        assert "evaluation_result" in result
        assert result["evaluation_result"] in ("pass", "fail")
        assert "evaluation_details" in result
        details = result["evaluation_details"]
        assert "phase_a_absorption" in details
        assert "phase_b_recovery" in details
        assert "safety_net" in details
        assert "evaluated_at" in result

        # 各 criterion が正しいフィールドを持つ
        for c in details["phase_a_absorption"] + details["phase_b_recovery"]:
            assert "criterion" in c
            assert "pass" in c
            assert "threshold" in c
