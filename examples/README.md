# Loop and graph engineering, minimally

Two runnable examples for the workshop's middle section. Both are stdlib-only
Python, no credentials, no network, no model — the control flow is the whole
point, so nothing else is on screen. Attendees can run them on a laptop with
`python3` and nothing else, same as the hands-on policy track in `WORKSHOP.md`.

```bash
python3 examples/loop_engineering.py
python3 examples/graph_engineering.py
```

## Loop engineering

> Designing the agentic loop — reason, act, observe, repeat — so an agent
> drives itself toward a goal instead of being prompted step by step. Coined
> mid-2026 as the successor to prompt engineering.

`loop_engineering.py` runs a fix-verify loop against a scratch copy of
`buggy-api/` (the planted bug survives — it copies to a temp dir). The model's
proposals are canned; everything the *loop* contributes is real:

| Concern | Why it's the loop's job, not the model's |
| --- | --- |
| **Budget** | A confused agent stops after N iterations instead of forever |
| **Exit test** | `pytest` green, not the agent's own claim that it's done |
| **Progress** | The failure signature must change, or the next iteration produces the same proposal |
| **Guardrail** | Editing test files is refused regardless of what the model proposes |
| **Verification** | Every patch is re-tested before it counts |
| **Revert** | A failed patch is undone, so bad edits don't stack |

Two paths to show:

```bash
python3 examples/loop_engineering.py           # green on iteration 3
python3 examples/loop_engineering.py --stuck   # escalates on repeated failure signature
```

The first iteration is the one to linger on: the model proposes weakening an
assertion, and the guardrail refuses. That's `SKILL.md` step 6 ("fix the code,
not the test") moved out of the prompt and into the harness, where the agent
can't talk its way around it.

## Graph engineering

> Designing the multi-agent *organization* as a topology — which nodes exist
> (agents, deterministic functions, routers, human checkpoints), which
> transitions are permitted, and how the work graph forms at runtime. Loops
> made agent behavior programmable; graphs make agent orgs programmable.

`graph_engineering.py` runs the same triage work as a 17-node graph. Three
things a loop can't express:

**Concurrency.** Three reviewers — correctness, security, tests — dispatch
simultaneously off `fix`. A loop reviews sequentially and pays 3x.

**Topology.** The human checkpoint is a *node*. While it's blocked,
`file_ticket` and `identity` finish, because they don't depend on it. In the
linear version a block stops the world.

**Governed edges.** `EDGE_POLICY` refuses the `verdict -> push` transition
unless 2 of 3 reviews pass. Policy on the edge, not in the prompt — the same
move `cate-config.yaml` makes at the gateway, expressed as topology.

```bash
python3 examples/graph_engineering.py                # 17/17, ~7.7s
python3 examples/graph_engineering.py --linear       # same nodes in order, ~9.2s
python3 examples/graph_engineering.py --skip-review  # edge denied, subtree pruned
```

Numbers from a local run:

```
sum of nodes     8100ms   <- what a single loop pays
critical path    5700ms   <- what the graph pays
```

The `--skip-review` run is the governance beat: the denied edge prunes five
downstream nodes, so the PR is never opened at all. Nothing crashed, and no
agent had to be trusted to skip its own work.

## Where this lands in the workshop

Between Act 1 (show the pieces) and Act 2 (fire the trigger). By then the room
has read `SKILL.md` as 13 numbered steps; these two examples are the answer to
"what if step 6 doesn't work the first time" (loop) and "what if the steps
aren't a line" (graph) — and both end on governance, which is where Act 2 picks
up.
