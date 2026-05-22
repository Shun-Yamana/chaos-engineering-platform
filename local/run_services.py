"""
ローカル起動スクリプト: 4サービスを subprocess で一括管理する。
使い方:
  pip install aws-xray-sdk>=2.14.0   # 初回のみ
  python local/run_services.py       # 全サービス起動
  Ctrl+C で全プロセスを停止
"""

import os
import sys
import signal
import subprocess
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SERVICES = [
    {
        "name": "service-c",
        "dir": os.path.join(ROOT, "services", "service-c"),
        "port": 8002,
        "env": {},
    },
    {
        "name": "service-d",
        "dir": os.path.join(ROOT, "services", "service-d"),
        "port": 8003,
        "env": {},
    },
    {
        "name": "service-b",
        "dir": os.path.join(ROOT, "services", "service-b"),
        "port": 8001,
        "env": {"SERVICE_C_INTERNAL_URL": "http://localhost:8002"},
    },
    {
        "name": "service-a",
        "dir": os.path.join(ROOT, "services", "service-a"),
        "port": 8080,
        "env": {
            "SERVICE_B_INTERNAL_URL": "http://localhost:8001",
            "SERVICE_D_INTERNAL_URL": "http://localhost:8003",
            "CORS_ORIGINS": "*",
        },
    },
]

# X-Ray デーモンなしでも落ちないよう設定
_BASE_ENV = {
    **os.environ,
    "AWS_XRAY_CONTEXT_MISSING": "LOG_ERROR",
    "AWS_DEFAULT_REGION": "ap-northeast-1",
    "AWS_ACCESS_KEY_ID": "local",
    "AWS_SECRET_ACCESS_KEY": "local",
}


def _wait_healthy(name: str, port: int, retries: int = 20) -> bool:
    url = f"http://localhost:{port}/health"
    for i in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=2):
                print(f"  [ok] {name} is healthy (port {port})")
                return True
        except Exception:
            time.sleep(0.5)
    print(f"  [!!] {name} did not become healthy within {retries * 0.5}s")
    return False


procs: list[subprocess.Popen] = []


def _stop_all():
    for p in procs:
        try:
            p.terminate()
        except Exception:
            pass
    for p in procs:
        try:
            p.wait(timeout=5)
        except Exception:
            p.kill()
    print("\nAll services stopped.")


def main():
    signal.signal(signal.SIGINT, lambda *_: (_stop_all(), sys.exit(0)))
    signal.signal(signal.SIGTERM, lambda *_: (_stop_all(), sys.exit(0)))

    print("Starting services...\n")
    for svc in SERVICES:
        env = {**_BASE_ENV, **svc["env"]}
        cmd = [sys.executable, "-m", "uvicorn", "main:app",
               "--host", "0.0.0.0", "--port", str(svc["port"])]
        p = subprocess.Popen(cmd, cwd=svc["dir"], env=env)
        procs.append(p)
        print(f"  started {svc['name']} (pid={p.pid}, port={svc['port']})")

        # c/d を先に起動してから b/a を待つ
        if svc["name"] in ("service-c", "service-d"):
            time.sleep(0.5)
        else:
            _wait_healthy(svc["name"], svc["port"])

    # service-a はすこし遅いので改めて確認
    _wait_healthy("service-a", 8080)

    print("\nAll services ready. Endpoints:")
    print("  service-a  http://localhost:8080")
    print("  service-b  http://localhost:8001")
    print("  service-c  http://localhost:8002")
    print("  service-d  http://localhost:8003")
    print("\nPress Ctrl+C to stop all.\n")

    # プロセスを監視し、クラッシュを検知したら報告する
    while True:
        for svc, p in zip(SERVICES, procs):
            ret = p.poll()
            if ret is not None:
                print(f"[!!] {svc['name']} exited with code {ret}")
        time.sleep(5)


if __name__ == "__main__":
    main()
