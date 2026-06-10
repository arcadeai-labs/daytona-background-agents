#!/usr/bin/env bash
set -euo pipefail

ENGINE_URL="${ENGINE_URL:?Set ENGINE_URL (e.g. https://api.arcade.dev)}"
API_KEY="${ARCADE_API_KEY:?Set ARCADE_API_KEY}"
CATE_WEBHOOK_URL="${CATE_WEBHOOK_URL:-http://localhost:8888}"

log() { echo "[setup] $*"; }
fail() { echo "[setup] ERROR: $*" >&2; exit 1; }

api() {
  local method="$1" path="$2"
  shift 2
  curl -sf -X "$method" "${ENGINE_URL}${path}" \
    -H "Authorization: Bearer ${API_KEY}" \
    -H "Content-Type: application/json" \
    "$@"
}

# ── 1. Health check ────────────────────────────────────────────────
log "Checking engine health..."
health=$(api GET /v1/health || true)
if [ -z "$health" ] || [ "$(echo "$health" | jq -r '.healthy')" != "true" ]; then
  fail "Engine at ${ENGINE_URL} is not healthy. Response: ${health:-none}"
fi
version=$(echo "$health" | jq -r '.version // "unknown"')
log "Engine healthy (version: ${version})"

# ── 2. Register CATE webhook plugin ────────────────────────────────
log "Creating CATE webhook plugin..."
plugin_response=$(api POST /v1/admin/plugins -d "$(cat <<JSON
{
  "name": "demo-cate-hooks",
  "plugin_type": "webhook",
  "description": "Daytona demo governance hooks",
  "status": "active",
  "webhook_config": {
    "endpoints": {
      "access": {
        "url": "${CATE_WEBHOOK_URL}/access",
        "failure_mode": "fail_open",
        "phase": "before"
      },
      "pre": {
        "url": "${CATE_WEBHOOK_URL}/pre",
        "failure_mode": "fail_closed",
        "phase": "before"
      },
      "post": {
        "url": "${CATE_WEBHOOK_URL}/post",
        "failure_mode": "fail_open",
        "phase": "after"
      }
    },
    "auth": {
      "type": "bearer",
      "token": "demo-secret"
    },
    "health_check_path": "/health"
  }
}
JSON
)")

plugin_id=$(echo "$plugin_response" | jq -r '.id')
if [ -z "$plugin_id" ] || [ "$plugin_id" = "null" ]; then
  fail "Failed to create plugin. Response: ${plugin_response}"
fi
log "Plugin created: ${plugin_id}"

# ── 3. Register hooks ──────────────────────────────────────────────
hook_ids=()
for hook_point in "tool.access" "tool.pre" "tool.post"; do
  short_name="${hook_point##*.}"
  log "Creating hook: demo-${short_name} (${hook_point})..."

  hook_response=$(api POST /v1/admin/hooks -d "$(cat <<JSON
{
  "name": "demo-${short_name}",
  "plugin_id": "${plugin_id}",
  "hook_point": "${hook_point}",
  "phase": "before",
  "failure_mode": "fail_closed",
  "status": "active",
  "priority": 1
}
JSON
)")

  hook_id=$(echo "$hook_response" | jq -r '.id')
  if [ -z "$hook_id" ] || [ "$hook_id" = "null" ]; then
    fail "Failed to create ${hook_point} hook. Response: ${hook_response}"
  fi
  hook_ids+=("$hook_id")
  log "Hook created: ${hook_id}"
done

# ── 4. Create MCP gateway ──────────────────────────────────────────
log "Creating MCP gateway..."
gateway_response=$(api POST /v1/gateways -d "$(cat <<JSON
{
  "name": "demo-gateway",
  "description": "Daytona demo - all tools",
  "allowed_tools": ["Linear.*", "Slack.*", "GitHub.*", "GoogleDocs.*", "Gmail.*", "Daytona.*"]
}
JSON
)")

gateway_id=$(echo "$gateway_response" | jq -r '.id')
if [ -z "$gateway_id" ] || [ "$gateway_id" = "null" ]; then
  fail "Failed to create gateway. Response: ${gateway_response}"
fi
log "Gateway created: ${gateway_id}"

# ── 5. Summary ──────────────────────────────────────────────────────
cat <<SUMMARY

============================================================
  DEMO SETUP COMPLETE
============================================================
  Engine:     ${ENGINE_URL} (v${version})
  Plugin:     ${plugin_id}
  Hooks:      ${hook_ids[0]} (access)
              ${hook_ids[1]} (pre)
              ${hook_ids[2]} (post)
  Gateway:    ${gateway_id}
  Webhook:    ${CATE_WEBHOOK_URL}

  Update .mcp.json gateway URL:
    https://${ENGINE_URL#https://}/mcp/v1/gateways/${gateway_id}
============================================================
SUMMARY
