#!/bin/bash
# 使い方:
#   ./run.sh <alb_host> [target_rps] [origin_secret]
#
# 例 (通常トラフィック):
#   ./run.sh my-alb-xxx.ap-northeast-1.elb.amazonaws.com
#
# 例 (高負荷トラフィック):
#   ./run.sh my-alb-xxx.ap-northeast-1.elb.amazonaws.com 100 my-secret

set -euo pipefail

ALB_HOST="${1:?Usage: $0 <alb_host> [target_rps] [origin_secret]}"
TARGET_RPS="${2:-20}"
ORIGIN_SECRET="${3:-}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RESULTS_DIR="${SCRIPT_DIR}/results"
mkdir -p "${RESULTS_DIR}"

TIMESTAMP=$(date +%Y%m%dT%H%M%S)
JTL_FILE="${RESULTS_DIR}/normal-traffic-${TIMESTAMP}.jtl"

echo "[jmeter] target=${ALB_HOST}, rps=${TARGET_RPS}"

jmeter -n \
  -t "${SCRIPT_DIR}/normal-traffic.jmx" \
  -Jalb_host="${ALB_HOST}" \
  -Jtarget_rps="${TARGET_RPS}" \
  -Jorigin_secret="${ORIGIN_SECRET}" \
  -l "${JTL_FILE}" \
  -j "${RESULTS_DIR}/jmeter-${TIMESTAMP}.log"
