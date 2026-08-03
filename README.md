# Background agents, governed

A working background agent, end to end. An email arrives and Claude Code autonomously triages it: creates a Linear ticket, spins up a Daytona sandbox, fixes the bug, opens a PR, and reports back. Nobody is watching while it works, and that is the point: every action runs through Arcade as the delegated user, checked by Contextual Access policy at the moment of execution, with a queryable audit trail.

Three layers, deliberately separated:

1. **The trigger is yours.** Here it's an email poller (`run.sh`). In your stack it's cron, a webhook, CI, or a workflow engine. Arcade doesn't wake your agent up; it governs what the agent can do once awake.
2. **The procedure is a skill.** The agent's entire behavior is `.claude/skills/support-triage/SKILL.md`, a markdown file the harness auto-loads. The runtime (Claude Code) is a commodity; the procedure is the asset.
3. **The governance is config.** `cate-config.yaml` holds three rules: a human-in-the-loop block on sandbox creation, branch protection on main, and forcing every agent PR into draft. Enforced gateway-side on every call; the agent can't opt out.

Companion reading: [How Does Arcade.dev Work With My Background Agents?](https://www.arcade.dev/blog/arcade-background-agents) covers the why; this repo is the how. `WORKSHOP.md` is a 60-minute live-workshop run-of-show built on this demo.

**Want to poke at the mechanics without signing up for anything?** `examples/` has
two runnable pieces — loop engineering (budgets, exit tests, guardrails around a
fix-verify cycle) and graph engineering (parallel dispatch, human checkpoints as
nodes, policy on the edges between agents). Both need only `python3`. The policy
server in `cate-server/` also runs standalone: see the hands-on track in
`WORKSHOP.md` to get denied by it with `curl` in about a minute.

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

## Two ways in

**Track A — the mechanics, no accounts (10 minutes).** Everything that makes this
interesting except the live agent: the policy engine denying you, loop
engineering, graph engineering. Needs `python3`, `go`, and `git`. No signup, no
API key, no network. Start here.

```bash
git clone https://github.com/arcadeai-labs/daytona-background-agents
cd daytona-background-agents

python3 examples/loop_engineering.py --stuck            # a loop that knows to give up
python3 examples/graph_engineering.py --skip-review     # a denied handoff prunes the subtree

# Be the agent, and get told no:
cd cate-server && go build -o cate-server . && ./cate-server -config ../cate-config.yaml -port 8888 &
cd .. && curl -s -X POST localhost:8888/pre -H 'Content-Type: application/json' -d '{
  "execution_id":"me-1","tool":{"name":"CreateSandbox","toolkit":"Daytona"},
  "inputs":{"name":"triage"},"context":{"user_id":"you@example.com"},"servers":{}}'
# -> {"code":"CHECK_FAILED","error_message":"HITL_CHECKPOINT: ..."}
```

See `examples/README.md` and the hands-on track in `WORKSHOP.md` for the guided
version.

**Track B — the full loop (an afternoon, and it costs money).** The email-triggered
agent that files a ticket, fixes a bug in a cloud sandbox, and opens a PR. Be
honest with yourself about the setup before you start:

| Need | Why | Free? |
| --- | --- | --- |
| Anthropic plan or API key | Claude Code is the runtime | **No** — paid |
| Arcade account + project | Gateway, token vault, policy hooks | Signup required |
| Contextual Access (CATE) on your Arcade plan | `run.sh` registers a plugin + pre-execution hook. Without it, the governance half doesn't run. | **Verify for your plan** |
| Gmail, Linear, Slack, GitHub, Daytona | Five OAuth grants for the tools the agent calls | Signup each |
| A Linear team with a `Bug` label and an `In Review` state | `SKILL.md` expects both | — |
| ngrok account | Tunnels CATE webhooks to your laptop. One token per person — a shared token means everyone acts as you. | Free tier OK |
| A **fork** of this repo | The agent pushes a branch and opens a PR against `DEMO_REPO_URL` | — |
| macOS or Linux | `run.sh` uses BSD `sed -i ''` and `lsof`; on Windows use WSL | — |

CLI tooling for Track B:

```bash
claude --version    # Claude Code, authenticated
arcade --version    # Arcade CLI: arcade login && arcade project set <project-id>
go version          # builds the CATE server
ngrok version       # tunnels the webhook
jq --version        # JSON parsing in scripts
python3 --version   # token expiry checks
```

## Setup (Track B)

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
| `GATEWAY_SLUG`     | **CHANGE** | A slug unique to your project (see note)    |
| `DEMO_REPO_URL`    | **CHANGE** | **Your fork** of this repo (see note)       |
| `NGROK_AUTHTOKEN`  | No         | Pre-configured                              |
| `WATCH_SENDER`     | Optional   | Filter emails by sender address             |

`ORG_ID` and `PROJECT_ID` are auto-detected from `~/.arcade/credentials.yaml` (set by `arcade login` + `arcade project set`).

**Fork first.** The agent clones `DEMO_REPO_URL`, pushes a branch to it, and opens
a PR against it — so it must be a repo you can push to. Fork this repo, then set
`DEMO_REPO_URL` to your fork. Pointing it at someone else's repo fails at the push
step, and no amount of policy config will save you.

**Pick your own `GATEWAY_SLUG`.** `run.sh` creates the gateway if the slug doesn't
resolve and patches it if it does. Leaving the default means colliding with a
gateway that already exists in your project, which surfaces as
`db error: key already exists`. Use something project-specific.

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
8. **Creates branch, commits, pushes, opens PR** — CATE forces it to `draft: true`
9. **Updates the Linear ticket** — moves to "In Review" with PR link
10. **Sends Slack summary** — posts to #demo-engineering

The HITL auto-approve delay is configurable via `HITL_APPROVE_DELAY` in `.env` (default: 30 seconds, set at `run.sh:119`).

## CATE Governance Rules

Defined in `cate-config.yaml`:

| Rule                      | What it does                                                                  |
| ------------------------- | ----------------------------------------------------------------------------- |
| **HITL sandbox approval** | Blocks `Daytona.create_sandbox` — auto-approved after delay                   |
| **Branch protection**     | Blocks `Daytona.git_push` to `main`/`master`                                  |
| **PR forced to draft**    | Injects `draft: true` on `Github.CreatePullRequest` — a human must promote it |

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
| `cate-server/`           | Go source for the CATE webhook server (built by `run.sh`)         |
| `buggy-api/`             | Sample Python repo with intentional pagination bug               |
| `.claude/skills/support-triage/SKILL.md` | The agent's entire procedure                     |
| `hitl-approve.sh`        | Flip the HITL rule by hand (`--restore` to re-block)             |
| `audit-check.sh`         | Read the audit trail (`logs`, `clear`)                           |
| `audit-watch.sh`         | Tail policy decisions live during a run                          |
| `examples/`              | Loop- and graph-engineering examples (no credentials needed)      |
| `WORKSHOP.md`            | 60-minute live-workshop run-of-show                              |
| `docs/`                  | Blog draft, video script, PR receipt graphic                     |
