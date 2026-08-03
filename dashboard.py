#!/usr/bin/env python3
"""Governance dashboard - the projector view of what the agent is doing.

The terminal is the wrong surface for a room: the interesting events scroll past
in 10pt monospace between pages of agent chatter. This serves one page, legible
from the back row, showing every tool call the agent makes and what policy
decided about it. Click any row for the full request.

Three states, which are the three governance moves in cate-config.yaml:

  ALLOWED   policy let it through
  BLOCKED   policy stopped it        (HITL checkpoint, push to main)
  STAMPED   policy rewrote the call  (draft: true on every PR)

Why it keeps its own ledger: run.sh:226 issues DELETE /_logs inside the HITL
approve path, so CATE's own log loses the denial seconds after it happens - the
exact entry Act 4 needs. This polls continuously, dedupes, and appends to
.dashboard-ledger.json, so the history survives both that wipe and a restart.

Stdlib only. Proxies CATE so the browser isn't fighting CORS.

    ./dashboard.py                    # http://localhost:7777
    ./dashboard.py --reset            # start a fresh ledger
    ./dashboard.py --port 8080 --cate-port 8888
"""

import argparse
import json
import pathlib
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = pathlib.Path(__file__).resolve().parent
LEDGER = HERE / ".dashboard-ledger.json"

# The only commands the page can run. Fixed argv lists, never shell strings and
# never anything the browser supplies - the page picks an action by name.
ACTIONS = {
    "trigger": ["./trigger-email.sh"],
    "approve": ["./hitl-approve.sh"],
    "restore": ["./hitl-approve.sh", "--restore"],
}

# Toolkits this demo actually uses. Anything else is another session sharing the
# gateway (it happens) and is hidden behind the "everything" filter.
DEMO_TOOLKITS = {"Daytona", "Github", "GitHub", "Linear", "Slack", "Gmail"}

_lock = threading.Lock()
_ledger = []
_seen = set()


def _key(e):
    b = e.get("body") or {}
    return (
        e.get("timestamp", ""),
        e.get("endpoint", ""),
        b.get("execution_id", ""),
        ((b.get("tool") or {}).get("name", "")),
    )


def load_ledger():
    global _ledger, _seen
    if LEDGER.exists():
        try:
            _ledger = json.loads(LEDGER.read_text())
            _seen = {_key(e) for e in _ledger}
        except (json.JSONDecodeError, OSError):
            _ledger, _seen = [], set()


def save_ledger():
    try:
        LEDGER.write_text(json.dumps(_ledger))
    except OSError:
        pass


RUN_LOG = HERE / ".dashboard-run.log"

# The deck, folded in so the whole hour happens on one page. Copy is lifted from
# the HyperFrames deck verbatim - that version renders blank past slide 1, and a
# plain DOM deck can't fail that way on stage.
#   kicker: small label above  ·  title: the line  ·  body: the paragraph
#   items:  [label, text] pairs  ·  code: monospace block  ·  act: clock hint
SLIDES = [
    {'act': 'holding', 'kicker': 'walk-in', 'title': "Background agents that won't get you fired", 'body': 'Want to cook along? Fork this repo now and open HANDOUT.md. Everyone else: sit tight, we start with a question.', 'code': 'github.com/arcadeai-labs/daytona-background-agents\n\nfork it. clone it. open HANDOUT.md.'},
    {'act': '0:03', 'kicker': 'the example', 'title': 'The slot machine is a background agent.', 'items': [['unattended', 'Runs all night. Nobody watching.'], ['bounded', 'Only plays games the floor installed.'], ['HITL', 'At $1,200 it locks and pages a human.'], ['audited', 'Every spin on camera.']]},
    {'act': '0:06', 'kicker': 'our version', 'title': "It's 3 a.m. A user is stuck.\nWhat can your agent do to help?", 'body': 'Same floor. Different building.'},
    {'act': '0:08', 'kicker': 'now you connect', 'cue': 'you, before doors: press start · wait for the green READY pill · reset feed', 'title': 'Your turn: give a model real reach.', 'body': "Connect, then ask: “summarize my last 3 commits.” (Sharing your screen? Ask for this repo's open issues instead.)", 'code': 'uv tool install arcade-mcp\narcade login\narcade connect claude-code \\\n  --tool Github.WhoAmI \\\n  --tool Github.GetUserRecentActivity \\\n  --tool Github.GetRepository'},
    {'act': '0:16', 'kicker': 'before the demo', 'title': 'Ask it: “what would you use this for?”', 'body': 'Hold that thought.'},
    {'act': '0:18', 'kicker': 'three layers', 'title': 'Arcade only owns one layer.', 'items': [['trigger', 'Yours. cron, webhook, email.'], ['procedure', 'One markdown file.'], ['governance', "Config the agent can't opt out of."]]},
    {'act': '0:19', 'kicker': 'the three moves', 'title': 'Stop. Constrain. Stamp.', 'items': [['stop', 'Sandbox blocks until a human. the pit boss.'], ['constrain', 'No pushes to main. the table limit.'], ['stamp', 'Every PR forced to draft. chips, not cash.']]},
    {'act': '0:22', 'kicker': 'going live', 'cue': 'Esc → live feed · press SEND TRIGGER EMAIL · when the red row lands, talk, then press APPROVE SANDBOX', 'title': "Watch what it does when it's told no.", 'code': 'HITL_CHECKPOINT: Sandbox creation requires human approval.'},
    {'act': '0:48', 'kicker': 'the receipts', 'cue': 'Esc → feed · click the STAMPED row: draft:true it never asked for · click a BLOCKED row: rule_match', 'title': "The demo isn't the PR.\nIt's the audit trail.", 'body': 'The eye in the sky, for agents.'},
    {'act': '0:50', 'kicker': 'the moment', 'cue': 'Esc → feed · press RE-BLOCK (Act 4) · watch the CreateSandbox pill flip to block, live', 'title': 'Policy is checked when the agent acts,\nnot when you deployed it.'},
    {'act': 'extra', 'kicker': 'loop engineering', 'title': "A loop that can't stop is a runaway.", 'items': [['budget', 'Hard cap on iterations.'], ['exit test', 'Tests green, not self-report.'], ['guardrail', 'It tried to weaken a test. Refused.']]},
    {'act': 'extra', 'kicker': 'graph engineering', 'title': 'Graphs make agent orgs programmable.', 'items': [['concurrency', 'Three reviewers at once.'], ['topology', 'A block stalls a subtree, not the world.'], ['edges', 'No push unless 2 of 3 reviews pass.']]},
    {'act': '0:55', 'kicker': 'takeaways', 'title': 'Take three things home.', 'items': [['trigger', '100 lines of bash.'], ['agent', 'A markdown file.'], ['governance', 'Config.']]},
    {'act': '1:00', 'kicker': 'your turn', 'title': 'Run the governance loop yourself.\nNo account required.', 'code': 'git clone github.com/arcadeai-labs/daytona-background-agents\nopen HANDOUT.md   # every command from this hour'},
    {'act': 'Q&A', 'kicker': 'question', 'title': 'What if it edits the test instead of the code?', 'items': [['skill', 'Forbids it.'], ['review', 'The draft PR catches it.'], ['audit', 'The log proves it.']]},
    {'act': 'Q&A', 'kicker': 'question', 'title': 'Why not a service account?', 'items': [['attribution', 'The log would name a robot.'], ['scope', "Union of everyone's permissions."], ['delegated', 'Revoke the human, the agent loses it.']]},
]


