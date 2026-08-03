#!/usr/bin/env bash
# Participant onboarding: API key in, working MCP gateway out. No dashboard
# clicking — the gateway is created through the same management API run.sh uses.
#
#   ./onboard.sh
#
# Prompts for what it can't discover, creates (or updates) a gateway with two
# read-only GitHub tools, verifies the MCP handshake, and writes .mcp.json.
set -euo pipefail

ENGINE_URL="${ENGINE_URL:-https://api.arcade.dev}"

ask() { # ask VAR "prompt" [default]
  local var="$1" prompt="$2" def="${3:-}" cur="${!1:-}"
  if [ -z "$cur" ]; then
    read -r -p "  ${prompt}${def:+ [$def]}: " cur
    cur="${cur:-$def}"
  fi
  printf -v "$var" '%s' "$cur"
}

echo "── Arcade onboarding ──────────────────────────────────────"
ask ARCADE_API_KEY "Arcade API key (arc_proj...)"
ask ARCADE_USER_ID "Your email (this is who your agent acts as)"

# Org and project ids: from the CLI credentials file if you've ever run
# `arcade login`, otherwise from you (both are in the dashboard URL).
CREDS="$HOME/.arcade/credentials.yaml"
if [ -f "$CREDS" ]; then
  ORG_ID="${ORG_ID:-$(grep 'org_id:' "$CREDS" | head -1 | awk '{print $2}')}"
  PROJECT_ID="${PROJECT_ID:-$(grep 'project_id:' "$CREDS" | head -1 | awk '{print $2}')}"
fi
ask ORG_ID "Org ID"
ask PROJECT_ID "Project ID"
ask GATEWAY_SLUG "Gateway slug (yours alone; letters/dashes)" "workshop-$(whoami)"

BASE="${ENGINE_URL}/v1/orgs/${ORG_ID}/projects/${PROJECT_ID}"
auth=(-H "Authorization: Bearer ${ARCADE_API_KEY}" -H "Content-Type: application/json")

# Read-only on purpose: enough for the first win, nothing that writes.
TOOLS='["Github.WhoAmI","Github.GetRepository"]'

echo
echo "[1/3] Creating gateway '${GATEWAY_SLUG}'..."
existing=$(curl -sf "${BASE}/gateways" "${auth[@]}" \
  | jq -r ".items[]? | select(.slug==\"${GATEWAY_SLUG}\") | .id" || true)

if [ -n "$existing" ]; then
  curl -sf -X PATCH "${BASE}/gateways/${existing}" "${auth[@]}" \
    -d "{\"tool_filter\": {\"allowed_tools\": ${TOOLS}}}" > /dev/null
  echo "      already existed — tools updated (${existing})"
else
  resp=$(curl -s -X POST "${BASE}/gateways" "${auth[@]}" -d "{
    \"name\": \"${GATEWAY_SLUG}\",
    \"slug\": \"${GATEWAY_SLUG}\",
    \"description\": \"workshop onboarding\",
    \"auth_type\": \"arcade_header\",
    \"tool_filter\": {\"allowed_tools\": ${TOOLS}}
  }")
  gw_id=$(echo "$resp" | jq -r '.id // empty')
  if [ -z "$gw_id" ]; then
    echo "      FAILED: $(echo "$resp" | head -c 300)"
    echo "      (a key/ID typo shows up here — check all three values)"
    exit 1
  fi
  echo "      created (${gw_id})"
fi

echo "[2/3] Verifying the MCP handshake..."
# MCP over HTTP is stateful: initialize first, then reuse the session id it
# hands back. A bare tools/list gets "missing Mcp-Session-Id header".
mcp() {
  curl -s "$@" -X POST "${ENGINE_URL}/mcp/${GATEWAY_SLUG}" \
    -H "Authorization: Bearer ${ARCADE_API_KEY}" \
    -H "Arcade-User-Id: ${ARCADE_USER_ID}" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream"
}
hdrs=$(mktemp)
mcp -D "$hdrs" -o /dev/null -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"onboard","version":"1.0"}}}'
session=$(grep -i '^mcp-session-id:' "$hdrs" | tr -d '\r' | awk '{print $2}')
rm -f "$hdrs"
tools_seen=$(mcp -H "Mcp-Session-Id: ${session}" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
  | { grep -o '"name":"[^"]*"' || true; } | wc -l | tr -d ' ')
if [ "${tools_seen}" -gt 0 ]; then
  echo "      gateway is serving ${tools_seen} tools"
else
  echo "      WARNING: handshake returned no tools — the gateway may need a"
  echo "      few seconds; re-run this script to re-check."
fi

echo "[3/3] Writing .mcp.json..."
cat > .mcp.json <<EOF
{
  "mcpServers": {
    "arcade": {
      "type": "http",
      "url": "${ENGINE_URL}/mcp/${GATEWAY_SLUG}",
      "headers": {
        "Authorization": "Bearer ${ARCADE_API_KEY}",
        "Arcade-User-Id": "${ARCADE_USER_ID}"
      }
    }
  }
}
EOF

echo
echo "── done ───────────────────────────────────────────────────"
echo "  .mcp.json written. Open your MCP client here (Claude Code:"
echo "  just start it in this directory) and ask:"
echo
echo "    \"What Arcade tools can you reach? Use one to tell me how many"
echo "     open issues arcadeai-labs/daytona-background-agents has.\""
echo
echo "  The first GitHub call returns an OAuth link — approve it once."
