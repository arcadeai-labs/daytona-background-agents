#!/usr/bin/env python3
"""Governance dashboard — the projector view of what the agent is doing.

The terminal is the wrong surface for a room: the interesting events scroll past
in 10pt monospace between pages of agent chatter. This serves one page, legible
from the back row, showing every tool call the agent makes and what policy
decided about it. Click any row for the full request.

Three states, which are the three governance moves in cate-config.yaml:

  ALLOWED   policy let it through
  BLOCKED   policy stopped it        (HITL checkpoint, push to main)
  STAMPED   policy rewrote the call  (draft: true on every PR)

Why it keeps its own ledger: run.sh:226 issues DELETE /_logs inside the HITL
approve path, so CATE's own log loses the denial seconds after it happens — the
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
# never anything the browser supplies — the page picks an action by name.
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


def run_action(name):
    """Run one of the fixed ACTIONS and return (ok, combined output)."""
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


def poll(cate_port, stop):
    """Server-side poller. Runs regardless of whether a browser is open, which is
    the point — the wipe happens whether or not anyone is watching."""
    while not stop.is_set():
        try:
            with urllib.request.urlopen(
                f"http://localhost:{cate_port}/_logs", timeout=3
            ) as r:
                entries = json.loads(r.read()).get("logs", [])
            with _lock:
                added = False
                for e in entries:
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
        stop.wait(0.75)


PAGE = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>Agent governance — live</title>
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
  main{padding:0 0 60px}
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
    <div class="who">acting as <b id="who">—</b></div>
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
</header>
<main id="feed"><div class="empty">Waiting for the agent's first tool call…</div></main>
<script>
const DEMO_KITS = ['Daytona','Github','GitHub','Linear','Slack','Gmail'];
// run.sh polls the inbox through the gateway every 15s, so each poll is a real
// governed call. True, and useless on a projector — it buries everything else.
const POLLER_TOOLS = ['SearchThreads','GetThread'];
let events = [], filter = 'ALL';

function classify(r){
  if(!r) return 'ALLOWED';
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
    if (hidePoll && kit === 'Gmail' && POLLER_TOOLS.includes(name)) return false;
    if (demoOnly && !DEMO_KITS.includes(kit)) return false;
    return true;
  };
  let list = events.filter(e =>
    keep(e) && (filter === 'ALL' || classify(e.response) === filter));

  const counts = {ALLOWED:0, BLOCKED:0, STAMPED:0};
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
    <div class="row is-${st}" data-i="${i}">
      <div class="ts">${esc((e.timestamp||'').slice(11,19))}</div>
      <div class="kit">${esc(t.toolkit||'?')}</div>
      <div class="tool">${esc(t.name||'?')}</div>
      <div class="badge ${st}">${st}</div>
      <div class="caret">▸</div>
    </div>
    ${why ? `<div class="why ${st}">└─ ${esc(why)}</div>` : ''}
    <div class="detail" data-d="${i}">
      <div class="grid">
        <div class="cell"><div class="k">acting as</div><div class="v">${esc(ctx.user_id||'—')}</div></div>
        <div class="cell"><div class="k">hook</div><div class="v">${esc(e.endpoint||'—')}</div></div>
        <div class="cell"><div class="k">rule matched</div><div class="v">${esc(e.rule_match||'none')}</div></div>
        <div class="cell"><div class="k">execution id</div><div class="v">${esc(e.body?.execution_id||'—')}</div></div>
        ${ctx.secrets?.length ? `<div class="cell"><div class="k">secrets used</div><div class="v">${esc(ctx.secrets.join(', '))}</div></div>`:''}
      </div>
      <div class="lbl">inputs the agent sent</div>
      <pre>${esc(JSON.stringify(e.body?.inputs ?? {}, null, 2))}</pre>
      <div class="lbl">what policy answered</div>
      <pre>${esc(JSON.stringify(r, null, 2))}</pre>
    </div>`;
  }).join('');

  feed.querySelectorAll('.row').forEach(row => row.addEventListener('click', () => {
    row.classList.toggle('open');
    feed.querySelector(`.detail[data-d="${row.dataset.i}"]`)?.classList.toggle('open');
  }));

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
document.querySelectorAll('.chip').forEach(c => c.addEventListener('click', () => {
  document.querySelectorAll('.chip').forEach(x => x.classList.remove('on'));
  c.classList.add('on'); filter = c.dataset.f; render();
}));
tick(); setInterval(tick, 900);
</script></body></html>
"""


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
        else:
            self.send_error(404)

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
    print("Ctrl+C to stop.")
    try:
        ThreadingHTTPServer(("", args.port), Handler).serve_forever()
    except KeyboardInterrupt:
        stop.set()
