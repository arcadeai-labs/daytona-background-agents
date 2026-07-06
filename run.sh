#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── Load .env ─────────────────────────────────────────────────────
if [ ! -f "${SCRIPT_DIR}/.env" ]; then
  echo "ERROR: No .env file. Copy .env.example to .env and fill in values." >&2
  exit 1
fi
set -a; source "${SCRIPT_DIR}/.env"; set +a

ENGINE_URL="${ENGINE_URL:-https://api.arcade.dev}"
API_KEY="${ARCADE_API_KEY:?ARCADE_API_KEY missing in .env}"
USER_ID="${ARCADE_USER_ID:?ARCADE_USER_ID missing in .env}"
NGROK_AUTHTOKEN="${NGROK_AUTHTOKEN:?NGROK_AUTHTOKEN missing in .env}"
GATEWAY_SLUG="${GATEWAY_SLUG:-demo-gateway}"
CATE_PORT="${CATE_PORT:-8888}"
POLL_INTERVAL="${POLL_INTERVAL:-15}"
WATCH_SENDER="${WATCH_SENDER:-}"
DEMO_REPO_URL="${DEMO_REPO_URL:-https://github.com/arcadeai-labs/daytona-background-agents}"
DEMO_QUIET="${DEMO_QUIET:-}"

# Arcade CLI access token + org/project context
ARCADE_CREDS="${HOME}/.arcade/credentials.yaml"
if [ ! -f "$ARCADE_CREDS" ]; then
  echo "ERROR: Not logged into Arcade CLI. Run: arcade login" >&2
  exit 1
fi
ACCESS_TOKEN=$(grep 'access_token:' "$ARCADE_CREDS" | head -1 | awk '{print $2}')
[ -z "$ACCESS_TOKEN" ] && { echo "ERROR: No access token. Run: arcade login" >&2; exit 1; }

# Pull org_id and project_id from CLI config (override with .env if set)
ORG_ID="${ORG_ID:-$(grep 'org_id:' "$ARCADE_CREDS" | head -1 | awk '{print $2}')}"
PROJECT_ID="${PROJECT_ID:-$(grep 'project_id:' "$ARCADE_CREDS" | head -1 | awk '{print $2}')}"
[ -z "$ORG_ID" ] && { echo "ERROR: No org_id found. Run: arcade login" >&2; exit 1; }
[ -z "$PROJECT_ID" ] && { echo "ERROR: No project_id found. Run: arcade project set <id>" >&2; exit 1; }

# Check if token is expired
token_exp=$(echo "$ACCESS_TOKEN" | cut -d. -f2 | python3 -c "import sys,base64,json; d=sys.stdin.read().strip(); d+='='*(4-len(d)%4); print(json.loads(base64.urlsafe_b64decode(d))['exp'])" 2>/dev/null || echo "0")
now_epoch=$(date +%s)
if [ "$now_epoch" -ge "$token_exp" ]; then
  echo "ERROR: Arcade access token expired. Run: arcade logout && arcade login && arcade project set ${PROJECT_ID}" >&2
  exit 1
fi

# Base path for all org-scoped API calls
BASE="/v1/orgs/${ORG_ID}/projects/${PROJECT_ID}"

# Exact tools needed for the demo flow
DEMO_TOOLS=(
  # Gmail — search and read emails
  Gmail.SearchThreads
  Gmail.GetThread
  # Linear — create/update/list tickets
  Linear.CreateIssue
  Linear.UpdateIssue
  Linear.TransitionIssueState
  Linear.ListIssues
  Linear.ListTeams
  Linear.ListLabels
  Linear.ListWorkflowStates
  # Daytona — sandbox lifecycle, code, git
  Daytona.CreateSandbox
  Daytona.DeleteSandbox
  Daytona.GetSandbox
  Daytona.ListSandboxes
  Daytona.GitClone
  Daytona.GitStatus
  Daytona.GitAdd
  Daytona.GitCommit
  Daytona.GitPush
  Daytona.GitCreateBranch
  Daytona.GitBranches
  Daytona.RunCommand
  Daytona.RunCode
  Daytona.ReadFile
  Daytona.WriteFile
  Daytona.ReplaceInFiles
  Daytona.ListFiles
  Daytona.FindFiles
  Daytona.SearchContent
  # GitHub — identity + create PR
  Github.WhoAmI
  Github.CreatePullRequest
  Github.CreateBranch
  Github.GetRepository
  # Slack — send summary
  Slack.SendMessage
  Slack.ListConversations
)

log() { echo "[demo] $*"; }
fail() { echo "[demo] ERROR: $*" >&2; exit 1; }

