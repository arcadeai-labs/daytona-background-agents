# Video script - a background agent hits the wall (and that's the point)

**Format:** screen recording + voiceover, no face cam needed
**Target length:** ~4-5 min
**Companion to:** the background-agents blog posts (Megan's why + the technical how)

## Before you hit record

```bash
cd ~/daytona-background-agents
./audit-check.sh clear                      # empty audit log
rm -f /tmp/arcade-demo-*.txt                # reset processed-email + HITL state
cd buggy-api && python3 -m pytest -q; cd .. # confirm 2 tests FAIL (the bug is planted)
grep -c "YOUR_KEY_HERE" .env && echo "STOP" # confirm .env is real
```

Terminal: large font, dark theme. Second monitor: the GitHub repo and Linear board
in a browser, ready to alt-tab. Silence notifications. Have the recorded backup run.

---

## COLD OPEN (~15s)

*Screen: empty terminal.*

> "This is a background agent. In four minutes it's going to read a bug report
> from an email, fix the bug in a cloud sandbox, and open a PR, with nobody
> watching. And the best moment is going to be when it gets told no."

## BEAT 1 - Three files (~45s)

```bash
cat .claude/skills/support-triage/SKILL.md
```

*Scroll slowly. Pause on the Governance section.*

> "There's no agent framework here. The agent is this markdown file, a numbered
> procedure the harness auto-loads. Notice it's told that a denial called
> HITL_CHECKPOINT is governance, and to wait, not crash."

```bash
cat cate-config.yaml
```

> "And this is the entire policy layer. Sandbox creation blocked until a human
> approves. Pushes to main blocked always. And every PR this agent opens is
> forced into draft on the way through. Three rules: stop, constrain,
> stamp. Enforced at the gateway, so the agent can't opt out."

```bash
cd buggy-api && python3 -m pytest -q; cd ..
```

> "Two failing tests. A real pagination bug. That's our victim."

## BEAT 2 - Fire the trigger (~40s)

```bash
./run.sh
```

*Wait for `Waiting for an email...`, then send the email on camera (phone or a
second window): subject `buggy api`, body describing the pagination bug.*

> "The trigger is ours, not Arcade's. Here it's an email poller; in your stack
> it's cron, a webhook, CI. Arcade's job starts at the agent's first tool call,
> which runs as me, through my OAuth grants, checked against policy every time."

*Poller detects the email, launches Claude Code with the three-line prompt.*

> "Three lines. The skill does the rest."

## BEAT 3 - The wall (money beat, ~60s)

*The agent files the Linear ticket (alt-tab to Linear, two seconds), then asks
Daytona for a sandbox and gets blocked. Zoom on:*

```
HITL_CHECKPOINT: Sandbox creation requires human approval.
```

*Let the agent's own explanation render. Read along with it.*

> "This is the moment that matters. The agent asked for compute and policy said
> no. The deny is a message the agent can read, so it explains what it wanted
> and waits. Nothing crashed. And approval isn't a button in some agent UI.
> It's an out-of-band policy change."

*The watcher auto-approves (or run `./hitl-approve.sh` in a second pane for
effect). The agent retries the same call and proceeds.*

> "Approved. Same call, second attempt, sails through. No agent code changed."

## BEAT 4 - Watch it work (~60s)

*Timelapse or narrate over the run. Call out as they scroll:*

- git identity set from `Github.WhoAmI` → "its commits belong to a human"
- tests fail → fix → tests pass
- branch `fix/buggy-api-<timestamp>` pushed → "it can't push to main; policy
  matches on the branch input itself, so don't-touch-main is enforced, not hoped"
- *alt-tab to GitHub:* the PR - opened as a **draft** the agent couldn't opt out of

> "The agent never asked to open a draft. The gateway rewrote the PR inputs on
> the way through. Your reviewers get a guaranteed signal about which PRs came
> from an agent."

- Linear ticket flips to In Review; Slack summary lands.

## BEAT 5 - The receipts (~45s)

```bash
./audit-check.sh logs
```

*Scroll the JSON, then flatten it verbally:*

> "Every tool call, every input, every decision: blocked, allowed, or rewritten,
> attributed to the user the agent acted for. In production this streams to your
> SIEM over OpenTelemetry. An agent that ran while you slept is as accountable
> as one you watched."

## CLOSE (~20s)

*Screen: the repo README.*

> "The trigger was a hundred lines of bash you'd replace with your own stack.
> The agent was a markdown file. The governance was config. Repo's linked below.
> Clone it, plant your own bug, and watch your agent hit the wall."

---

## Contingencies

| Failure | Recovery |
| --- | --- |
| Arcade token expired | `arcade logout && arcade login`, restart. Pre-flight catches it. |
| Poller misses the email | Check `WATCH_SENDER` and the `buggy api` subject. |
| Agent free-styles past the skill | Cut, reset state, rerun. The skill prompt names it explicitly; this is rare. |
| HITL auto-approve doesn't fire | `./hitl-approve.sh` in the second pane. Honestly a better shot anyway. |
| Sandbox slow | Cut to Linear/GitHub tabs while it provisions; tighten in edit. |
| Anything else | The recorded backup run. Always have the recorded backup run. |
