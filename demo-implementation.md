# Arcade + Daytona Demo: Implementation Plan

## Overview

Live demo: Arcade's governed AI agent platform + Daytona sandboxed code execution. Five acts flowing from interactive Claude Code to fully autonomous agent and back.

The core moment: an email poller detects a support email and **literally launches Claude Code on Alex's machine** with a triage prompt. Claude Code already has the Arcade MCP gateway configured with all tools. The audience watches Claude Code work autonomously — creating tickets, spinning up sandboxes, fixing bugs, opening PRs — all governed by CATE hooks.

---

## Architecture

```
Alex's Machine                    Arcade Cloud                     Daytona Cloud
+---------------------+          +-------------------+            +-----------------+
| Claude Code         | MCP/HTTP | Engine            |  API       | Sandbox         |
|  (Arcade GW config) |--------->| - Token Vault     |----------->| - git clone     |
|                     |          | - CATE Hooks      |            | - code edit     |
| email-poller.sh     |          | - MCP Gateway     |            | - test & PR     |
|  detects email -->  |          | - Audit Trail     |            +-----------------+
|  launches `claude`  |          +---| Webhook |-----+
+---------------------+              |    |    |
                                +----+    |    +----+
                                |         |         |
                              Linear    Slack    GitHub
                              GDocs     Gmail    Daytona
```

### MCP Gateway Config

```json
// daytona-demo/.mcp.json
{
  "mcpServers": {
    "arcade": {
      "type": "http",
      "url": "https://<engine-host>/mcp/v1/gateways/<gateway-id>",
      "headers": {
        "Authorization": "Bearer ${ARCADE_API_KEY}",
        "Arcade-User-Id": "shub@arcade.dev"
      }
    }
  }
}
```

Claude Code picks this up automatically when launched from the `daytona-demo/` directory. Single MCP connection exposes all 38+ tools across 6 services.

---

## The Email Poller

Simple bash script. Polls Gmail via Arcade REST API. When it finds a support email, it **launches `claude` on Alex's machine** with a triage prompt.

```bash
#!/usr/bin/env bash
# daytona-demo/email-poller.sh
set -euo pipefail

ENGINE_URL="${ENGINE_URL:-https://engine.arcade.dev}"
API_KEY="${ARCADE_API_KEY:?Set ARCADE_API_KEY}"
POLL_INTERVAL="${POLL_INTERVAL:-15}"
PROCESSED_FILE="/tmp/arcade-demo-processed.txt"

touch "$PROCESSED_FILE"
log() { echo "[$(date +%H:%M:%S)] $*"; }

log "Watching for support emails..."

while true; do
  # Poll Gmail for unread support-triage emails via Arcade REST
  result=$(curl -s -X POST "${ENGINE_URL}/v1/tools/execute" \
    -H "Authorization: Bearer ${API_KEY}" \
    -H "Content-Type: application/json" \
    -d '{
      "tool_name": "Gmail.search_threads",
      "user_id": "shub@arcade.dev",
      "input": {"query": "label:support-triage is:unread", "max_results": 1}
    }')

  thread_id=$(echo "$result" | jq -r '.output.value.threads[0].id // empty')

  if [ -n "$thread_id" ] && ! grep -q "$thread_id" "$PROCESSED_FILE"; then
    # Get email content
    detail=$(curl -s -X POST "${ENGINE_URL}/v1/tools/execute" \
      -H "Authorization: Bearer ${API_KEY}" \
      -H "Content-Type: application/json" \
      -d "{
        \"tool_name\": \"Gmail.get_thread\",
        \"user_id\": \"shub@arcade.dev\",
        \"input\": {\"thread_id\": \"${thread_id}\"}
      }")

    subject=$(echo "$detail" | jq -r '.output.value.subject // "No subject"')
    body=$(echo "$detail" | jq -r '.output.value.messages[0].body // ""' | head -30)

    log "NEW SUPPORT EMAIL: $subject"
    log "Launching Claude Code to triage..."

    # THIS IS THE KEY MOMENT:
    # Launch claude in Alex's terminal with the triage prompt.
    # Claude Code picks up .mcp.json, has all Arcade tools, and works autonomously.
    claude "
You are a triage agent. A support email just arrived that needs investigation and resolution.

Subject: ${subject}

Body:
${body}

Your task:
1. Create a Linear ticket for this bug (team: DEMO, priority: High, labels: Bug, auto-triage)
2. Create a Daytona sandbox to investigate and fix the bug
3. Clone the repo https://github.com/arcade-demos/buggy-api in the sandbox
4. Run the tests to identify the failing test
5. Read the source code, find the bug, fix it
6. Run tests again to confirm the fix
7. Create a feature branch, commit, push, and open a PR
8. Delete the sandbox
9. Update the Linear ticket to 'In Review' with the PR link
10. Send a Slack message to #demo-engineering summarizing what you did
11. Create a Google Doc with a full triage report

Work through each step. If you hit a governance checkpoint, explain what happened and wait for approval.
"

    echo "$thread_id" >> "$PROCESSED_FILE"
    log "Claude Code session complete."
  fi

  sleep "$POLL_INTERVAL"
done
```

