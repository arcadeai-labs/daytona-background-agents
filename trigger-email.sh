#!/usr/bin/env bash
# Send the trigger email that wakes the background agent.
#
# The demo's trigger is deliberately external - Arcade governs what the agent
# may do, not when it wakes up. But "email yourself from your phone" is a bad
# first step for someone trying the demo alone, so this does it for you.
#
#   ./trigger-email.sh                 # send to yourself (ARCADE_USER_ID)
#   ./trigger-email.sh you@other.com   # send somewhere else
#
# Goes through Arcade as the delegated user, like every other call in this demo,
# so it lands in the audit trail too.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
set -a; source "${SCRIPT_DIR}/.env"; set +a

ENGINE_URL="${ENGINE_URL:-https://api.arcade.dev}"
: "${ARCADE_API_KEY:?ARCADE_API_KEY missing in .env}"
: "${ARCADE_USER_ID:?ARCADE_USER_ID missing in .env}"
TO="${1:-$ARCADE_USER_ID}"

# The poller matches on subject:(buggy api) - see run.sh. Keep those two words.
SUBJECT="buggy api pagination is broken"
BODY="Hi - page 1 of /items is returning items 11 through 20 instead of 1 through 10, and item 1 through 10 never appear at all. Page 2 repeats what page 1 showed. Looks like an off-by-one in the pagination offset. Customers are seeing it. Can someone take a look?

Thanks,
A customer"

echo "[trigger] Sending '${SUBJECT}' to ${TO} as ${ARCADE_USER_ID}..."

response=$(curl -s -X POST "${ENGINE_URL}/v1/tools/execute" \
  -H "Authorization: Bearer ${ARCADE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d "$(python3 - "$TO" "$SUBJECT" "$BODY" "$ARCADE_USER_ID" <<'PY'
import json, sys
to, subject, body, user = sys.argv[1:5]
print(json.dumps({
    "tool_name": "Gmail.SendEmail",
    "input": {"recipient": to, "subject": subject, "body": body},
    "user_id": user,
}))
PY
)")

# An unauthorized send comes back with a URL to click, not an error - surface it.
auth_url=$(printf '%s' "$response" | python3 -c "
import json,sys
try: d=json.load(sys.stdin)
except Exception: sys.exit()
u=(d.get('authorization') or {}).get('url') or d.get('url','')
print(u)
" 2>/dev/null || true)

if [ -n "$auth_url" ]; then
  echo "[trigger] Gmail send needs one more OAuth scope. Authorize here, then re-run:"
  echo "          ${auth_url}"
  exit 1
fi

if printf '%s' "$response" | grep -q '"success":[[:space:]]*true\|"status":[[:space:]]*"success"'; then
  echo "[trigger] Sent. The poller should pick it up within ${POLL_INTERVAL:-15}s."
else
  echo "[trigger] Unexpected response:"
  printf '%s\n' "$response" | head -20
  exit 1
fi