# Org-scoped API call with user access token
api() {
  local method="$1" path="$2"
  shift 2
  curl -s -X "$method" "${ENGINE_URL}${BASE}${path}" \
    -H "Authorization: Bearer ${ACCESS_TOKEN}" \
    -H "Content-Type: application/json" \
    "$@"
}

CREATED_PLUGIN_ID=""
CREATED_HOOK_ID=""
HITL_WATCHER_PID=""
HITL_APPROVE_DELAY="${HITL_APPROVE_DELAY:-30}"

# ── HITL watcher ─────────────────────────────────────────────
# Polls CATE logs for sandbox creation blocks, waits a few seconds
# (so Alex can talk about governance), then auto-approves.
hitl_watcher() {
  local cate_url="http://localhost:${CATE_PORT}"
  local seen_file="/tmp/arcade-demo-hitl-seen.txt"
  > "$seen_file"

  while true; do
    # Check CATE logs for blocked create_sandbox requests
    logs=$(curl -sf "${cate_url}/_logs" 2>/dev/null || echo '{}')
    blocked=$(echo "$logs" | jq -r '
      .logs[]? |
      select(.endpoint == "/pre") |
      select(.response.code == "CHECK_FAILED") |
      select(.body.tool.name // "" | test("CreateSandbox")) |
      .body.execution_id // .timestamp
    ' 2>/dev/null || true)

    if [ -n "$blocked" ]; then
      # Check if we already approved this one
      while IFS= read -r block_id; do
        if [ -n "$block_id" ] && ! grep -qF "$block_id" "$seen_file"; then
          echo ""
          log "╔══════════════════════════════════════════════════════════╗"
          log "║  HITL CHECKPOINT: Sandbox creation was BLOCKED by CATE  ║"
          log "║  Auto-approving in ${HITL_APPROVE_DELAY} seconds...                        ║"
          log "╚══════════════════════════════════════════════════════════╝"
          echo ""

          sleep "$HITL_APPROVE_DELAY"

          # Approve: change create_sandbox rule from block to proceed
          curl -sf -X PUT "${cate_url}/_config" \
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
                        "labels": "[\"ai-generated\", \"auto-triage\"]"
                      }
                    }
                  }
                ]
              }
            }' > /dev/null 2>&1

          log "HITL APPROVED — sandbox creation unblocked"
          echo "$block_id" >> "$seen_file"

          # Wait for sandbox to be created, then restore the block
          sleep 15
          curl -sf -X PUT "${cate_url}/_config" \
            -H "Content-Type: application/json" \
            -d '{
              "pre": {
                "default_action": "proceed",
                "rules": [
                  {
                    "toolkit": "Daytona",
                    "tool": "CreateSandbox",
                    "action": "block",
                    "error_message": "HITL_CHECKPOINT: Sandbox creation requires human approval. Config: {{inputs}}"
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
                        "labels": "[\"ai-generated\", \"auto-triage\"]"
                      }
                    }
                  }
                ]
              }
            }' > /dev/null 2>&1
          log "HITL block restored for next run"

          # Clear logs so watcher doesn't re-trigger on old CHECK_FAILED entries
          curl -sf -X DELETE "${cate_url}/_logs" >/dev/null 2>&1 || true
        fi
      done <<< "$blocked"
    fi

    sleep 2
  done
}

cleanup() {
  log "Shutting down..."

  # Delete hook and plugin from Arcade (so stale webhooks don't block tools)
  if [ -n "$CREATED_HOOK_ID" ]; then
    log "Deleting hook ${CREATED_HOOK_ID}..."
    api DELETE "/hooks/${CREATED_HOOK_ID}" > /dev/null 2>&1 || true
  fi
  if [ -n "$CREATED_PLUGIN_ID" ]; then
    log "Deleting plugin ${CREATED_PLUGIN_ID}..."
    api DELETE "/plugins/${CREATED_PLUGIN_ID}" > /dev/null 2>&1 || true
  fi

  # Clear CATE logs before stopping so next run starts clean
  "${SCRIPT_DIR}/audit-check.sh" clear >/dev/null 2>&1 || true

  # Stop processes
  [ -n "${HITL_WATCHER_PID:-}" ] && kill "$HITL_WATCHER_PID" 2>/dev/null || true
  [ -n "${CATE_PID:-}" ] && kill "$CATE_PID" 2>/dev/null || true
  [ -n "${NGROK_PID:-}" ] && kill "$NGROK_PID" 2>/dev/null || true
  pkill -f "ngrok http" 2>/dev/null || true

  log "Done."
  exit 0
}
trap cleanup EXIT INT TERM