That's it. The poller finds the email, extracts the content, and launches `claude` with a prompt. Claude Code does everything else through the MCP gateway.

---

## CATE Webhook Configuration

```yaml
# daytona-demo/cate-config.yaml
health:
  status: healthy

access:
  default_action: allow
  rules: []

pre:
  default_action: proceed
  rules:
    # HITL: Block sandbox creation until human approves
    - toolkit: "Daytona"
      tool: "create_sandbox"
      action: block
      error_message: "HITL_CHECKPOINT: Sandbox creation requires human approval. Config: {{inputs}}"

    # Block direct pushes to main
    - toolkit: "Daytona"
      tool: "git_push"
      input_match: "branch contains main"
      action: block
      error_message: "Direct push to protected branch blocked. Use a feature branch."

    # Auto-label AI-generated PRs
    - toolkit: "GitHub"
      tool: "create_pull_request"
      action: proceed
      override:
        inputs:
          labels: '["ai-generated", "auto-triage"]'

post:
  default_action: proceed
  rules: []
```

### HITL Approval Flow

When Claude Code tries to create a sandbox, CATE blocks it:

```
Claude Code calls Daytona.create_sandbox
  -> Engine fires CATE pre-execution hook
    -> Webhook returns CHECK_FAILED: "HITL_CHECKPOINT: Sandbox creation requires human approval"
  -> Claude Code sees the error, tells Alex what it wants to do
  -> Alex approves (updates webhook config to temporarily allow)
  -> Claude Code retries -> sandbox created
```

Alex approves via:

```bash
# Quick-approve: remove the sandbox block rule
curl -X PUT http://localhost:8888/_config \
  -H "Content-Type: application/json" \
  -d '{"pre": {"default_action": "proceed", "rules": []}}'
```

Or clicks "Approve" in the demo dashboard.

---

## Pre-Demo Setup

### 1. Arcade Engine

Register plugin + hooks:

```bash
# Register webhook plugin
curl -X POST https://<engine>/v1/plugins \
  -H "Authorization: Bearer $ARCADE_API_KEY" \
  -d '{
    "name": "demo-cate-hooks",
    "plugin_type": "webhook",
    "status": "active",
    "webhook_config": {
      "endpoints": {
        "access": {"url": "http://localhost:8888/access", "failure_mode": "fail_open", "phase": "before"},
        "pre": {"url": "http://localhost:8888/pre", "failure_mode": "fail_closed", "phase": "before"},
        "post": {"url": "http://localhost:8888/post", "failure_mode": "fail_open", "phase": "after"}
      },
      "auth": {"type": "bearer", "token": "demo-secret"},
      "health_check_path": "/health"
    }
  }'

# Register hooks
for hp in "tool.access" "tool.pre" "tool.post"; do
  curl -X POST https://<engine>/v1/hooks \
    -H "Authorization: Bearer $ARCADE_API_KEY" \
    -d "{\"name\":\"demo-${hp##*.}\",\"plugin_id\":\"<plugin-id>\",\"hook_point\":\"${hp}\",\"phase\":\"before\",\"failure_mode\":\"fail_closed\",\"status\":\"active\",\"priority\":1}"
done
```

### 2. MCP Gateway

```bash
curl -X POST https://<engine>/v1/gateways \
  -H "Authorization: Bearer $ARCADE_API_KEY" \
  -d '{
    "name": "demo-gateway",
    "description": "Daytona demo - all tools",
    "allowed_tools": ["Linear.*", "Slack.*", "GitHub.*", "GoogleDocs.*", "Gmail.*", "Daytona.*"]
  }'
```

### 3. Demo Bug Repo

Small repo `arcade-demos/buggy-api` with intentional bug:

