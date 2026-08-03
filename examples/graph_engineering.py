#!/usr/bin/env python3
"""Graph engineering: the triage org as a topology, with governed edges.

Loop engineering made one agent's behavior programmable. Graph engineering
makes the *organization* programmable: which nodes exist (agents,
deterministic functions, routers, human checkpoints), which transitions
between them are permitted, and how the work graph forms at runtime.

Two things a loop can't express, both on screen below:

  concurrency   - three reviewers dispatched at once, not one after another
  topology      - a human checkpoint is a node, so a block stalls its
                  subtree while unrelated nodes keep working

And the part that matters for governance: in a graph, policy attaches to
*edges*, not just nodes. "The fixer may hand off to the pusher only if a
reviewer approved" is an edge rule. That's the same check cate-config.yaml
makes at the gateway, expressed in the topology instead of the prompt.

No credentials, no network - each node sleeps for its simulated cost.

    python3 examples/graph_engineering.py
    python3 examples/graph_engineering.py --linear     # the same org, one loop
    python3 examples/graph_engineering.py --skip-review  # edge policy denies a handoff
"""

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

# node -> (kind, dependencies, simulated cost in ms)
#
# kinds: agent (model-driven), fn (deterministic), human (checkpoint),
#        router (fan-in decision)
GRAPH = {
    "intake":         ("agent",  [], 300),
    "file_ticket":    ("fn",     ["intake"], 400),
    "identity":       ("fn",     ["intake"], 200),
    "approve_sandbox": ("human", ["intake"], 100),
    "sandbox":        ("fn",     ["approve_sandbox"], 600),
    "clone":          ("fn",     ["sandbox", "identity"], 900),
    "reproduce":      ("agent",  ["clone"], 700),
    "fix":            ("agent",  ["reproduce"], 900),
    # Three reviewers, three lenses, dispatched together. The thing a loop
    # would have run sequentially for 3x the wall clock.
    "review_correct": ("agent",  ["fix"], 800),
    "review_security": ("agent", ["fix"], 700),
    "review_tests":   ("agent",  ["fix"], 600),
    "verdict":        ("router", ["review_correct", "review_security", "review_tests"], 200),
    "push":           ("fn",     ["verdict"], 400),
    "open_pr":        ("fn",     ["push"], 300),
    "update_ticket":  ("fn",     ["open_pr"], 300),
    "notify":         ("fn",     ["open_pr"], 200),
    "report":         ("agent",  ["open_pr"], 500),
}

# Edge policy: transitions the graph refuses regardless of what any agent
# decides. Read as "src -> dst is only allowed if <guard> holds."
EDGE_POLICY = {
    ("verdict", "push"): "at least 2 of 3 reviews must pass",
}

HUMAN_APPROVES_AFTER_MS = 1500

print_lock = Lock()
START = time.monotonic()
waited_on_human = [0]


def elapsed_ms():
    return int((time.monotonic() - START) * 1000)


def say(node, event, detail=""):
    with print_lock:
        kind = GRAPH[node][0] if node in GRAPH else ""
        print(f"  {elapsed_ms():>5}ms  {node:<16} {kind:<6} {event:<9} {detail}")


def run_node(name, kind, cost_ms, reviews_pass):
    """Returns True if the node completed, False if it must be retried."""
    if kind == "human" and elapsed_ms() < HUMAN_APPROVES_AFTER_MS:
        say(name, "BLOCKED", "HITL_CHECKPOINT - subtree stalls, siblings continue")
        return False
    say(name, "start")
    time.sleep(cost_ms / 1000)
    if name == "verdict":
        passed = sum(reviews_pass)
        say(name, "done", f"{passed}/3 reviews passed")
        return True
    say(name, "done")
    return True


def edge_allowed(src, dst, reviews_pass):
    """Governance on the edge, not in the prompt. No agent can talk past this."""
    guard = EDGE_POLICY.get((src, dst))
    if guard is None:
        return True, ""
    if (src, dst) == ("verdict", "push") and sum(reviews_pass) < 2:
        return False, guard
    return True, ""


