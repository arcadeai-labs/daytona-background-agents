# Background agents that won't get you fired — 60-minute workshop

The agent runtime is a commodity. The harness era is here (Claude Code, Cursor,
ChatGPT). What's scarce is everything around it: authenticated tools, reusable
procedures, and the governance that lets an agent run with nobody watching.

That's the whole workshop. By the end of the hour the room has watched an email
turn into a merged-ready PR with no human driving, and can name exactly which layer
did what: the trigger they own, the procedure shipped as a skill, and the policy
that governed every action in between.

## What you need before the room fills

Run the pre-flight the night before AND the morning of. Live demos die from stale
tokens, not bad code.

```bash
cd daytona-background-agents

# Tooling present
claude --version && arcade --version && ngrok version && jq --version

# Arcade CLI logged in and pointed at the right project
arcade whoami

# .env filled in (API key, user id, ngrok token)
grep -c "YOUR_KEY_HERE\|your_ngrok" .env && echo "STOP: .env has placeholders" || echo ".env ok"

# The bug is still planted (test must FAIL)
cd buggy-api && python3 -m pytest -q; cd ..
```

Also confirm: Gmail, Linear, GitHub, Slack, and Daytona are all connected for your
Arcade user (first tool call will prompt OAuth otherwise), and the `DEMO` team
exists in Linear with a `Bug` label.

Reset state between runs:

```bash
./audit-check.sh clear
rm -f /tmp/arcade-demo-*.txt
```

---

## Act 0 — The claim (5 min)

No terminal yet. One slide or just say it:

> "Background agents run on a schedule or a trigger. Nobody is there to click
> approve. Today you'll watch one fix a bug end to end, and the interesting part
> is not that it can. It's everything that stops it from doing anything else."

Name the three layers on the whiteboard:

1. **Trigger** — you own this. Today it's an email poller. Tomorrow it's cron,
   a webhook, CI, Inngest. Arcade doesn't wake your agent up.
2. **Procedure** — a skill, not an agent. A markdown file the harness loads.
3. **Governance** — Arcade's gateway: delegated per-user auth, policy checked at
   the moment of every action, audit trail.

## Act 1 — Show the pieces (10 min)

Everything fits on screen. That's the point.

```bash
ls
```

Walk three files, in this order:

```bash
cat .claude/skills/support-triage/SKILL.md
```

> "This is the agent. All of it. A numbered procedure with governance notes.
> The harness is Claude Code, unmodified. If we switch to Cursor next quarter,
> this file comes with us."

```bash
cat cate-config.yaml
```

> "This is the policy. Three rules, three flavors of governance. Sandbox creation
> is blocked pending a human: that's stop. Pushes to main are blocked always:
> that's constrain. Every PR is forced into draft: that's stamp. The agent
> can't opt out of any of them, because they run gateway-side, not agent-side."

```bash
cat buggy-api/api.py   # or wherever the bug lives — show the pagination code
cd buggy-api && python3 -m pytest -q; cd ..
```

> "And this is the victim: a real repo with a real failing test."

## Act 2 — Fire the trigger (15 min)

Start the demo loop, then send the email from your phone, on camera, from the
audience's point of view:

```bash
./run.sh
```

Send an email to the watched inbox: subject **buggy api**, body describing the
pagination bug. Then narrate what the terminal shows:

1. Poller sees the email, launches `claude` with a three-line prompt that names
   the skill and hands over the email. Say the line: **"the skill does the rest."**
2. The agent files the Linear ticket. Point at the per-user attribution: it acted
   as *you*, via your OAuth grant, not as a bot token.
3. The agent asks Daytona for a sandbox and **hits the wall**:

```
HITL_CHECKPOINT: Sandbox creation requires human approval.
```

Stop here. This is the money beat. Let the room read the agent's own explanation
of why it's blocked and that it's waiting.

> "The deny is a message the agent can read. It knows why it stopped. Nothing
> crashed. And notice what the approval is: not a click in our app, but an
> out-of-band change to policy."

The watcher auto-approves after 30 seconds (or run `./hitl-approve.sh` yourself
for effect). The agent retries the same call and proceeds.

## Act 3 — Watch it work (10 min)

Mostly narration while the agent runs. Beats to call out as they scroll past:

- Clone into the sandbox, git identity set from `Github.WhoAmI`: attribution again.
- Failing test reproduced, fix applied, tests green.
- Branch `fix/buggy-api-<timestamp>` pushed. If anyone asks "what if it pushed to
  main": it can't. Offer to prove it in Q&A by asking the agent to try.