def start_demo():
    """Launch run.sh detached, so it outlives this request and keeps polling.

    Deliberately not in ACTIONS: run.sh is long-running, so it can't be handled
    by the capture-output-and-wait path the other actions use."""
    if is_running("run.sh"):
        return True, "already running - check the pill: if it says READY, just press 'send trigger email'"
    try:
        with open(RUN_LOG, "wb") as log:
            subprocess.Popen(
                ["./run.sh"],
                cwd=HERE,
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        return True, f"run.sh started - arming takes ~30s. Log: {RUN_LOG.name}"
    except OSError as e:
        return False, str(e)


def stop_demo():
    """run.sh traps its own exit and deletes the CATE hook and plugin, so a plain
    TERM is the correct way to stop it - never KILL, or the hooks leak."""
    if not is_running("run.sh"):
        return False, "run.sh is not running"
    subprocess.run(["pkill", "-f", "run.sh"], capture_output=True)
    return True, "sent TERM to run.sh - it cleans up its own hook and plugin"


def is_running(pattern):
    return (
        subprocess.run(["pgrep", "-f", pattern], capture_output=True).returncode == 0
    )


def reset_feed(cate_port):
    """Clean slate for the projector: wipe the ledger AND CATE's own log -
    clearing only one means the poller re-ingests the other within a second.
    Leaves /tmp/arcade-demo-processed.txt alone on purpose: deleting it while
    run.sh polls would re-fire the agent on every old unread trigger email."""
    try:
        req = urllib.request.Request(
            f"http://localhost:{cate_port}/_logs", method="DELETE"
        )
        urllib.request.urlopen(req, timeout=3).read()
    except (urllib.error.URLError, OSError):
        pass  # CATE down is fine - the ledger wipe still applies
    with _lock:
        _ledger.clear()
        _seen.clear()
        save_ledger()
    return True, "feed reset - ledger and CATE log cleared, fresh wall"


def run_action(name, cate_port=8888):
    """Run one of the fixed ACTIONS and return (ok, combined output)."""
    if name == "start":
        return start_demo()
    if name == "stop":
        return stop_demo()
    if name == "reset":
        return reset_feed(cate_port)
    argv = ACTIONS.get(name)
    if not argv:
        return False, f"unknown action: {name}"
    try:
        p = subprocess.run(
            argv, cwd=HERE, capture_output=True, text=True, timeout=60
        )
        return p.returncode == 0, (p.stdout + p.stderr).strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return False, "timed out after 60s"
    except OSError as e:
        return False, str(e)


def demo_state(cate_port):
    """Everything the stage operator needs to know at a glance."""
    st = {"policy": [], "armed": False, "run_sh": False, "token_min": None}

    try:
        with urllib.request.urlopen(
            f"http://localhost:{cate_port}/_config", timeout=3
        ) as r:
            rules = json.loads(r.read()).get("pre", {}).get("rules", [])
        for rule in rules:
            st["policy"].append({
                "toolkit": rule.get("toolkit"),
                "tool": rule.get("tool"),
                "action": rule.get("action"),
                "override": (rule.get("override") or {}).get("inputs"),
            })
        sandbox = next((r for r in rules if r.get("tool") == "CreateSandbox"), None)
        pr = next((r for r in rules if r.get("tool") == "CreatePullRequest"), None)
        st["armed"] = bool(
            sandbox and sandbox.get("action") == "block"
            and pr and ((pr.get("override") or {}).get("inputs") or {}).get("draft") is True
        )
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        pass

    try:
        st["run_sh"] = subprocess.run(
            ["pgrep", "-f", "run.sh"], capture_output=True, timeout=5
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        pass

    # Boot progress: tail the run log so "start" isn't a leap of faith. The
    # poller printing its waiting banner is the "you can send now" signal.
    st["ready"] = False
    st["boot"] = []
    try:
        lines = RUN_LOG.read_text().splitlines()
        tail = [l.strip() for l in lines if l.strip()][-30:]
        st["ready"] = st["run_sh"] and any(
            "Waiting for" in l and "email" in l for l in tail
        )
        st["boot"] = [l for l in tail if l.startswith("[demo]")][-4:]
    except OSError:
        pass

    # The Arcade CLI token expiring mid-workshop is the classic way this dies.
    try:
        creds = (pathlib.Path.home() / ".arcade/credentials.yaml").read_text()
        m = re.search(r"expires_at:\s*'?([0-9T:.\-]+)", creds)
        if m:
            import datetime
            raw = m.group(1)
            # microseconds can come back at odd widths; normalise to 6 digits
            raw = re.sub(r"\.(\d{1,6})\d*$", lambda x: "." + x.group(1).ljust(6, "0"), raw)
            exp = datetime.datetime.fromisoformat(raw)
            st["token_min"] = int((exp - datetime.datetime.now()).total_seconds() // 60)
    except (OSError, ValueError):
        pass

    return st


_log_off = [0]


def ingest_runlog():
    """[demo] lines from run.sh, as feed rows. The boot sequence, the email
    pickup, and the HITL banners are the story's narration; they belong on
    the timeline, not in a strip that disappears."""
    import datetime
    try:
        size = RUN_LOG.stat().st_size
    except OSError:
        return
    if size < _log_off[0]:
        _log_off[0] = 0          # run.sh restarted and truncated the log
    if size == _log_off[0]:
        return
    with open(RUN_LOG, "rb") as f:
        f.seek(_log_off[0])
        chunk = f.read()
    # only consume complete lines; partial writes wait for the next poll
    cut = chunk.rfind(b"\n")
    if cut < 0:
        return
    _log_off[0] += cut + 1
    now = datetime.datetime.now().astimezone().isoformat()
    with _lock:
        for i, raw in enumerate(chunk[:cut].split(b"\n")):
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("[demo]"):
                continue
            msg = line[len("[demo]"):].strip()
            e = {
                "timestamp": now,
                "endpoint": "/runlog",
                "body": {
                    "execution_id": f"log-{_log_off[0]}-{i}",
                    "tool": {"toolkit": "demo", "name": msg},
                    "inputs": {},
                    "context": {"user_id": "run.sh"},
                },
                "response": {"code": "LOG"},
                "rule_match": "",
            }
            _seen.add(_key(e))
            _ledger.append(e)
        save_ledger()


def poll(cate_port, stop):
    """Server-side poller. Runs regardless of whether a browser is open, which is
    the point - the wipe happens whether or not anyone is watching."""
    while not stop.is_set():
        try:
            with urllib.request.urlopen(
                f"http://localhost:{cate_port}/_logs", timeout=3
            ) as r:
                entries = json.loads(r.read()).get("logs", [])
            with _lock:
                added = False
                for e in entries:
                    # /health pings carry a null body and would render as "?"
                    # rows. They're liveness checks, not governance decisions.
                    if e.get("endpoint") == "/health" or not (e.get("body") or {}).get("tool"):
                        continue
                    k = _key(e)
                    if k not in _seen:
                        _seen.add(k)
                        _ledger.append(e)
                        added = True
                if added:
                    _ledger.sort(key=lambda x: x.get("timestamp", ""))
                    save_ledger()
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            pass  # CATE restarting; the ledger is what persists
        ingest_runlog()
        stop.wait(0.75)


PAGE = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>Agent governance - live</title>
<style>
  :root{
    --bg:#07090d; --panel:#0d1117; --fg:#e9eff7; --dim:#7d8da0; --line:#1a2330;
    --ok:#3fb950; --block:#ff5f56; --stamp:#e3b341; --accent:#58a6ff;
  }
  *{box-sizing:border-box}
  html,body{margin:0;height:100%}
  body{background:
        radial-gradient(1100px 520px at 12% -8%, #12213a 0%, transparent 60%),
        radial-gradient(900px 480px at 105% 0%, #1c1430 0%, transparent 55%),
        var(--bg);
       color:var(--fg);
       font:15px/1.55 ui-monospace,"SF Mono",SFMono-Regular,Menlo,monospace;
       -webkit-font-smoothing:antialiased}
  header{padding:22px 30px 18px;border-bottom:1px solid var(--line);
         backdrop-filter:blur(8px);position:sticky;top:0;z-index:5;
         background:rgba(7,9,13,.86)}
  .top{display:flex;align-items:center;gap:22px;flex-wrap:wrap}
  h1{font-size:20px;margin:0;font-weight:600;letter-spacing:-.02em;
     display:flex;align-items:center;gap:11px}
  .dot{width:9px;height:9px;border-radius:50%;background:var(--ok);
       box-shadow:0 0 0 0 rgba(63,185,80,.7);animation:pulse 2.2s infinite}
  @keyframes pulse{0%{box-shadow:0 0 0 0 rgba(63,185,80,.6)}
                   70%{box-shadow:0 0 0 11px rgba(63,185,80,0)}
                   100%{box-shadow:0 0 0 0 rgba(63,185,80,0)}}
  .dot.stale{background:var(--dim);animation:none;box-shadow:none}
  .who{color:var(--dim);font-size:14px}
  .who b{color:var(--fg);font-weight:600}
  .counts{margin-left:auto;display:flex;gap:9px}
  .chip{padding:7px 15px;border:1px solid var(--line);border-radius:9px;
        background:var(--panel);cursor:pointer;font:inherit;color:var(--dim);
        font-size:13px;transition:.16s;white-space:nowrap}
  .chip:hover{border-color:#2b3a4d;transform:translateY(-1px)}
  .chip b{font-size:19px;font-variant-numeric:tabular-nums;margin-right:7px}
  .chip.on{border-color:currentColor}
  .chip.all{color:var(--accent)} .chip.a b{color:var(--ok)}
  .chip.b b{color:var(--block)} .chip.s b{color:var(--stamp)}
  .sub{margin-top:13px;display:flex;gap:9px;align-items:center;flex-wrap:wrap}
  .sub label{color:var(--dim);font-size:13px;display:flex;gap:7px;align-items:center;cursor:pointer}
  .cue{margin-top:14px;display:flex;gap:12px;align-items:center;padding:12px 16px;
       border:1px solid #2b4a2e;border-radius:10px;background:#0f1a12;font-size:17px}
  .cue.wait{border-color:#2a3646;background:#0d141d;color:var(--dim)}
  .cue-k{font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--ok);
         border:1px solid currentColor;border-radius:4px;padding:2px 7px}
  .cue.wait .cue-k{color:var(--dim)}
  .cue b{color:#7ee787}
  .op.cued{border-color:var(--ok);color:#7ee787;
           animation:cuepulse 1.6s infinite}
  @keyframes cuepulse{0%,100%{box-shadow:0 0 0 0 rgba(63,185,80,.5)}
                      60%{box-shadow:0 0 0 8px rgba(63,185,80,0)}}
  #deckwrap{display:none;position:fixed;inset:0;z-index:50;overflow:auto;
      background:
        radial-gradient(1400px 700px at 8% -12%, #14243d 0%, transparent 58%),
        radial-gradient(1000px 560px at 108% -4%, #221636 0%, transparent 55%),
        radial-gradient(900px 900px at 50% 118%, #0d1b16 0%, transparent 50%),
        #06080c}
  #deckwrap.on{display:flex;align-items:center;justify-content:center;
      padding:5vh 7vw 9vh}
  #deck{width:100%;max-width:1280px;animation:sIn .45s cubic-bezier(.2,.8,.2,1)}
  @keyframes sIn{from{opacity:0;transform:translateY(14px) scale(.985)}
                 to{opacity:1;transform:none}}
  .stopline{display:flex;align-items:center;gap:14px;margin-bottom:4.5vh}
  .skick{font-size:clamp(13px,1.1vw,17px);letter-spacing:.28em;text-transform:uppercase;
         color:var(--accent);font-weight:700}
  .skick::before{content:"";display:inline-block;width:34px;height:2px;
         background:var(--accent);vertical-align:middle;margin-right:14px}
  .sact{margin-left:auto;font-size:12px;letter-spacing:.12em;color:var(--dim);
        border:1px solid var(--line);border-radius:999px;padding:5px 14px;
        text-transform:uppercase}
  #deck h2{margin:0 0 3.5vh;font-weight:750;letter-spacing:-.035em;
           font-family:system-ui,-apple-system,"SF Pro Display","Inter",sans-serif;
           color:var(--fg)}
  #deck.t-statement h2{font-size:clamp(44px,6.4vw,96px);line-height:1.04}
  #deck.t-cards h2, #deck.t-code h2{font-size:clamp(34px,4.4vw,64px);line-height:1.08}
  .sbody{font-size:clamp(19px,2vw,30px);line-height:1.45;color:#9fb0c3;
         max-width:36ch;margin:0 0 3vh;
         font-family:system-ui,-apple-system,"Inter",sans-serif}
  #deck.t-statement .sbody{font-size:clamp(22px,2.4vw,36px);color:#b9c8da}
  .term{border:1px solid #202b3a;border-radius:14px;overflow:hidden;margin:0 0 3vh;
        box-shadow:0 24px 60px -24px rgba(0,0,0,.7)}
  .termbar{display:flex;align-items:center;gap:8px;padding:12px 16px;
        background:#10161f;border-bottom:1px solid #1a2330}
  .tdot{width:11px;height:11px;border-radius:50%}
  .termbar span{margin-left:10px;color:#5b6b7e;font-size:12px;letter-spacing:.08em}
  .scode{font-size:clamp(16px,1.6vw,24px);line-height:1.65;padding:22px 26px;margin:0;
        color:#7ee787;background:#0a0f16;white-space:pre-wrap}
  .sitems{display:grid;gap:16px;margin-top:1vh;
        grid-template-columns:repeat(auto-fit,minmax(260px,1fr))}
  .sitem{background:linear-gradient(180deg,#0e141d 0%,#0b1018 100%);
        border:1px solid #1c2635;border-radius:16px;padding:24px 24px 22px;
        border-top:3px solid var(--ac,#58a6ff)}
  .sk{font-size:clamp(15px,1.4vw,21px);font-weight:750;color:var(--ac,#58a6ff);
        letter-spacing:.01em;margin-bottom:10px;text-transform:lowercase}
  .sv{font-size:clamp(15px,1.5vw,22px);line-height:1.45;color:#b3c2d3;
        font-family:system-ui,-apple-system,"Inter",sans-serif}
  .sfoot{display:flex;align-items:center;gap:18px;margin-top:5vh}
  .dots{display:flex;gap:7px}
  .dot2{width:7px;height:7px;border-radius:50%;background:#26313f;cursor:pointer;
        transition:.2s}
  .dot2.here{background:var(--accent);transform:scale(1.35)}
  .snum{color:#3d4a59;font-size:13px;font-variant-numeric:tabular-nums;margin-left:auto}
  .deckhint{position:fixed;bottom:18px;left:0;right:0;text-align:center;
            color:var(--dim);font-size:13px;opacity:.7}
  .op.deck{margin-left:8px}
  .links{margin-top:12px;display:flex;gap:16px;align-items:center;flex-wrap:wrap;
         font-size:13px;color:var(--dim)}
  .links a{color:var(--accent);text-decoration:none;border-bottom:1px solid transparent}
  .links a:hover{border-bottom-color:var(--accent)}
  .ops{margin-top:14px;display:flex;gap:9px;align-items:center;flex-wrap:wrap}
  .op{font:inherit;font-size:14px;font-weight:600;padding:9px 17px;border-radius:9px;
      cursor:pointer;border:1px solid;background:var(--panel);transition:.16s}
  .op:hover{transform:translateY(-1px)}
  .op:disabled{opacity:.45;cursor:wait;transform:none}
  .op.go{color:var(--accent);border-color:#1f4b7a}
  .op.approve{color:var(--ok);border-color:#1f5c2c}
  .op.restore{color:var(--block);border-color:#6e2b28}
  .op.reset{color:var(--dim);border-color:var(--line)}
  .op.start{color:var(--fg);border-color:#3a4d63;background:#13202f}
  .op.stop{color:#c98a86;border-color:#553431}
  .state{margin-left:auto;display:flex;gap:16px;font-size:13px;color:var(--dim);
         align-items:center;flex-wrap:wrap}
  .pill{padding:4px 10px;border-radius:999px;border:1px solid var(--line)}
  .pill.armed{color:var(--ok);border-color:#1f5c2c}
  .pill.dis{color:var(--block);border-color:#6e2b28}
  .pill.warn{color:var(--stamp);border-color:#6b5518}
  .out{margin-top:11px;font-size:13px;color:var(--dim);white-space:pre-wrap;
       max-height:0;overflow:hidden;transition:max-height .25s}
  .out.show{max-height:150px;overflow:auto}
  .cols2{display:grid;grid-template-columns:1fr 340px;align-items:start}
  main{padding:0 0 60px;min-width:0}
  #oplog{position:sticky;top:150px;max-height:calc(100vh - 170px);overflow-y:auto;
      border-left:1px solid var(--line);padding:14px 16px 30px;background:#0a0e14}
  .oph2{font-size:11px;letter-spacing:.22em;text-transform:uppercase;color:var(--dim);
      margin-bottom:12px}
  .opempty{color:#4a5867;font-size:13px;line-height:1.5}
  .ople{margin-bottom:14px;border:1px solid var(--line);border-radius:10px;overflow:hidden}
  .ople .ophead{display:flex;gap:8px;align-items:baseline;padding:8px 12px;
      background:#10161f;font-size:12px}
  .ople .ophead b{color:var(--accent);font-weight:600}
  .ople .ophead span{color:#4a5867;margin-left:auto;font-variant-numeric:tabular-nums}
  .ople pre{margin:0;padding:10px 12px;font-size:12px;line-height:1.55;color:#8da0b5;
      white-space:pre-wrap;word-break:break-word;max-height:180px;overflow-y:auto}
  .ople.err .ophead b{color:var(--block)}
  @media (max-width:1100px){.cols2{grid-template-columns:1fr}#oplog{display:none}}
  .row{display:grid;grid-template-columns:88px 128px 1fr 118px 22px;
       gap:15px;align-items:center;padding:13px 30px;
       border-bottom:1px solid var(--line);cursor:pointer;transition:background .13s}
  .row:hover{background:#0e1520}
  .row.new{animation:slide .55s cubic-bezier(.2,.8,.2,1)}
  @keyframes slide{from{background:#16283d;opacity:0;transform:translateY(-7px)}
                   to{background:transparent;opacity:1;transform:none}}
  .row.is-BLOCKED{border-left:3px solid var(--block);padding-left:27px}
  .row.is-STAMPED{border-left:3px solid var(--stamp);padding-left:27px}
  .ts{color:var(--dim);font-size:13px;font-variant-numeric:tabular-nums}
  .kit{color:var(--dim);font-size:14px}
  .tool{font-weight:600;font-size:18px;letter-spacing:-.01em}
  .badge{text-align:center;padding:5px 0;border-radius:6px;font-size:12px;
         font-weight:700;letter-spacing:.09em}
  .ALLOWED{color:var(--ok);background:#3fb95015}
  .BLOCKED{color:var(--block);background:#ff5f5622}
  .STAMPED{color:var(--stamp);background:#e3b34120}
  .HUMAN{color:var(--accent);background:#58a6ff1f}
  .LOG{color:#5b6b7e;background:transparent;font-weight:500}
  .row.is-LOG{padding-top:7px;padding-bottom:7px}
  .row.is-LOG .tool{font-size:14px;font-weight:400;color:#7d8da0;font-style:italic}
  .row.is-LOG .kit{color:#4a5867}
  .scue{display:flex;gap:12px;align-items:baseline;margin:0 0 3vh;padding:14px 18px;
        border:1px dashed #2f5f9e;border-radius:12px;background:#0d1726;
        color:#79b8ff;font-size:clamp(13px,1.25vw,19px)}
  .scue b{font-size:11px;letter-spacing:.22em;color:#4a90e2;border:1px solid #2f5f9e;
        border-radius:5px;padding:2px 8px;flex:none}
  .row.is-HUMAN{border-left:3px solid var(--accent);padding-left:27px}
  .caret{color:var(--dim);font-size:12px;transition:transform .18s}
  .row.open .caret{transform:rotate(90deg)}
  .why{padding:0 30px 13px 30px;color:var(--dim);font-size:14px;
       border-bottom:1px solid var(--line)}
  .why.BLOCKED{color:#ffa9a3} .why.STAMPED{color:#ecc95f}
  .detail{display:none;padding:4px 30px 22px;border-bottom:1px solid var(--line);
          background:#0a0e14}
  .detail.open{display:block}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));
        gap:11px;margin-bottom:15px}
  .cell{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:11px 13px}
  .cell .k{color:var(--dim);font-size:11px;letter-spacing:.08em;text-transform:uppercase}
  .cell .v{margin-top:4px;font-size:14px;word-break:break-all}
  pre{margin:0;background:var(--panel);border:1px solid var(--line);
      border-radius:8px;padding:13px;overflow-x:auto;font-size:13px;color:#b9c8da}
  .lbl{color:var(--dim);font-size:11px;letter-spacing:.08em;
       text-transform:uppercase;margin:13px 0 6px}
  .empty{padding:70px 30px;color:var(--dim);font-size:17px;text-align:center}
  .hidden{display:none !important}
</style></head><body>
<header>
  <div class="top">
    <h1><span class="dot" id="dot"></span>Agent governance</h1>
    <div class="who">acting as <b id="who">-</b></div>
    <div class="counts">
      <button class="chip all on" data-f="ALL"><b id="n-all">0</b>calls</button>
      <button class="chip a" data-f="ALLOWED"><b id="n-ok">0</b>allowed</button>
      <button class="chip b" data-f="BLOCKED"><b id="n-block">0</b>blocked</button>
      <button class="chip s" data-f="STAMPED"><b id="n-stamp">0</b>stamped</button>
    </div>
  </div>
  <div class="sub">
    <label><input type="checkbox" id="hidePoll" checked> hide inbox polling</label>
    <label><input type="checkbox" id="demoOnly" checked> demo toolkits only</label>
    <label><input type="checkbox" id="newest" checked> newest first</label>
    <span class="who" style="margin-left:auto">click any row for the full request</span>
  </div>
  <div class="cue" id="cue"><span class="cue-k">next</span><span id="cue-t">…</span></div>
  <div class="ops">
    <button class="op start"   data-a="start">start</button>
    <button class="op go"      data-a="trigger">send trigger email</button>
    <button class="op approve" data-a="approve">approve sandbox</button>
    <button class="op restore" data-a="restore">re-block (Act 4)</button>
    <button class="op stop"    data-a="stop">stop</button>
    <button class="op reset"   data-a="reset">reset feed</button>
    <button class="op deck"    id="present">present ▸</button>
    <div class="state" id="state"></div>
  </div>
  <div class="out" id="out"></div>
  <div class="links">
    <span>open in a tab:</span>
    <a href="https://github.com/arcadeai-labs/daytona-background-agents" target="_blank" rel="noopener">repo</a>
    <a href="https://github.com/arcadeai-labs/daytona-background-agents/tree/main/examples" target="_blank" rel="noopener">loop + graph examples</a>
    <a href="https://github.com/arcadeai-labs/daytona-background-agents/pulls" target="_blank" rel="noopener">pull requests</a>
    <a href="https://linear.app/arcadedev/team/DEMO/active" target="_blank" rel="noopener">Linear board</a>
    <a href="https://www.arcade.dev/blog/arcade-background-agents" target="_blank" rel="noopener">the why (blog)</a>
  </div>
</header>
<div id="deckwrap"><div id="deck"></div>
  <div class="deckhint">← → or space to move · Esc back to the live feed</div></div>
<div class="cols2">
<main id="feed"><div class="empty">Waiting for the agent's first tool call…</div></main>
<aside id="oplog">
  <div class="oph2">operator log</div>
  <div id="oplogbody"><div class="opempty">your button presses and their output land here</div></div>
</aside>
</div>
<script>
const DEMO_KITS = ['Daytona','Github','GitHub','Linear','Slack','Gmail'];
// run.sh polls the inbox through the gateway every 15s, so each poll is a real
// governed call. True, and useless on a projector - it buries everything else.
const POLLER_TOOLS = ['SearchThreads','GetThread'];
let events = [], filter = 'ALL';
const openKeys = new Set();
function ekey(e){ return [(e.timestamp||''),(e.body?.execution_id||''),(e.body?.tool?.name||'')].join('|'); }

function classify(r){
  if(!r) return 'ALLOWED';
  if(r.code === 'LOG') return 'LOG';
  if(r.code === 'HUMAN') return 'HUMAN';
  if(r.code === 'CHECK_FAILED') return 'BLOCKED';
  if(r.override) return 'STAMPED';
  return 'ALLOWED';
}
function esc(s){ return String(s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }

function render(){
  const demoOnly = document.getElementById('demoOnly').checked;
  const hidePoll = document.getElementById('hidePoll').checked;
  const newest = document.getElementById('newest').checked;
  const keep = e => {
    const kit = e.body?.tool?.toolkit || '', name = e.body?.tool?.name || '';
    if (kit === 'You' || kit === 'demo') return true;   // operator + narrator rows always visible
    if (hidePoll && kit === 'Gmail' && POLLER_TOOLS.includes(name)) return false;
    if (demoOnly && !DEMO_KITS.includes(kit)) return false;
    return true;
  };
  let list = events.filter(e =>
    keep(e) && (filter === 'ALL' || classify(e.response) === filter));

  const counts = {ALLOWED:0, BLOCKED:0, STAMPED:0, HUMAN:0, LOG:0};
  events.filter(keep).forEach(e => counts[classify(e.response)]++);
  document.getElementById('n-all').textContent = counts.ALLOWED+counts.BLOCKED+counts.STAMPED;
  document.getElementById('n-ok').textContent = counts.ALLOWED;
  document.getElementById('n-block').textContent = counts.BLOCKED;
  document.getElementById('n-stamp').textContent = counts.STAMPED;

  const feed = document.getElementById('feed');
  if (!list.length){ feed.innerHTML = '<div class="empty">Nothing matches this filter.</div>'; return; }
  if (newest) list = list.slice().reverse();

  feed.innerHTML = list.map((e,i) => {
    const t = e.body?.tool || {}, r = e.response || {}, st = classify(r);
    const why = st==='BLOCKED' ? (r.error_message || 'blocked by policy')
              : st==='STAMPED' ? ('policy rewrote inputs → ' + JSON.stringify(r.override.inputs))
              : '';
    const ctx = e.body?.context || {};
    return `
    <div class="row is-${st}" data-k="${esc(ekey(e))}">
      <div class="ts">${esc((e.timestamp||'').slice(11,19))}</div>
      <div class="kit">${esc(t.toolkit||'?')}</div>
      <div class="tool">${esc(t.name||'?')}</div>
      <div class="badge ${st}">${st}</div>
      <div class="caret">▸</div>
    </div>
    ${why ? `<div class="why ${st}">└─ ${esc(why)}</div>` : ''}
    <div class="detail" data-k="${esc(ekey(e))}">
      <div class="grid">
        <div class="cell"><div class="k">acting as</div><div class="v">${esc(ctx.user_id||'-')}</div></div>
        <div class="cell"><div class="k">hook</div><div class="v">${esc(e.endpoint||'-')}</div></div>
        <div class="cell"><div class="k">rule matched</div><div class="v">${esc(e.rule_match||'none')}</div></div>
        <div class="cell"><div class="k">execution id</div><div class="v">${esc(e.body?.execution_id||'-')}</div></div>
        ${ctx.secrets?.length ? `<div class="cell"><div class="k">secrets used</div><div class="v">${esc(ctx.secrets.join(', '))}</div></div>`:''}
      </div>
      <div class="lbl">inputs the agent sent</div>
      <pre>${esc(JSON.stringify(e.body?.inputs ?? {}, null, 2))}</pre>
      <div class="lbl">what policy answered</div>
      <pre>${esc(JSON.stringify(r, null, 2))}</pre>
    </div>`;
  }).join('');

  // Re-apply anything the operator had expanded. The feed re-renders whenever a
  // new event lands, and slamming a detail pane shut mid-sentence is the fastest
  // way to lose your place in front of a room.
  feed.querySelectorAll('.row').forEach(row => {
    const k = row.dataset.k;
    if (openKeys.has(k)) {
      row.classList.add('open');
      feed.querySelector(`.detail[data-k="${k}"]`)?.classList.add('open');
    }
    row.addEventListener('click', () => {
      const on = row.classList.toggle('open');
      on ? openKeys.add(k) : openKeys.delete(k);
      feed.querySelector(`.detail[data-k="${k}"]`)?.classList.toggle('open', on);
    });
  });

  const last = [...events].reverse().find(e => e.body?.context?.user_id);
  if (last) document.getElementById('who').textContent = last.body.context.user_id;
}

let lastCount = -1, quiet = 0;
async function tick(){
  try{
    const res = await fetch('/events');
    const next = (await res.json()).events || [];
    if (next.length !== events.length){ events = next; render(); quiet = 0; }
    else { quiet++; }
    document.getElementById('dot').classList.toggle('stale', quiet > 40);
  }catch(e){ document.getElementById('dot').classList.add('stale'); }
}
['demoOnly','newest','hidePoll'].forEach(id =>
  document.getElementById(id).addEventListener('change', render));

// ── stage controls ────────────────────────────────────────────────
const out = document.getElementById('out');
document.querySelectorAll('.op').forEach(btn => btn.addEventListener('click', async () => {
  const all = [...document.querySelectorAll('.op')];
  all.forEach(b => b.disabled = true);
  const started = new Date().toTimeString().slice(0,8);
  let j = {ok:false, output:'request failed'};
  try{
    const r = await fetch('/act/' + btn.dataset.a, {method:'POST'});
    j = await r.json();
  }catch(e){ j = {ok:false, output:String(e)}; }
  const body = document.getElementById('oplogbody');
  body.querySelector('.opempty')?.remove();
  const entry = document.createElement('div');
  entry.className = 'ople' + (j.ok ? '' : ' err');
  entry.innerHTML = `<div class="ophead"><b>${esc(btn.textContent.trim())}</b><span>${started}</span></div>
    <pre>${esc(j.output)}</pre>`;
  body.prepend(entry);
  all.forEach(b => b.disabled = false);
  refreshState();
}));

async function refreshState(){
  try{
    const s = await (await fetch('/state')).json();
    const bits = [];
    bits.push(s.armed
      ? '<span class="pill armed">policy armed</span>'
      : '<span class="pill dis">policy DISARMED</span>');
    // three states, not two: down / arming / READY - "ready" is the poller's
    // own waiting banner, i.e. the moment "send trigger email" will land
    bits.push(!s.run_sh
      ? '<span class="pill dis">stopped - press start</span>'
      : s.ready
        ? '<span class="pill armed">READY - send the email</span>'
        : '<span class="pill warn">arming… (~30s)</span>');
    // while arming, stream the boot log into the output strip
    if (s.run_sh && !s.ready && (s.boot||[]).length){
      out.textContent = s.boot.join('\n');
      out.classList.add('show');
    }
    if (s.token_min !== null && s.token_min !== undefined){
      const cls = s.token_min < 0 ? 'dis' : (s.token_min < 45 ? 'warn' : 'armed');
      const txt = s.token_min < 0 ? 'token EXPIRED' : 'token ' + s.token_min + 'm';
      bits.push('<span class="pill '+cls+'">'+txt+'</span>');
    }
    for (const r of s.policy){
      const on = r.action === 'block' ? 'dis' : (r.override ? 'warn' : '');
      bits.push('<span class="pill '+on+'">'+r.tool+': '+r.action
        + (r.override ? ' +draft' : '') + '</span>');
    }
    document.getElementById('state').innerHTML = bits.join('');
  }catch(e){}
}
refreshState(); setInterval(refreshState, 4000);
document.querySelectorAll('.chip').forEach(c => c.addEventListener('click', () => {
  document.querySelectorAll('.chip').forEach(x => x.classList.remove('on'));
  c.classList.add('on'); filter = c.dataset.f; render();
}));

// ── stage cue: what to press next, derived from live state ─────────
function cueFor(state, ev){
  const named = n => ev.filter(e => e.body?.tool?.name === n);
  if (!state.run_sh)
    return {t:'run.sh is not running. Press <b>start demo</b>, then wait ~30s for it to arm.', a:'start'};
  if (!state.armed)
    return {t:'Arming… policy not fully in place yet. Wait for all three rules.', a:null};
  const sandbox = named('CreateSandbox');
  const lastSandbox = sandbox[sandbox.length-1];
  if (lastSandbox && classify(lastSandbox.response) === 'BLOCKED')
    return {t:'The agent is blocked and waiting. Let the room read it, then press <b>approve sandbox</b>.', a:'approve'};
  const stamped = ev.some(e => e.body?.tool?.name==='CreatePullRequest' && e.response?.override);
  if (stamped)
    return {t:'PR was stamped <b>draft: true</b>. For Act 4, press <b>re-block</b> to change policy live.', a:'restore'};
  const started = named('CreateIssue').length || named('ListTeams').length;
  if (!started)
    return {t:'Armed and polling. Press <b>send trigger email</b> when you are ready.', a:'trigger'};
  return {t:'Agent is working - narrate the feed. Nothing to press.', a:null};
}
function paintCue(state){
  const c = cueFor(state, events);
  const box = document.getElementById('cue');
  document.getElementById('cue-t').innerHTML = c.t;
  box.classList.toggle('wait', !c.a);
  document.querySelectorAll('.op').forEach(b =>
    b.classList.toggle('cued', !!c.a && b.dataset.a === c.a));
}

// ── the deck, on the same page ─────────────────────────────────────
let slides = [], deckIdx = 0;
fetch('/slides').then(r=>r.json()).then(d=>{
  slides = d.slides || [];
  // #present opens the deck; #present-6 opens it on slide 6
  const m = location.hash.match(/^#present(?:-(\d+))?$/);
  if (m){ if (m[1]) deckIdx = Math.min(slides.length-1, Math.max(0, +m[1]-1)); showDeck(true); }
});
function showDeck(on){
  document.getElementById('deckwrap').classList.toggle('on', on);
  if (on) paintSlide();
}
function paintSlide(){
  const s = slides[deckIdx]; if (!s) return;
  const ACC = ['#58a6ff','#3fb950','#e3b341','#ff5f56'];
  const type = (s.items&&s.items.length) ? 't-cards' : (s.code ? 't-code' : 't-statement');
  const items = (s.items||[]).map(([k,v],i) =>
    `<div class="sitem" style="--ac:${ACC[i%4]}"><div class="sk">${esc(k)}</div><div class="sv">${esc(v)}</div></div>`).join('');
  const dots = slides.map((_,i) =>
    `<span class="dot2 ${i===deckIdx?'here':''}" data-i="${i}"></span>`).join('');
  const deck = document.getElementById('deck');
  deck.className = type;
  deck.innerHTML = `
    <div class="stopline">
      ${s.kicker?`<div class="skick">${esc(s.kicker)}</div>`:''}
      <div class="sact">${esc(s.act||'')}</div>
    </div>
    <h2>${esc(s.title||'').replace(/\n/g,'<br>')}</h2>
    ${s.cue?`<div class="scue"><b>STAGE</b><span>${esc(s.cue)}</span></div>`:''}
    ${s.code?`<div class="term"><div class="termbar">
        <div class="tdot" style="background:#ff5f56"></div>
        <div class="tdot" style="background:#e3b341"></div>
        <div class="tdot" style="background:#3fb950"></div>
        <span>arcade gateway</span></div>
      <pre class="scode">${esc(s.code)}</pre></div>`:''}
    ${s.body?`<p class="sbody">${esc(s.body)}</p>`:''}
    ${items?`<div class="sitems">${items}</div>`:''}
    <div class="sfoot"><div class="dots">${dots}</div>
      <div class="snum">${deckIdx+1} / ${slides.length}</div></div>`;
  deck.style.animation = 'none'; void deck.offsetWidth; deck.style.animation = '';
  deck.querySelectorAll('.dot2').forEach(d => d.addEventListener('click', ev => {
    ev.stopPropagation(); deckIdx = +d.dataset.i; paintSlide();
  }));
}
function move(d){ deckIdx = Math.max(0, Math.min(slides.length-1, deckIdx+d)); paintSlide(); }
document.getElementById('present').addEventListener('click', () => showDeck(true));
document.addEventListener('keydown', e => {
  const on = document.getElementById('deckwrap').classList.contains('on');
  if (e.key === 'Escape' && on) return showDeck(false);
  if (!on) return;
  if (e.key === 'ArrowRight' || e.key === ' ') { e.preventDefault(); move(1); }
  if (e.key === 'ArrowLeft') move(-1);
});

tick(); setInterval(tick, 900);
</script></body></html>
"""


OPERATOR_LABELS = {
    "trigger": "sent the trigger email",
    "approve": "approved the sandbox",
    "restore": "re-blocked sandbox creation",
    "start": "started the demo",
    "stop": "stopped the demo",
}


def log_operator(name):
    """The human's click is part of the story - put it on the timeline where
    it happened, between the deny and the retry."""
    import datetime
    label = OPERATOR_LABELS.get(name)
    if not label:
        return
    e = {
        "timestamp": datetime.datetime.now().astimezone().isoformat(),
        "endpoint": "/operator",
        "body": {
            "execution_id": f"op-{time.time():.0f}",
            "tool": {"toolkit": "You", "name": label},
            "inputs": {},
            "context": {"user_id": "the human at the keyboard"},
        },
        "response": {"code": "HUMAN"},
        "rule_match": "human-in-the-loop",
    }
    with _lock:
        _seen.add(_key(e))
        _ledger.append(e)
        save_ledger()


class Handler(BaseHTTPRequestHandler):
    cate_port = 8888

    def _send(self, body, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            self._send(PAGE.encode(), "text/html; charset=utf-8")
        elif self.path == "/events":
            with _lock:
                body = json.dumps({"count": len(_ledger), "events": _ledger}).encode()
            self._send(body, "application/json")
        elif self.path == "/slides":
            self._send(json.dumps({"slides": SLIDES}).encode(), "application/json")
        elif self.path == "/state":
            self._send(json.dumps(demo_state(self.cate_port)).encode(), "application/json")
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path.startswith("/act/") and self.path[len("/act/"):] in ("start", "stop", "reset"):
            name = self.path[len("/act/"):]
            ok, output = run_action(name, self.cate_port)
            if ok:
                log_operator(name)
            self._send(json.dumps({"ok": ok, "output": output}).encode(), "application/json")
            return
        if not self.path.startswith("/act/"):
            self.send_error(404)
            return
        name = self.path[len("/act/"):]
        if name not in ACTIONS:
            self._send(
                json.dumps({"ok": False, "output": f"unknown action: {name}"}).encode(),
                "application/json",
            )
            return
        ok, output = run_action(name)
        if ok:
            log_operator(name)
        self._send(json.dumps({"ok": ok, "output": output}).encode(), "application/json")

    def log_message(self, *a):
        pass  # don't scribble over the demo terminal


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7777)
    ap.add_argument("--cate-port", type=int, default=8888)
    ap.add_argument("--reset", action="store_true", help="discard the saved ledger")
    args = ap.parse_args()

    if args.reset:
        LEDGER.unlink(missing_ok=True)
    load_ledger()
    Handler.cate_port = args.cate_port

    stop = threading.Event()
    threading.Thread(target=poll, args=(args.cate_port, stop), daemon=True).start()

    print(f"Governance dashboard:  http://localhost:{args.port}")
    print(f"Polling CATE on :{args.cate_port} · ledger has {len(_ledger)} events")
    print("Stage controls enabled (trigger / approve / re-block).")
    print("Ctrl+C to stop.")
    try:
        # 127.0.0.1, never 0.0.0.0 - this page can run commands, and conference
        # wifi is not a place to expose that.
        ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
    except KeyboardInterrupt:
        stop.set()