```
buggy-api/
  src/handler.py        # Bug: offset = page * limit (should be (page-1) * limit)
  src/models.py
  tests/test_handler.py # Failing test exposing the duplicate on page 2
  requirements.txt
```

### 4. Pre-authorize OAuth

Before demo: open Claude Code, run a quick Linear/GitHub/Slack/Gmail/GDocs call to trigger and complete each OAuth flow once. Tokens persist in Arcade's vault.

---

## Act-by-Act Play-by-Play

### Act 1: The Gateway

Alex has Claude Code open in `daytona-demo/` (which has `.mcp.json`).

1. Alex types: **"Check my Linear tickets"**
2. Claude Code discovers `Linear.list_issues` through the MCP gateway
3. First Linear call — Arcade detects missing OAuth, returns auth URL
4. Alex clicks link, authorizes in browser, Arcade stores token
5. Claude Code retries, Linear issues appear
6. Alex: _"Notice — one MCP connection, 38 tools, 6 services. Linear, Slack, GitHub, Google Docs, Gmail, Daytona. One API key. Per-user OAuth."_
7. Alex browses tools: "Show me all available tools" — Claude lists everything from the gateway

### Act 2: The Trigger

Alex transitions to autonomous mode.

1. Alex: _"Now instead of me typing, let's set up an automation. An email poller watches for support emails and launches Claude Code to handle them."_
2. Alex starts the poller in a **second terminal**:
   ```bash
   ARCADE_API_KEY=arc_... ./email-poller.sh
   ```
3. A pre-planted support email sits in the inbox (or Alex sends one live):
   ```
   Subject: Pagination bug in API
   Body: Page 2 duplicates the last item from page 1
   ```
4. Poller detects it:
   ```
   [10:15:01] NEW SUPPORT EMAIL: Pagination bug in API
   [10:15:01] Launching Claude Code to triage...
   ```
5. **Claude Code launches in Alex's terminal** — audience watches it boot up with the triage prompt and start working
6. Alex: _"The poller just launched Claude Code with instructions. Claude has the same MCP gateway, same tools. It's going to do everything autonomously. Imagine this automation itself running in a Daytona sandbox."_

### Act 3: The Sandbox Developer

Claude Code is now working autonomously. Audience watches the terminal.

1. **Claude creates a Linear ticket** — calls `Linear.create_issue` through MCP gateway
   - Ticket appears: DEMO-XX, priority High, labels: Bug + auto-triage

2. **Claude calls `Daytona.create_sandbox`** — CATE pre-hook fires, blocks it
   - Claude sees: _"Sandbox creation requires human approval"_
   - Claude explains to Alex what it wants to create
   - **Alex approves** (clicks approve in dashboard or runs the curl)
   - Claude retries — sandbox created (~187ms)
   - Alex: _"That's HITL at the infrastructure layer. Not a prompt instruction that can be ignored — a webhook that blocks the API call."_

3. **Claude clones the repo** — calls `Daytona.git_clone`
   - `git_clone` declares `requires_auth=GitHub(scopes=["repo"])`
   - Alex already authorized GitHub → token injected per-command via credential helper
   - Token never written to sandbox filesystem
   - Alex: _"The sandbox has zero credentials on disk. Arcade injected the GitHub token for exactly the duration of that git clone, then it's gone."_

4. **Claude investigates** — runs tests, reads source, finds the off-by-one bug

5. **Claude fixes** — edits `handler.py`, runs tests again, all pass

6. **Claude ships** — creates branch, commits, pushes, opens PR
   - CATE pre-hook on `create_pull_request` auto-injects `ai-generated` + `auto-triage` labels

7. **Claude cleans up** — deletes the ephemeral sandbox

### Act 4: The Wrap-Up

Claude Code continues in the same session, now post-sandbox:

1. **Updates Linear** — ticket moved to "In Review" with PR link
2. **Sends Slack** — message to #demo-engineering:
   ```
   Auto-triage complete for ENG-847
   PR: https://github.com/arcade-demos/buggy-api/pull/12
   Fix: pagination off-by-one in handler.py. Tests pass.
   ```
3. **Creates Google Doc** — full triage report with timeline
4. Claude Code finishes and exits

Alex: _"Six services. Two HITL checkpoints. Isolated sandbox with governed credentials. And every action logged."_

### Act 5: The Audit

Alex returns to the first terminal.

