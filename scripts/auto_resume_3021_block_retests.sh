#!/usr/bin/env bash
set -u -o pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLS_MD="$ROOT/TOOLS.md"
TASK_DIR="$ROOT/scripts/result/3021-取暖器-通用垂类-0406/测试模式04111203"
MANIFEST="$ROOT/scripts/_runtime/测试模式04111203/weekly/manifest.json"
LOG_FILE="$TASK_DIR/auto_resume_block_retests.log"
PID_FILE="$TASK_DIR/auto_resume_block_retests.pid"
CHECK_INTERVAL="${CHECK_INTERVAL:-60}"
ONLY_LIST="pkg-07-r2-timeout-1,pkg-08-r2-volume-low,pkg-09-r2-speed-1,pkg-10-r2-vol-1,pkg-11-r2-compress-1,pkg-17-r2-baud-2400,pkg-18-r2-loglevel-0"

mkdir -p "$TASK_DIR"

if [[ -f "$PID_FILE" ]]; then
  existing_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "$existing_pid" ]] && kill -0 "$existing_pid" 2>/dev/null; then
    exit 0
  fi
fi

echo "$$" > "$PID_FILE"

timestamp() {
  date '+%Y-%m-%d %H:%M:%S'
}

log() {
  printf '[%s] %s\n' "$(timestamp)" "$*" | tee -a "$LOG_FILE"
}

cleanup() {
  if [[ -f "$PID_FILE" ]] && [[ "$(cat "$PID_FILE" 2>/dev/null || true)" == "$$" ]]; then
    rm -f "$PID_FILE"
  fi
}

trap cleanup EXIT

extract_token() {
  if [[ ! -f "$TOOLS_MD" ]]; then
    return 0
  fi
  grep '^LISTENAI_TOKEN=' "$TOOLS_MD" | tail -n1 | cut -d'=' -f2- | tr -d '\r\n'
}

validate_token() {
  local token="$1"
  python3 - "$token" <<'PY'
import json
import sys

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

token = sys.argv[1].strip()
resp = requests.get(
    "https://integration-platform.listenai.com/ai-voice-firmwares/api/backend/biz/category/options",
    params={"category": "PRODUCTION"},
    headers={"token": token},
    verify=False,
    timeout=30,
)
payload = resp.json()
print(json.dumps(payload, ensure_ascii=False))
if payload.get("code") == 200:
    raise SystemExit(0)
raise SystemExit(1)
PY
}

run_retests() {
  local token="$1"
  (
    cd "$ROOT"
    export LISTENAI_TOKEN="$token"
    python3 -X utf8 scripts/py/listenai_round2_targeted_retests.py \
      --task-dir "$TASK_DIR" \
      --manifest "$MANIFEST" \
      --only "$ONLY_LIST" \
      --send-email
  )
}

last_seen_token=""
log "watcher started; interval=${CHECK_INTERVAL}s"
log "task_dir=$TASK_DIR"
log "manifest=$MANIFEST"

while true; do
  token="$(extract_token)"
  if [[ -z "$token" ]]; then
    log "TOOLS.md 中暂无 LISTENAI_TOKEN，等待 ${CHECK_INTERVAL}s"
    sleep "$CHECK_INTERVAL"
    continue
  fi

  if [[ "$token" != "$last_seen_token" ]]; then
    log "检测到 token 变化，开始校验可用性"
    last_seen_token="$token"
  fi

  if validate_token "$token" >>"$LOG_FILE" 2>&1; then
    log "token 校验通过，开始执行剩余 7 个单参数补测"
    if run_retests "$token" >>"$LOG_FILE" 2>&1; then
      log "补测完成，报告已更新并触发邮件发送"
      exit 0
    fi
    rc=$?
    log "补测执行失败，exit=$rc；${CHECK_INTERVAL}s 后重试"
  else
    log "token 当前不可用；${CHECK_INTERVAL}s 后重试"
  fi

  sleep "$CHECK_INTERVAL"
done
