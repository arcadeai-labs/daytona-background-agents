# Background agents that won't get you fired — the handout

Everything from the hour, with the details the slides left out.
Repo: https://github.com/arcadeai-labs/daytona-background-agents

## 1. Connect Arcade (what you did in the first ten minutes)

```bash
uv tool install arcade-mcp        # pipx / pip install arcade-mcp also work
arcade login                      # browser OAuth — creates your account too
arcade connect claude-code \
  --tool Github.WhoAmI \
  --tool Github.GetUserRecentActivity \
  --tool Github.GetRepository
# other clients: cursor | vscode | windsurf | codex | gemini | opencode | amazonq
```

Restart your client, then ask: **"summarize my last 3 commits."** The first
GitHub call returns an OAuth link — approve it once. That's delegated auth:
Arcade holds *your* grant, scoped to what you approved, revocable without
touching the agent.

Caveat we said out loud: `GetUserRecentActivity` reads through your grant, so
it can include commits in **private** repos. Your data, your screen — but if
you're projecting, ask for this repo's open issue count instead.

No `arcade connect` for your client? `./onboard.sh` does the same thing over
the raw management API. Full detail: `ONBOARDING.md`.

## 2. The casino, decoded

The slot machine is the casino's background agent: it runs all night with
nobody watching, it can only play games the floor installed, at $1,200 it
locks and pages an attendant (that's a real IRS hand-pay threshold), and every
spin is on camera. Map it across:

| Casino | This demo | Mechanism |
| --- | --- | --- |
| Chips, not cash | Draft PR the agent never asked for | gateway injects `draft: true` (**stamp**) |
| Table limit | No pushes to `main`, ever | policy block (**constrain**) |
| Machine locks, pages attendant | `CreateSandbox` denied until a human approves | `HITL_CHECKPOINT` (**stop**) |
| Eye in the sky | Audit trail, every call attributed to a person | CATE log / OpenTelemetry |
| Player's card | `Arcade-User-Id` — who the agent acts *as* | delegated per-user OAuth |

The house lets players go wild because the floor is engineered for it. Same
deal: the agent plays as hard as it wants; the constraints live where it
can't reach them.

## 3. What you watched (the demo, beat by beat)

1. An email arrives — subject `buggy api`. The trigger is *ours* (a bash
   poller); yours can be cron, a webhook, CI.
2. Claude Code launches with a 3-line prompt naming one skill:
   `.claude/skills/support-triage/SKILL.md`. That file is the entire agent.
3. It files a Linear ticket — attributed to the human, not a bot token.
4. It asks Daytona for a sandbox and is **denied**: `HITL_CHECKPOINT`. It
   reads the denial, explains itself, waits, retries. A human clicks approve.
5. Clone, reproduce the failing test, fix `buggy-api/src/handler.py`
   (off-by-one: `offset = page * limit` → `(page - 1) * limit`), tests green.
6. Feature branch, PR — which arrives as a **draft it never requested**.
7. Ticket to In Review, Slack summary, sandbox deleted, and the audit trail
   shows every call, every denial, and the human click in between.

## 4. Run the governance loop yourself — no accounts

Needs `git`, `python3`, `go`. Nothing else.

```bash
git clone https://github.com/arcadeai-labs/daytona-background-agents
cd daytona-background-agents

# loop engineering: budgets, exit tests, a guardrail that refuses test edits
python3 examples/loop_engineering.py
python3 examples/loop_engineering.py --stuck      # escalates instead of spinning

# graph engineering: parallel reviewers, human checkpoint as a node, governed edges
python3 examples/graph_engineering.py
python3 examples/graph_engineering.py --skip-review   # denied edge prunes the subtree

# be the agent; get told no by the same policy engine
cd cate-server && go build -o cate-server . && ./cate-server -config ../cate-config.yaml -port 8888 &
cd .. && curl -s -X POST localhost:8888/pre -H 'Content-Type: application/json' -d '{
  "execution_id":"me-1","tool":{"name":"CreateSandbox","toolkit":"Daytona"},
  "inputs":{"name":"triage"},"context":{"user_id":"you@example.com"},"servers":{}}'

# be the human in the loop
./hitl-approve.sh            # approve; re-run the curl -> OK
./hitl-approve.sh --restore  # wall goes back up

# the projector view you watched, on your laptop
./dashboard.py               # http://localhost:7777  (#present for the deck)
```

The point of doing it with curl: the gateway doesn't care whether the caller
is a model or a human. Same rules, same denials, same audit trail.

## 5. Only have GitHub connected? It still works

The skill degrades on purpose: GitHub is the only hard requirement. No Linear
-> the triage report rides in the PR body. No Daytona sandbox -> the agent
fixes the file through the GitHub API and says plainly that a human must run
the tests. No Slack -> skipped and noted. And no Gmail needed at all:

```bash
./triage.sh        # fires the same agent with the canned bug report
```

The output is still the thing that matters: a draft PR it can't promote.

## 6. Run the full loop (an afternoon, at home)

The README's "Track B" table lists every prerequisite honestly — seven
services, two of which cost money, and a **fork** of the repo (the agent
pushes branches and opens PRs against `DEMO_REPO_URL`, so it must be a repo
you can write to). `WORKSHOP.md` is the full run-of-show if you want to give
this hour yourself.

## 7. The three lines to remember

- The runtime is a commodity. The procedure is a markdown file.
- Governance has three moves — stop, constrain, stamp — and all three are
  config, checked at the moment of the call.
- Your agent's permissions aren't in your prompt. They're wherever you put
  them — and if that's nowhere, you don't have permissions, you have hope.
