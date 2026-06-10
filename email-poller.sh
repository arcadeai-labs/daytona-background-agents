#!/usr/bin/env bash
set -euo pipefail

ENGINE_URL="${ENGINE_URL:-https://api.arcade.dev}"
API_KEY="${ARCADE_API_KEY:?Set ARCADE_API_KEY}"
USER_ID="${ARCADE_USER_ID:?Set ARCADE_USER_ID}"
POLL_INTERVAL="${POLL_INTERVAL:-15}"
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
      \"input\": {\"query\": \"subject:(buggy api) is:unread\", \"max_results\": 1}
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
You are a triage agent. A support email just arrived that needs investigation and resolution.

Subject: ${subject}

Body:
${body}

Your task:
1. Use Linear.ListTeams to find the team named 'DEMO', then create a Linear ticket in that team (priority: High, labels_to_add: ['Bug', 'auto-triage']). IMPORTANT: Use the exact label name Bug — NOT type: bug which belongs to a different team and will error.
2. Create a Daytona sandbox to investigate and fix the bug
3. Clone the repo https://github.com/arcade-demos/buggy-api in the sandbox
4. Navigate to the buggy-api/ directory
5. Use Github.WhoAmI to get the current user's name and email, then configure git identity in the cloned repo: git config user.email and git config user.name
6. Run the tests to identify the failing test
7. Read the source code, find the bug, fix it
8. Run tests again to confirm the fix
9. Create a feature branch named fix/buggy-api-<YYYYMMDD-HHmmss> using the current timestamp, commit, push, and open a PR
10. Delete the sandbox
11. Update the Linear ticket to 'In Review' with the PR link
12. Send a Slack message to #demo-engineering summarizing what you did
13. Create a Google Doc with a full triage report

Work through each step. If a tool call is denied with HITL_CHECKPOINT, this is a human-in-the-loop governance checkpoint — NOT an error. Explain to the user what you were trying to do, why it was blocked, and that you are waiting for human approval. Then retry the same tool call after a short pause. A background watcher will auto-approve it.
PROMPT
)"

    echo "$thread_id" >> "$PROCESSED_FILE"
    log "Claude Code session complete."
  fi

  sleep "$POLL_INTERVAL"
done
