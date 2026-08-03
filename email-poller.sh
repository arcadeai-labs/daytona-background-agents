#!/usr/bin/env bash
set -euo pipefail

ENGINE_URL="${ENGINE_URL:-https://api.arcade.dev}"
API_KEY="${ARCADE_API_KEY:?Set ARCADE_API_KEY}"
USER_ID="${ARCADE_USER_ID:?Set ARCADE_USER_ID}"
POLL_INTERVAL="${POLL_INTERVAL:-15}"
DEMO_REPO_URL="${DEMO_REPO_URL:-https://github.com/arcadeai-labs/daytona-background-agents}"
PROCESSED_FILE="/tmp/arcade-demo-processed.txt"

touch "$PROCESSED_FILE"
log() { echo "[$(date +%H:%M:%S)] $*"; }

log "Watching for support emails (polling every ${POLL_INTERVAL}s)..."

while true; do
  result=$(curl -sf -X POST "${ENGINE_URL}/v1/tools/execute" \
    -H "Authorization: Bearer ${API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{
      \"tool_name\": \"Gmail.search_threads\",
      \"user_id\": \"${USER_ID}\",
      \"input\": {\"subject\": [\"buggy api\"], \"label_ids\": [\"UNREAD\"], \"max_results\": 1}
    }" 2>/dev/null || echo '{}')

  thread_id=$(echo "$result" | jq -r '.output.value.threads[0].id // empty')

  if [ -n "$thread_id" ] && ! grep -qF "$thread_id" "$PROCESSED_FILE"; then
    detail=$(curl -sf -X POST "${ENGINE_URL}/v1/tools/execute" \
      -H "Authorization: Bearer ${API_KEY}" \
      -H "Content-Type: application/json" \
      -d "{
        \"tool_name\": \"Gmail.get_thread\",
        \"user_id\": \"${USER_ID}\",
        \"input\": {\"thread_id\": \"${thread_id}\"}
      }" 2>/dev/null || echo '{}')

    subject=$(echo "$detail" | jq -r '.output.value.subject // "No subject"')
    body=$(echo "$detail" | jq -r '.output.value.messages[0].body // ""' | head -30)

    log "NEW SUPPORT EMAIL: ${subject}"
    log "Launching Claude Code to triage..."

    claude --print "$(cat <<PROMPT
Use the support-triage skill. A support email just arrived that needs investigation and resolution.

Subject: ${subject}

Body:
${body}

The buggy repo is ${DEMO_REPO_URL}. The skill does the rest.
PROMPT
)"

    echo "$thread_id" >> "$PROCESSED_FILE"
    log "Claude Code session complete."
  fi

  sleep "$POLL_INTERVAL"
done
