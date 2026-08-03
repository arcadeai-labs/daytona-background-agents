#!/usr/bin/env bash
# Fire the triage agent by hand — no Gmail, no poller, no run.sh.
#
#   ./triage.sh                          # uses the canned pagination bug report
#   ./triage.sh "pages repeat items"     # or bring your own bug report
#
# This is the same 3-line launch run.sh uses when an email lands; the trigger
# layer is yours to replace, and this replaces it with your keyboard. The skill
# degrades gracefully: GitHub is the only connection it actually requires
# (see "Degraded mode" in .claude/skills/support-triage/SKILL.md).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
[ -f .env ] && { set -a; source .env; set +a; }

LINEAR_TEAM="${LINEAR_TEAM:-DEMO}"
SLACK_CHANNEL="${SLACK_CHANNEL:-demo-engineering}"
DEMO_REPO_URL="${DEMO_REPO_URL:-https://github.com/arcadeai-labs/daytona-background-agents}"

BODY="${1:-Page 1 of /items returns items 11-20 instead of 1-10, and items 1-10 never appear at all. Looks like an off-by-one in the pagination offset. Customers are seeing it.}"

command -v claude >/dev/null || { echo "claude not on PATH — install Claude Code first"; exit 1; }
[ -f .mcp.json ] || echo "note: no .mcp.json here — run ./onboard.sh or 'arcade connect' first so the agent has tools"

prompt="Use the support-triage skill. A support email just arrived that needs investigation and resolution.

Subject: buggy api pagination is broken

Body:
${BODY}

The buggy repo is ${DEMO_REPO_URL}. The Linear team is '${LINEAR_TEAM}'. The Slack channel is #${SLACK_CHANNEL}. The skill does the rest."

# Same invocation as run.sh:564 — interactive, prompt preloaded. Permissions
# come from .claude/settings.json (Arcade MCP tools pre-allowed, repo-scoped),
# so no permission-skipping flags are needed or wanted.
echo "[triage] launching claude — GitHub is the only required connection; other steps degrade"
exec claude "$prompt"
