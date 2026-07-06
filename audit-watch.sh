#!/usr/bin/env bash
# Live governance ticker: streams every CATE decision as it happens.
# Run in a second terminal next to run.sh — this is the "what is the
# agent doing, and what did policy say" pane.
set -euo pipefail

CATE_PORT="${CATE_PORT:-8888}"
CATE_URL="http://localhost:${CATE_PORT}"

GREEN=$'\033[32m'; RED=$'\033[31m'; YELLOW=$'\033[33m'; DIM=$'\033[2m'; BOLD=$'\033[1m'; RESET=$'\033[0m'

echo "${BOLD}── Governance feed (CATE @ :${CATE_PORT}) ─────────────────────────${RESET}"
echo "${DIM}every tool call the agent makes, and what policy decided${RESET}"
echo ""

seen=0
while true; do
  logs=$(curl -sf "${CATE_URL}/_logs" 2>/dev/null || echo '{"logs":[]}')
  total=$(echo "$logs" | jq '.logs | length')
  if [ "$total" -gt "$seen" ]; then
    echo "$logs" | jq -r --argjson n "$seen" '.logs[$n:][] |
      [ (.timestamp[11:19]),
        (.body.tool.toolkit // "?"),
        (.body.tool.name // "?" | sub("^.*\\."; "")),
        (.response.code),
        (.response.error_message // (if .response.override then "inputs rewritten by policy" else "" end))
      ] | @tsv' | while IFS=$'\t' read -r ts toolkit tool code msg; do
        case "$code" in
          OK)           icon="${GREEN}✓ ALLOWED${RESET}" ;;
          CHECK_FAILED) icon="${RED}✗ BLOCKED${RESET}" ;;
          *)            icon="${YELLOW}${code}${RESET}" ;;
        esac
        line="$(printf '%s  %-8s %-22s %b' "$ts" "$toolkit" "$tool" "$icon")"
        echo "$line"
        [ -n "$msg" ] && echo "         ${DIM}└─ ${msg}${RESET}"
      done
    seen=$total
  fi
  sleep 1
done
