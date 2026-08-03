#!/usr/bin/env python3
"""Loop engineering: the harness around a fix-verify cycle.

The model proposes; the loop decides whether to keep going. Everything
interesting here is the loop's job, not the model's:

  budget        — a hard cap on iterations, so a confused agent stops
  exit test     — an objective signal for done (tests green), not self-report
  progress      — the failure signature must change, or we're spinning
  guardrail     — some edits are refused no matter what the model proposes
  verification  — every patch is re-tested before it counts
  revert        — a failed patch is undone, so attempts don't stack

Runs against a scratch copy of buggy-api, so the planted bug survives.
No credentials, no network, no model — the "proposals" are canned so the
control flow is the only thing on screen.

    python3 examples/loop_engineering.py
    python3 examples/loop_engineering.py --stuck   # no-progress exit
"""

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MAX_ITERATIONS = 5

# Stand-ins for model output. Each is (file, find, replace, rationale).
PROPOSALS = [
    (
        "tests/test_handler.py",
        "assert page[0].id == 1",
        "assert page[0].id == 11",
        "loosen the assertion to match observed behavior",
    ),
    (
        "src/handler.py",
        "offset = page * limit",
        "offset = page",
        "offset should not scale with limit",
    ),
    (
        "src/handler.py",
        "offset = page * limit",
        "offset = (page - 1) * limit",
        "pages are 1-indexed, so page 1 must map to offset 0",
    ),
]

STUCK_PROPOSALS = [PROPOSALS[1]] * MAX_ITERATIONS


def require_pytest():
    """Fail with an instruction instead of a traceback. Attendees arrive with
    whatever python3 their laptop shipped with."""
    probe = subprocess.run(
        [sys.executable, "-c", "import pytest"], capture_output=True
    )
    if probe.returncode == 0:
        return
    print("This example needs pytest:\n")
    print(f"    {sys.executable} -m pip install pytest\n")
    print("Or use the venv from WORKSHOP.md's hands-on track:\n")
    print("    cd buggy-api && python3 -m venv .venv")
    print("    .venv/bin/pip install -r requirements.txt")
    print("    cd .. && buggy-api/.venv/bin/python examples/loop_engineering.py")
    sys.exit(2)


def run_tests(workdir):
    """Objective exit signal. Returns (passed, failure_signature, summary)."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=workdir,
        capture_output=True,
        text=True,
    )
    summary = ""
    for line in proc.stdout.splitlines():
        if "passed" in line or "failed" in line or "error" in line:
            summary = line.strip().lstrip("=").rstrip("=").strip()
    names = sorted(set(re.findall(r"^FAILED (\S+)", proc.stdout, re.MULTILINE)))
    signature = hashlib.sha1("|".join(names).encode()).hexdigest()[:8]
    return proc.returncode == 0, signature, (summary or "no test output"), names


def guardrail(path):
    """Refused regardless of what the model wants. Mirrors SKILL.md step 6:
    fix the code, not the test."""
    if "tests/" in path or Path(path).name.startswith("test_"):
        return "test files are off-limits — a passing suite you edited proves nothing"
    return None


def apply_patch(workdir, path, find, replace):
    target = workdir / path
    text = target.read_text()
    if find not in text:
        return False
    target.write_text(text.replace(find, replace, 1))
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stuck", action="store_true", help="only offer a patch that never works")
    args = ap.parse_args()
    proposals = STUCK_PROPOSALS if args.stuck else PROPOSALS

    require_pytest()
    tmp = Path(tempfile.mkdtemp(prefix="loop-eng-"))
    workdir = tmp / "buggy-api"
    shutil.copytree(REPO / "buggy-api", workdir, ignore=shutil.ignore_patterns(".pytest_cache", ".venv"))
    print(f"scratch copy: {workdir}\n")

    passed, signature, summary, names = run_tests(workdir)
    print(f"baseline           {summary}  [signature {signature}]")
    if passed:
        print("\nnothing to do — the bug isn't planted.")
        return 0

    seen = {signature}
    exit_reason = f"budget exhausted after {MAX_ITERATIONS} iterations"

    for i in range(1, MAX_ITERATIONS + 1):
        if i > len(proposals):
            exit_reason = "no proposals left"
            break
        path, find, replace, rationale = proposals[i - 1]
        print(f"\niteration {i}/{MAX_ITERATIONS}")
        print(f"  proposal         {path}: {find!r} -> {replace!r}")
        print(f"  rationale        {rationale}")

        refusal = guardrail(path)
        if refusal:
            # Costs an iteration on purpose: a refused proposal is still a turn
            # the agent spent, and pretending otherwise makes budgets meaningless.
            print(f"  guardrail        REFUSED — {refusal}")
            continue

        snapshot = (workdir / path).read_text()
        if not apply_patch(workdir, path, find, replace):
            print("  apply            no-op — target text not found, skipping")
            continue
        print("  apply            patched")

        passed, signature, summary, names = run_tests(workdir)
        print(f"  verify           {summary}  [signature {signature}]")

        if passed:
            exit_reason = f"exit test satisfied on iteration {i}"
            break

        # Undo before the next attempt. Stacking failed patches turns one bad
        # edit into a file nobody can reason about, including the agent.
        (workdir / path).write_text(snapshot)
        print("  revert           patch failed, file restored")

        if signature in seen:
            # Same tests failing the same way. More iterations of the same
            # observation will produce the same proposal; escalate instead.
            exit_reason = f"no progress — failure signature {signature} repeated, escalating to a human"
            break
        seen.add(signature)
        print(f"  progress         failure changed, {len(names)} still failing — continuing")

    print(f"\nstopped: {exit_reason}")
    print(f"result:  {'GREEN' if passed else 'RED'}")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
