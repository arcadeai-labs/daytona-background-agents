# Background agents, governed

A working background agent, end to end. An email arrives and Claude Code autonomously triages it: creates a Linear ticket, spins up a Daytona sandbox, fixes the bug, opens a PR, and reports back. Nobody is watching while it works, and that is the point: every action runs through Arcade as the delegated user, checked by Contextual Access policy at the moment of execution, with a queryable audit trail.

Three layers, deliberately separated:

1. **The trigger is yours.** Here it's an email poller (`run.sh`). In your stack it's cron, a webhook, CI, or a workflow engine. Arcade doesn't wake your agent up; it governs what the agent can do once awake.
2. **The procedure is a skill.** The agent's entire behavior is `.claude/skills/support-triage/SKILL.md`, a markdown file the harness auto-loads. The runtime (Claude Code) is a commodity; the procedure is the asset.
3. **The governance is config.** `cate-config.yaml` holds three rules: a human-in-the-loop block on sandbox creation, branch protection on main, and auto-labeling of AI-generated PRs. Enforced gateway-side on every call; the agent can't opt out.

Companion reading: [How Does Arcade.dev Work With My Background Agents?](https://www.arcade.dev/blog/arcade-background-agents) covers the why; this repo is the how. `WORKSHOP.md` is a 60-minute live-workshop run-of-show built on this demo.

```
Your Machine                       Arcade Cloud                     Daytona Cloud
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

## Prerequisites

- **Claude Code** — installed and authenticated (`claude` available on PATH)
- **Arcade CLI** — installed and logged in (`arcade login`, then `arcade project set <project-id>`)
- **Go** — to build the CATE server binary (one-time)
- **ngrok** — for tunneling CATE webhooks to your machine
- **jq** — for JSON parsing in scripts
- **python3** — for token expiry checks

Verify everything is installed:

```bash
claude --version
arcade --version
go version
ngrok version
jq --version
python3 --version
```

## Setup

### 1. Configure `.env`

Open `.env` and update the two values marked **CHANGE**:

```bash
cd daytona-demo
```

| Variable           | Change?    | Description                                 |
| ------------------ | ---------- | ------------------------------------------- |
| `ARCADE_API_KEY`   | **CHANGE** | Your Arcade project API key (`arc_proj...`) |
| `ARCADE_USER_ID`   | **CHANGE** | Your Arcade email (e.g. `alex@arcade.dev`)  |
| `ENGINE_URL`       | No         | `https://api.arcade.dev`                    |
| `ENGINE_HOST`      | No         | `api.arcade.dev`                            |
| `CATE_WEBHOOK_URL` | No         | `http://localhost:8888`                     |
| `GATEWAY_SLUG`     | No         | `demo-gateway` (resolved by slug, not ID)   |
| `NGROK_AUTHTOKEN`  | No         | Pre-configured                              |
| `WATCH_SENDER`     | Optional   | Filter emails by sender address             |

`ORG_ID` and `PROJECT_ID` are auto-detected from `~/.arcade/credentials.yaml` (set by `arcade login` + `arcade project set`).

### 2. Log into Arcade CLI

```bash
arcade login
arcade project set <your-project-id>
```

The access token is read from `~/.arcade/credentials.yaml`. If it expires, re-login:

```bash
arcade logout && arcade login && arcade project set <your-project-id>
```

### 3. Authorize Google (Gmail)

The `run.sh` script handles this automatically — it will open a browser for Google OAuth if needed. You only need to do this once.

## Running the Demo

### Start everything with one command:

```bash
./run.sh
```

This single script does everything:

1. **Validates** `.env` and Arcade CLI credentials (checks token expiry)
2. **Generates** `.mcp.json` for Claude Code's MCP gateway connection
3. **Health-checks** the Arcade Engine
4. **Builds & starts** the CATE server on port 8888 (if not already running)
5. **Starts ngrok** tunnel to expose CATE webhooks publicly
6. **Registers** the CATE plugin + pre-execution hook in Arcade (cleans up stale ones)
7. **Configures** the MCP gateway with all 30+ demo tools (Gmail, Linear, Daytona, GitHub, Slack)
8. **Verifies** MCP connectivity by running an `initialize` + `tools/list` handshake
9. **Checks** Google OAuth status (opens browser if needed)
10. **Seeds** the processed-email list so only NEW emails trigger the agent
11. **Polls Gmail** every 15 seconds for new emails matching the filter

When a new email arrives, it launches `claude` with a three-line prompt that names the `support-triage` skill and hands over the email. The skill does the rest.

### What you'll see:

```
============================================================
  DEMO RUNNING
============================================================
  Engine:   https://api.arcade.dev
  MCP:      https://api.arcade.dev/mcp/demo-gateway
  CATE:     https://xxxx.ngrok-free.app (pre-execution sandbox check)
  Tools:    30 (Gmail, Linear, Daytona, Github, Slack)
  Watching: from:someone@example.com is:unread
  Polling:  every 15s
============================================================

  Waiting for an email...
```

### Trigger the demo:

Send an email from the `WATCH_SENDER` address (or apply the `support-triage` label to an email in Gmail). The poller picks it up and launches Claude Code.

## Demo Flow (What Claude Code Does)

Once triggered by an email, Claude Code autonomously:

1. **Creates a Linear ticket** — team: DEMO, priority: High, labels: Bug + auto-triage
2. **Creates a Daytona sandbox** — CATE blocks it (HITL checkpoint). A background watcher detects the block, waits 10 seconds (talk about governance here), then auto-approves. Claude Code retries and succeeds.
3. **Clones this repo** into the sandbox (`DEMO_REPO_URL` in `.env`)
4. **Navigates to `buggy-api/`** and runs tests — identifies the failing `test_page_two_starts_at_item_11`
5. **Reads source code** — finds the off-by-one error in `src/handler.py`
6. **Fixes the bug** — corrects the pagination logic
7. **Re-runs tests** — confirms all pass
8. **Creates branch, commits, pushes, opens PR** — CATE auto-labels it `ai-generated`
9. **Updates the Linear ticket** — moves to "In Review" with PR link
10. **Sends Slack summary** — posts to #demo-engineering

The HITL auto-approve delay is configurable via `HITL_APPROVE_DELAY` in `.env` (default: 10 seconds).

## CATE Governance Rules

Defined in `cate-config.yaml`:

| Rule                      | What it does                                                                  |
| ------------------------- | ----------------------------------------------------------------------------- |
| **HITL sandbox approval** | Blocks `Daytona.create_sandbox` — auto-approved after delay                   |
| **Branch protection**     | Blocks `Daytona.git_push` to `main`/`master`                                  |
| **PR auto-labeling**      | Injects `ai-generated` + `auto-triage` labels on `Github.create_pull_request` |

## Stopping the Demo

Press `Ctrl+C`. The cleanup handler automatically:

- Deletes the CATE hook and plugin from Arcade (prevents stale webhooks)
- Kills the CATE server and ngrok processes

## Troubleshooting

| Problem                      | Fix                                                                    |
| ---------------------------- | ---------------------------------------------------------------------- |
| `ARCADE_API_KEY missing`     | Fill in `.env`                                                         |
| `Not logged into Arcade CLI` | Run `arcade login`                                                     |
| `access token expired`       | Run `arcade logout && arcade login && arcade project set <project-id>` |
| `Engine not healthy`         | Check `ENGINE_URL` in `.env`, verify the engine is running             |
| `CATE failed to start`       | Check if port 8888 is in use: `lsof -i :8888`                          |
| `Could not get ngrok URL`    | Check ngrok auth token, or kill existing ngrok: `pkill -f ngrok`       |
| `MCP init failed`            | Gateway may not be configured — check Arcade dashboard                 |
| `Google auth not completed`  | Re-run `./run.sh`, it will re-prompt for OAuth                         |
| Email not triggering         | Check `WATCH_SENDER` matches the sender, or use `label:support-triage` |

## File Reference

| File                     | Purpose                                                          |
| ------------------------ | ---------------------------------------------------------------- |
| `run.sh`                 | Main entrypoint — sets up everything and starts the email poller |
| `setup.sh`               | Standalone setup script (registers plugin, hooks, gateway)       |
| `email-poller.sh`        | Standalone email poller (used by run.sh inline)                  |
| `.env`                   | Environment variables (credentials, IDs, config)                 |
| `.mcp.json`              | Auto-generated MCP gateway config for Claude Code                |
| `cate-config.yaml`       | CATE governance rules (HITL, branch protection, PR labels)       |
| `cate-server/`           | Pre-built CATE webhook server binary                             |
| `buggy-api/`             | Sample Python repo with intentional pagination bug               |
| `AGENT-PROMPT.md`        | Full agent prompt spec (for reference)                           |
| `demo-implementation.md` | Detailed implementation plan and 5-act flow                      |