- PR opens **as a draft the agent never asked
  for**. Show the PR in the browser. Policy stamped it.
- Ticket moves to In Review, Slack summary lands in `#demo-engineering`.

## Act 4 — The receipts (10 min)

The demo isn't the PR. The demo is the audit trail:

```bash
./audit-check.sh logs
```

> "Every tool call, every input, every policy decision, attributable to the human
> the agent acted for. In production this streams to your SIEM over OpenTelemetry.
> An agent running while you sleep is as accountable as one you're watching."

Then the live policy change, the beat that lands hardest with security folks:

```bash
./hitl-approve.sh --restore   # re-block sandbox creation
```

Send a second bug email. The agent gets blocked again, live, because policy is
evaluated at the moment of action, not at setup. Revoke a user, downgrade a role,
change a rule: the running agent feels it on its next call.

## Act 5 — What you'd take home (10 min)

Close the loop on the thesis:

- **The trigger was 100 lines of bash.** Replace it with whatever fires in your
  stack. Arcade's job starts at the first tool call.
- **The agent was a markdown file.** Version it, review it, ship it like code.
- **The governance was config.** No changes to tools, no changes to the agent,
  no SDK in your way.

Q&A prompts that work well: "what happens if the agent edits the test instead of
the code?" (the skill forbids it, the PR review catches it, and the audit log
proves it), and "why not a service account?" (walk the delegated-auth section of
the README).

---

## Attendee hands-on track (no Arcade account required)

The live demo needs one set of credentials (yours). The hands-on doesn't: the
governance loop runs entirely locally, so every attendee can feel the mechanics
on their own laptop with just `git`, `go`, and `python3`. Slot this after Act 2,
or run it as the second half hour if the room skews hands-on.

**1. Clone and reproduce the bug (5 min):**

```bash
git clone https://github.com/arcadeai-labs/daytona-background-agents
cd daytona-background-agents/buggy-api
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q        # 2 failed, 1 passed — the bug is real
cd ..
```

**2. Run the policy server and get told no (10 min):**

```bash
cd cate-server && go build -o cate-server . && ./cate-server -config ../cate-config.yaml -port 8888 &
cd ..

# You are the agent. Ask for a sandbox:
curl -s -X POST localhost:8888/pre -H 'Content-Type: application/json' -d '{
  "execution_id":"me-1",
  "tool":{"name":"CreateSandbox","toolkit":"Daytona"},
  "inputs":{"name":"triage"},
  "context":{"user_id":"you@example.com"},"servers":{}
}'
# -> {"code":"CHECK_FAILED","error_message":"HITL_CHECKPOINT: ..."}
```

Have them try the other two rules: `GitPush` with `"branch":"main"` (blocked)
versus a feature branch (OK), and `CreatePullRequest` (OK, but look at the
`override` in the response: `draft: true` got injected).

**3. Be the human in the loop (5 min):**

```bash
./hitl-approve.sh            # flip the rule
# re-run the CreateSandbox curl -> {"code":"OK"}
./hitl-approve.sh --restore  # put the wall back
# re-run -> blocked again. Policy changed while "the agent" was running.
```

**4. Read the receipts (5 min):**

```bash
./audit-check.sh logs        # every request they just made, with the decision
```

The debrief writes itself: everything they just did with curl is exactly what
the agent experienced in the live demo, which is the point. Governance lives in
the gateway, so it doesn't matter whether the caller is a model or a human with
curl. Same rules, same denials, same audit trail.

---

## Contingencies

| Failure | Recovery |
| --- | --- |
| Arcade token expired mid-demo | `arcade logout && arcade login`, restart `run.sh`. Pre-flight catches this. |
| Gmail poller sees nothing | Check `WATCH_SENDER` filter and the `buggy api` subject; send from the allowed address. |
| Agent skips the skill | The poller prompt names the skill explicitly; if it still free-styles, restart and blame the demo gods, then show the SKILL.md as the artifact. |
| HITL approval doesn't fire | Run `./hitl-approve.sh` manually; the watcher is a convenience, not the mechanism. |
| Daytona sandbox slow to start | Narrate the audit log (`./audit-check.sh logs`) while waiting; it's the better content anyway. |
| Wifi dies | The recorded run. Always have the recorded run. |