# ══════════════════════════════════════════════════════════════════
# PHASE 1: SETUP
# ══════════════════════════════════════════════════════════════════

# ── Generate .mcp.json ────────────────────────────────────────────
log "Generating .mcp.json..."
cat > "${SCRIPT_DIR}/.mcp.json" <<MCPJSON
{
  "mcpServers": {
    "arcade": {
      "type": "http",
      "url": "${ENGINE_URL}/mcp/${GATEWAY_SLUG}",
      "headers": {
        "Authorization": "Bearer ${API_KEY}",
        "Arcade-User-Id": "${USER_ID}"
      }
    }
  }
}
MCPJSON

# ── Engine health ─────────────────────────────────────────────────
log "Checking engine..."
health=$(curl -s "${ENGINE_URL}/v1/health" || true)
if [ -z "$health" ] || [ "$(echo "$health" | jq -r '.healthy')" != "true" ]; then
  fail "Engine not healthy: ${health:-no response}"
fi
log "Engine OK"

# ── CATE server ───────────────────────────────────────────────────
BINARY="${SCRIPT_DIR}/cate-server/cate-server"
CONFIG="${SCRIPT_DIR}/cate-config.yaml"
[ ! -f "$CONFIG" ] && fail "cate-config.yaml not found"

if [ ! -x "$BINARY" ]; then
  log "Building CATE server..."
  (cd "${SCRIPT_DIR}/cate-server" && go build -o cate-server .)
fi

# Kill any existing CATE so we start fresh with clean config
if curl -sf http://localhost:${CATE_PORT}/health >/dev/null 2>&1; then
  log "Restarting CATE to ensure clean config..."
  pkill -f "cate-server.*-port ${CATE_PORT}" 2>/dev/null || true
  sleep 1
fi
"$BINARY" -config "$CONFIG" -port "$CATE_PORT" >/dev/null 2>&1 &
CATE_PID=$!
sleep 1
curl -sf http://localhost:${CATE_PORT}/health >/dev/null 2>&1 || fail "CATE failed to start"
log "CATE running on :${CATE_PORT}"

# Clear stale logs so HITL watcher doesn't trigger on previous run's blocks
"${SCRIPT_DIR}/audit-check.sh" clear >/dev/null 2>&1 || true

# ── ngrok tunnel ──────────────────────────────────────────────────
if curl -sf http://localhost:4040/api/tunnels >/dev/null 2>&1; then
  NGROK_URL=$(curl -sf http://localhost:4040/api/tunnels | jq -r '.tunnels[] | select(.proto=="https") | .public_url')
  log "ngrok already running: ${NGROK_URL}"
else
  log "Starting ngrok tunnel..."
  ngrok http ${CATE_PORT} --authtoken "${NGROK_AUTHTOKEN}" --log=stderr --log-level=warn >/dev/null 2>&1 &
  NGROK_PID=$!
  sleep 2
  NGROK_URL=$(curl -sf http://localhost:4040/api/tunnels | jq -r '.tunnels[] | select(.proto=="https") | .public_url' 2>/dev/null || true)
  [ -z "$NGROK_URL" ] && fail "Could not get ngrok URL"
  log "Tunnel: ${NGROK_URL}"
fi

CATE_WEBHOOK_URL="$NGROK_URL"

# ── CATE plugin (always create fresh, cleanup deletes on exit) ─────
log "Configuring CATE plugin..."

# Delete ALL leftover demo hooks and plugins from previous runs
log "Cleaning up any stale demo hooks/plugins..."
api GET /hooks 2>/dev/null | jq -r '.items[]? | select(.name | test("demo")) | .id' 2>/dev/null | while read -r hid; do
  [ -n "$hid" ] && api DELETE "/hooks/${hid}" > /dev/null 2>&1 && log "Deleted stale hook ${hid}" || true
done
api GET /plugins 2>/dev/null | jq -r '.items[]? | select(.name=="demo-cate-hooks") | .id' 2>/dev/null | while read -r pid; do
  [ -n "$pid" ] && api DELETE "/plugins/${pid}" > /dev/null 2>&1 && log "Deleted stale plugin ${pid}" || true
done

plugin_response=$(api POST /plugins -d "{
  \"name\": \"demo-cate-hooks\",
  \"plugin_type\": \"webhook\",
  \"description\": \"Daytona demo - pre-execution sandbox check\",
  \"status\": \"active\",
  \"webhook_config\": {
    \"endpoints\": {
      \"pre\": {
        \"url\": \"${CATE_WEBHOOK_URL}/pre\",
        \"failure_mode\": \"fail_open\",
        \"phase\": \"before\",
        \"status\": \"active\",
        \"priority\": 5
      }
    },
    \"auth\": {\"type\": \"bearer\", \"token\": \"demo-secret\"},
    \"health_check_path\": \"${CATE_WEBHOOK_URL}/health\"
  }
}")
CREATED_PLUGIN_ID=$(echo "$plugin_response" | jq -r '.id // empty')
[ -z "$CREATED_PLUGIN_ID" ] && fail "Plugin creation failed: ${plugin_response}"
log "Plugin: ${CREATED_PLUGIN_ID}"