def unreachable_from(start, pending):
    """`start` plus everything in `pending` that transitively depends on it."""
    doomed, changed = {start}, True
    while changed:
        changed = False
        for n in pending:
            if n not in doomed and any(d in doomed for d in GRAPH[n][1]):
                doomed.add(n)
                changed = True
    return doomed


def critical_path():
    memo = {}

    def cost_to(name):
        if name not in memo:
            _, deps, ms = GRAPH[name]
            best = max((cost_to(d) for d in deps), default=(0, []))
            memo[name] = (best[0] + ms, best[1] + [name])
        return memo[name]

    return max((cost_to(n) for n in GRAPH), key=lambda x: x[0])


def run_graph(reviews_pass):
    done, retry_after, denied = set(), {}, []
    pending, running = dict(GRAPH), {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        while pending or running:
            ready = []
            for n, (kind, deps, ms) in list(pending.items()):
                if not all(d in done for d in deps):
                    continue
                if elapsed_ms() < retry_after.get(n, 0):
                    continue
                blocked_edge = next(
                    ((d, n) for d in deps if not edge_allowed(d, n, reviews_pass)[0]), None
                )
                if blocked_edge:
                    if blocked_edge not in denied:
                        denied.append(blocked_edge)
                        src, dst = blocked_edge
                        say(dst, "DENIED", f"edge {src} -> {dst}: {EDGE_POLICY[blocked_edge]}")
                    # Prune the whole unreachable subtree, not just this node -
                    # otherwise its dependents wait on a node that never arrives.
                    for pruned in unreachable_from(n, pending):
                        pending.pop(pruned, None)
                    continue
                ready.append(n)
            for n in ready:
                kind, _, ms = pending.pop(n)
                running[pool.submit(run_node, n, kind, ms, reviews_pass)] = n
            if not running:
                if not pending:
                    break
                waited_on_human[0] += 50
                time.sleep(0.05)
                continue
            for fut in list(running):
                if not fut.done():
                    continue
                n = running.pop(fut)
                if fut.result():
                    done.add(n)
                else:
                    pending[n] = GRAPH[n]      # the deny is a message, not a crash
                    retry_after[n] = elapsed_ms() + 500
            time.sleep(0.02)
    return done, denied


def run_linear(reviews_pass):
    """The same org squeezed into one sequential loop, for comparison."""
    for name, (kind, _, ms) in GRAPH.items():
        while not run_node(name, kind, ms, reviews_pass):
            time.sleep(0.5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--linear", action="store_true", help="run the same nodes sequentially")
    ap.add_argument("--skip-review", action="store_true", help="make 2 of 3 reviews fail")
    args = ap.parse_args()

    reviews_pass = [True, False, False] if args.skip_review else [True, True, True]
    mode = "linear (one loop, 17 steps in order)" if args.linear else "graph (topology-ordered)"
    print(f"mode: {mode}")
    print(f"reviews: {sum(reviews_pass)}/3 will pass\n")

    if args.linear:
        run_linear(reviews_pass)
        done, denied = set(GRAPH), []
    else:
        done, denied = run_graph(reviews_pass)

    wall = elapsed_ms()
    sequential = sum(ms for _, _, ms in GRAPH.values())
    cp_cost, cp_nodes = critical_path()

    print(f"\n  nodes completed  {len(done)}/{len(GRAPH)}")
    print(f"  wall clock       {wall}ms  (of which ~{waited_on_human[0]}ms waiting on the human)")
    print(f"  sum of nodes     {sequential}ms   <- what a single loop pays")
    print(f"  critical path    {cp_cost}ms   <- what the graph pays")
    print(f"                   {' -> '.join(cp_nodes)}")
    if denied:
        for src, dst in denied:
            print(f"  edge denied      {src} -> {dst}, and everything downstream never ran")
    return 0


if __name__ == "__main__":
    sys.exit(main())
