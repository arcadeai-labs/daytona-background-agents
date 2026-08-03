# Get cooking — participant setup

Two tracks. Track A needs no accounts and takes five minutes; do it while your
neighbor fights the conference wifi. Track B is the live-agent path we'll use in
the cold open.

## Track A — no accounts, 5 minutes

Everything that carries the governance argument runs on your laptop. Needs
`git`, `python3`, and `go`.

```bash
git clone https://github.com/arcadeai-labs/daytona-background-agents
cd daytona-background-agents

# a fix-verify loop with budgets, exit tests, and a guardrail
python3 examples/loop_engineering.py --stuck

# a 17-node agent graph with a human checkpoint and a governed edge
python3 examples/graph_engineering.py --skip-review

# run the policy engine and get told no
cd cate-server && go build -o cate-server . && ./cate-server -config ../cate-config.yaml -port 8888 &
cd .. && curl -s -X POST localhost:8888/pre -H 'Content-Type: application/json' -d '{
  "execution_id":"me-1","tool":{"name":"CreateSandbox","toolkit":"Daytona"},
  "inputs":{"name":"triage"},"context":{"user_id":"you@example.com"},"servers":{}}'
# -> CHECK_FAILED: HITL_CHECKPOINT — same denial the real agent gets
```

The debrief writes itself: the gateway doesn't care whether the caller is a
model or a human with curl. Same rules, same denials, same audit trail.

## Track B — connect a real agent, ~10 minutes

Three commands. No API key to copy, no IDs to hunt down, no dashboard — the
CLI logs you in through the browser and creates the gateway for you:

```bash
# 1. Install the CLI (pipx and pip work too)
uv tool install arcade-mcp

# 2. Log in — opens your browser, creates your account if you don't have one
arcade login

# 3. Create a gateway with three read-only GitHub tools and wire up your client
arcade connect claude-code \
  --tool Github.WhoAmI \
  --tool Github.GetUserRecentActivity \
  --tool Github.GetRepository
#               ^ or: cursor | vscode | windsurf | codex | gemini | opencode | amazonq
```

Read-only tools on purpose — nothing that writes is the right first thing to
hand a stranger's agent. Restart your client after step 3; auth is plain OAuth
handled by the client, and every call your agent makes is attributed to the
account you logged in with. That attribution is the whole point of the hour.

(`onboard.sh` in this repo does the same thing over the raw management API —
use it if your client isn't in that list or you want to see the calls.)

**First win.** Ask your client:

   > Summarize my last 3 commits.

   The first GitHub call returns an authorization link — click it, approve,
   done. That's delegated OAuth: Arcade holds *your* grant, scoped to what you
   approved, revocable without touching the agent. And the answer is *yours* —
   the person next to you asking the same question gets their commits, not
   your commits, because the gateway resolves every call through the identity
   that logged in.

   One honest caveat: "recent activity" reads through *your* grant, so it can
   include commits in **private** repos. Your data, your screen — but if you're
   projecting or screen-sharing, use the public fallback instead:

   > How many open issues does `arcadeai-labs/daytona-background-agents` have?

**If you're stuck** (corp laptop, VPN eats the OAuth callback, no MCP client):
pair with a neighbor or drop to Track A. Don't borrow anyone else's key — then
you're acting as them, in their accounts, and the audit trail will say so.

## Track C — run the whole background agent yourself (at home)

The full loop — email trigger → Linear ticket → governed sandbox → draft PR —
needs your own accounts on seven services and about an afternoon. The README's
"Track B" table lists every prerequisite honestly, including the two that cost
money. Start from a **fork** (the agent pushes branches and opens PRs against
`DEMO_REPO_URL`, so it has to be a repo you can write to).