# Hook is auto-created by the plugin endpoints config
CREATED_HOOK_ID=$(echo "$plugin_response" | jq -r '.hooks[0].id // empty')
[ -n "$CREATED_HOOK_ID" ] && log "Hook: ${CREATED_HOOK_ID}" || log "WARNING: No hook auto-created"

# ── Gateway with tools ────────────────────────────────────────────
log "Configuring gateway..."

TOOLS_JSON=$(printf '%s\n' "${DEMO_TOOLS[@]}" | jq -R . | jq -s .)
tool_count=$(echo "$TOOLS_JSON" | jq 'length')
log "Configuring ${tool_count} tools"

existing_gw=$(api GET /gateways | jq -r ".items[] | select(.slug==\"${GATEWAY_SLUG}\") | .id" 2>/dev/null || true)

if [ -n "$existing_gw" ]; then
  api PATCH "/gateways/${existing_gw}" -d "{\"tool_filter\": {\"allowed_tools\": $TOOLS_JSON}}" > /dev/null
  log "Gateway updated: ${existing_gw} (${tool_count} tools)"
else
  gw_response=$(api POST /gateways -d "{
    \"name\": \"${GATEWAY_SLUG}\",
    \"description\": \"Daytona demo\",
    \"slug\": \"${GATEWAY_SLUG}\",
    \"auth_type\": \"arcade_header\",
    \"tool_filter\": {\"allowed_tools\": $TOOLS_JSON}
  }")
  gw_id=$(echo "$gw_response" | jq -r '.id // empty')
  [ -n "$gw_id" ] && log "Gateway created: ${gw_id}" || fail "Gateway failed: ${gw_response}"
fi

# ── Verify MCP tools ─────────────────────────────────────────────
log "Verifying MCP..."
mcp_init=$(curl -s -D /tmp/mcp-headers.txt -X POST "${ENGINE_URL}/mcp/${GATEWAY_SLUG}" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Arcade-User-Id: ${USER_ID}" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}')

if echo "$mcp_init" | grep -q "serverInfo"; then
  SESSION_ID=$(grep -i 'mcp-session-id' /tmp/mcp-headers.txt | awk '{print $2}' | tr -d '\r')
  mcp_tools=$(curl -s -X POST "${ENGINE_URL}/mcp/${GATEWAY_SLUG}" \
    -H "Authorization: Bearer ${API_KEY}" \
    -H "Arcade-User-Id: ${USER_ID}" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    -H "Mcp-Session-Id: ${SESSION_ID}" \
    -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}')
  mcp_count=$(echo "$mcp_tools" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('result',{}).get('tools',[])))" 2>/dev/null || echo "0")
  log "MCP OK: ${mcp_count} tools served"
else
  log "WARNING: MCP init failed: ${mcp_init}"
fi

# ── Google auth ───────────────────────────────────────────────────
log "Checking Google auth..."
auth_response=$(api POST /auth/authorize -d "{
  \"user_id\": \"${USER_ID}\",
  \"auth_requirement\": {
    \"provider_id\": \"google\",
    \"provider_type\": \"oauth2\",
    \"oauth2\": {
      \"scopes\": [\"https://www.googleapis.com/auth/gmail.modify\"]
    }
  }
}")

auth_status=$(echo "$auth_response" | jq -r '.status // empty')
auth_url=$(echo "$auth_response" | jq -r '.url // empty')

if [ "$auth_status" = "completed" ]; then
  log "Google auth: already authorized"
elif [ -n "$auth_url" ]; then
  log "Google auth required — opening browser..."
  open "$auth_url" 2>/dev/null || echo "  Visit: ${auth_url}"

  auth_id=$(echo "$auth_response" | jq -r '.id')
  log "Waiting for you to authorize (30s timeout)..."
  authorized=false
  for _ in $(seq 1 15); do
    sleep 2
    check=$(api GET "/auth/status?id=${auth_id}&wait=0")
    if [ "$(echo "$check" | jq -r '.status')" = "completed" ]; then
      log "Google auth: authorized"
      authorized=true
      break
    fi
  done
  if [ "$authorized" = "false" ]; then
    log "Google auth not completed yet — continuing anyway (auth will be prompted on first Gmail tool use)"
  fi
