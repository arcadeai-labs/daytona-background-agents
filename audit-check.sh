#!/usr/bin/env bash
set -euo pipefail

CATE_PORT="${CATE_PORT:-8888}"
CATE_URL="http://localhost:${CATE_PORT}"

case "${1:-logs}" in
  logs)
    echo "[audit] Recent CATE webhook requests:"
    echo ""
    curl -sf "${CATE_URL}/_logs" | python3 -m json.tool
    ;;
  config)
    echo "[audit] Current CATE configuration:"
    echo ""
    curl -sf "${CATE_URL}/_config" | python3 -m json.tool
    ;;
  status)
    echo "[audit] CATE server status:"
    echo ""
    curl -sf "${CATE_URL}/_status" | python3 -m json.tool
    ;;
  clear)
    echo "[audit] Clearing CATE logs..."
    curl -sf -X DELETE "${CATE_URL}/_logs"
    echo "[audit] Logs cleared."
    ;;
  *)
    echo "Usage: ./audit-check.sh [logs|config|status|clear]"
    echo ""
    echo "  logs    - View all webhook requests (default)"
    echo "  config  - View current CATE rules"
    echo "  status  - View server status"
    echo "  clear   - Clear request logs"
    ;;
esac