1. Alex runs `./audit-check.sh` to view the CATE webhook audit trail in the terminal
   - Or asks Claude Code: **"Show me the audit trail for what just happened"**
2. Audience sees: every tool call, every governance decision, every HITL checkpoint — all in the terminal
3. Alex: _"This is what you hand to your CISO."_

---

## GitHub Auth Inside Daytona — How It Works

The `git_clone` tool declares auth requirements:

```python
@tool(
    requires_auth=get_github_auth(scopes=["repo"]),
    requires_secrets=[DAYTONA_API_KEY],
)
```

Flow when Claude Code calls `Daytona.git_clone` via MCP:

1. Engine sees `requires_auth` for GitHub
2. Checks if Alex has a valid GitHub token in Arcade's vault
3. Token passed to Daytona toolkit worker
4. Worker injects token via per-command credential helper:
   ```bash
   git -c credential.helper='!f() { echo username=x-access-token; echo password=<token>; }; f' clone ...
   ```
5. Token exists only for the duration of the git command
6. Never on sandbox filesystem, never in env vars

---

## Tools Inventory

**38 tools across 6 services, single MCP gateway**

| Service             | Key Tools Used                                                                                                                                                                                                                                                                                   | Auth    |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------- |
| **Linear** (7)      | `list_issues`, `create_issue`, `update_issue`, `list_teams`, `list_workflow_states`, `list_labels`, `get_issue`                                                                                                                                                                                  | OAuth   |
| **Slack** (3)       | `send_message`, `list_conversations`, `get_messages`                                                                                                                                                                                                                                             | OAuth   |
| **GitHub** (5)      | `create_pull_request`, `list_pull_requests`, `create_branch`, `get_file_contents`, `create_issue_comment`                                                                                                                                                                                        | OAuth   |
| **Google Docs** (2) | `create_document_from_text`, `get_document_by_id`                                                                                                                                                                                                                                                | OAuth   |
| **Gmail** (3)       | `search_threads`, `get_thread`, `list_emails`                                                                                                                                                                                                                                                    | OAuth   |
| **Daytona** (18)    | `create_sandbox`, `delete_sandbox`, `git_clone`, `git_create_branch`, `git_add`, `git_commit`, `git_push`, `git_status`, `run_command`, `read_file`, `write_file`, `search_content`, `find_files`, `list_files`, `run_code`, `create_session`, `run_session_command`, `get_session_command_logs` | API Key |

---

## Key Demo Talking Points

1. **"One gateway, 38 tools, 6 services"** — single MCP connection from Claude Code
2. **"OAuth managed, not manual"** — user authorizes once, Arcade handles token lifecycle
3. **"HITL at the infrastructure layer"** — not a prompt instruction, a webhook that blocks the API call
4. **"Credentials never touch the sandbox"** — per-command injection, then gone
5. **"Every action auditable"** — full governance pipeline visible in real-time
6. **"The agent adapted"** — blocked from pushing to main, created a branch instead

---

## Files to Create

| File                            | Purpose                          |
| ------------------------------- | -------------------------------- |
| `daytona-demo/.mcp.json`        | Claude Code MCP gateway config   |
| `daytona-demo/cate-config.yaml` | CATE webhook server rules        |
| `daytona-demo/email-poller.sh`  | Gmail poller → launches `claude` |
| `daytona-demo/setup.sh`         | Register plugin, hooks, gateway  |
| `daytona-demo/hitl-approve.sh`  | Quick HITL checkpoint approval   |
| `daytona-demo/audit-check.sh`   | Terminal-based audit log viewer  |
| `daytona-demo/smoke-test.sh`    | Full demo validation             |
| `daytona-demo/buggy-api/`       | Demo bug repo with failing test  |

## External Resources

| Resource                          | Purpose          |
| --------------------------------- | ---------------- |
| Gmail label `support-triage`      | Email trigger    |
| Linear team + project             | Ticket creation  |
| Slack `#demo-engineering` channel | Notifications    |
| CATE webhook test server          | Governance hooks |

---

## Risk Mitigations

| Risk                     | Mitigation                                  |
| ------------------------ | ------------------------------------------- |
| OAuth not pre-authorized | Complete all OAuth flows before demo starts |
| Email doesn't arrive     | Pre-plant email before demo                 |
| Sandbox creation slow    | Use snapshot-based creation                 |
| CATE webhook down        | `setup.sh` starts it with health check      |
| Network issues           | Can demo on localhost with local engine     |
