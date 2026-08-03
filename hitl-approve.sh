#!/usr/bin/env bash
set -euo pipefail

CATE_PORT="${CATE_PORT:-8888}"
CATE_URL="http://localhost:${CATE_PORT}"

if [ "${1:-}" = "--restore" ]; then
  echo "[hitl] Restoring sandbox creation block..."
  curl -sf -X PUT "${CATE_URL}/_config" \
    -H "Content-Type: application/json" \
    -d '{
      "pre": {
        "default_action": "proceed",
        "rules": [
          {
            "toolkit": "Daytona",
            "tool": "CreateSandbox",
            "action": "block",
            "error_message": "HITL_CHECKPOINT: Sandbox creation requires human approval."
          },
          {
            "toolkit": "Daytona",
            "tool": "GitPush",
            "input_match": "branch contains main",
            "action": "block",
            "error_message": "Direct push to protected branch blocked. Use a feature branch."
          },
          {
            "toolkit": "Github",
            "tool": "CreatePullRequest",
            "action": "proceed",
            "override": {
              "inputs": {
                "draft": true
              }
            }
          }
        ]
      }
    }'
  echo ""
  echo "[hitl] Sandbox creation is now BLOCKED again."
else
  echo "[hitl] Approving sandbox creation..."
  curl -sf -X PUT "${CATE_URL}/_config" \
    -H "Content-Type: application/json" \
    -d '{
      "pre": {
        "default_action": "proceed",
        "rules": [
          {
            "toolkit": "Daytona",
            "tool": "CreateSandbox",
            "action": "proceed"
          },
          {
            "toolkit": "Daytona",
            "tool": "GitPush",
            "input_match": "branch contains main",
            "action": "block",
            "error_message": "Direct push to protected branch blocked. Use a feature branch."
          },
          {
            "toolkit": "Github",
            "tool": "CreatePullRequest",
            "action": "proceed",
            "override": {
              "inputs": {
                "draft": true
              }
            }
          }
        ]
      }
    }'
  echo ""
  echo "[hitl] Sandbox creation is now APPROVED. Claude Code will succeed on retry."
  echo "[hitl] Run './hitl-approve.sh --restore' to re-enable the block."
fi