else
  log "WARNING: Auth response: ${auth_response}"
fi

# ══════════════════════════════════════════════════════════════════
# PHASE 2: WATCH FOR EMAILS
# ══════════════════════════════════════════════════════════════════

PROCESSED_FILE="/tmp/arcade-demo-processed.txt"
touch "$PROCESSED_FILE"

if [ -n "$WATCH_SENDER" ]; then
  gmail_input="{\"subject\": \"buggy api\", \"sender\": \"${WATCH_SENDER}\", \"label_ids\": [\"UNREAD\"]}"
  gmail_query="subject:(buggy api) from:${WATCH_SENDER} label:UNREAD"
else
  gmail_input="{\"subject\": \"buggy api\", \"label_ids\": [\"UNREAD\"]}"
  gmail_query="subject:(buggy api) label:UNREAD"
fi

# Seed processed file with existing unread emails so we only trigger on NEW ones
log "Scanning existing unread emails..."
existing=$(api POST /tools/execute -d "{
  \"tool_name\": \"Gmail.SearchThreads\",
  \"user_id\": \"${USER_ID}\",
  \"input\": $(echo "$gmail_input" | jq -c ". + {max_results: 50}")
}" 2>/dev/null || echo '{}')
existing_ids=$(echo "$existing" | jq -r '.output.value.threads[]?.id // empty' 2>/dev/null || true)
if [ -n "$existing_ids" ]; then
  existing_count=0
  while IFS= read -r eid; do
    if [ -n "$eid" ] && ! grep -qF "$eid" "$PROCESSED_FILE"; then
      echo "$eid" >> "$PROCESSED_FILE"
      existing_count=$((existing_count + 1))
    fi
  done <<< "$existing_ids"
  log "Marked ${existing_count} existing emails as seen"
fi

cat <<READY

============================================================
  DEMO RUNNING
============================================================
  Engine:   ${ENGINE_URL}
  MCP:      ${ENGINE_URL}/mcp/${GATEWAY_SLUG}
  CATE:     ${NGROK_URL} (pre-execution sandbox check)
  Tools:    ${tool_count} (Gmail, Linear, Daytona, Github, Slack)
  Watching: ${gmail_query}
  Polling:  every ${POLL_INTERVAL}s
============================================================

  Waiting for an email...

  HITL auto-approve: ${HITL_APPROVE_DELAY}s delay after block
  Audit logs:        ./audit-check.sh

============================================================
READY

# Start HITL watcher in background
hitl_watcher &
HITL_WATCHER_PID=$!

while true; do
  result=$(api POST /tools/execute -d "{
    \"tool_name\": \"Gmail.SearchThreads\",
    \"user_id\": \"${USER_ID}\",
    \"input\": $(echo "$gmail_input" | jq -c ". + {max_results: 1}")
  }" 2>/dev/null || echo '{}')

  thread_id=$(echo "$result" | jq -r '.output.value.threads[0].id // empty' 2>/dev/null || true)

  if [ -n "$thread_id" ] && ! grep -qF "$thread_id" "$PROCESSED_FILE"; then
    detail=$(api POST /tools/execute -d "{
      \"tool_name\": \"Gmail.GetThread\",
      \"user_id\": \"${USER_ID}\",
      \"input\": {\"thread_id\": \"${thread_id}\"}
    }" 2>/dev/null || echo '{}')

    subject=$(echo "$detail" | jq -r '.output.value.messages[0].subject // "No subject"')
    body=$(echo "$detail" | jq -r '.output.value.messages[0].body // ""' | head -30)

    log "Email received: ${subject}"
    log "Launching Claude Code..."
    QUIET_NOTE=""
    if [ -n "$DEMO_QUIET" ]; then
      QUIET_NOTE="

REHEARSAL MODE: skip the Linear ticket, Slack message, and Google Doc steps entirely. Do not create anything in Linear, Slack, or Google Docs. Only the Daytona sandbox and the GitHub branch/PR."
    fi

    prompt="Use the support-triage skill. A support email just arrived that needs investigation and resolution.

Subject: ${subject}

Body:
${body}

The buggy repo is ${DEMO_REPO_URL}. The skill does the rest.${QUIET_NOTE}"

    unset CLAUDECODE
    claude "$prompt"

    echo "$thread_id" >> "$PROCESSED_FILE"
    log "Done. Waiting for next email..."
  fi

  sleep "$POLL_INTERVAL"
done
