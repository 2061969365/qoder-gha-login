#!/usr/bin/env bash
# run_local.sh — 在本机 / 容器 / WSL 里走一遍 Qoder 设备码登录
#
#   bash run_local.sh                 # 打印链接，等你授权，成功后写 qoder-token.json
#   bash run_local.sh --print-only    # 只打印链接
#   TIMEOUT=900 bash run_local.sh     # 改轮询超时
set -euo pipefail
cd "$(dirname "$0")"

TIMEOUT="${TIMEOUT:-600}"
STATE=".qoder-device.json"
OUT="${OUT:-qoder-token.json}"

PY="$(command -v python3 || command -v python)"
if [ -z "${PY}" ]; then
  echo "需要 python3" >&2
  exit 1
fi

if [ "${1:-}" = "--print-only" ]; then
  "${PY}" qoder_device_login.py --print-only --state-out "$STATE"
  exit 0
fi

"${PY}" qoder_device_login.py --print-only --state-out "$STATE"
echo
echo "浏览器打开上面的链接完成登录/注册（没有账号就点 Sign up），本脚本会自动等待…"
"${PY}" qoder_device_login.py --resume "$STATE" --out "$OUT" --timeout "$TIMEOUT"

echo
echo "token 已写入 ${OUT}；喂给 CLI："
echo "  export QODER_PERSONAL_ACCESS_TOKEN=\"\$(${PY} -c 'import json;print(json.load(open(\"${OUT}\"))[\"token\"])')\""
