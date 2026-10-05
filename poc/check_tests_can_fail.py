"""Switches off each protection in turn, in a scratch copy, and confirms the test suite then fails.

A suite that still passes with a protection removed isn't testing that protection. The repo itself
is never modified. Run from this folder:  python3 check_tests_can_fail.py"""

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

# (protection, file, code that enforces it, code that switches it off)
PROTECTIONS = [
    ("model can't add fields (e.g. an amount)", "helia_gateway/gateway.py", "if unexpected:", "if False:"),
    (
        "model's reason must be confirmed by billing",
        "helia_gateway/policy.py",
        "elif not f.reason_confirmed:",
        "elif False:",
    ),
    (
        "charge must belong to this customer",
        "helia_gateway/handlers.py",
        "if charge is None or charge.customer_id != ticket.customer_id:",
        "if charge is None:",
    ),
    (
        "ceiling beats the auto rule at runtime",
        "helia_gateway/policy.py",
        "if facts.amount > ceiling.amount_above:",
        "if False:",
    ),
    (
        "auto limit above a ceiling won't load",
        "helia_gateway/registry.py",
        "if limit is not None and limit > ceiling.amount_above:",
        "if False:",
    ),
    (
        "hourly auto-refund cap",
        "helia_gateway/policy.py",
        'f.auto_total_last_hour + f.amount > rule["hourly_cap"]',
        "False",
    ),
    (
        "unknown outcomes count against the cap",
        "helia_gateway/gateway.py",
        'self._auto_ledger.append((self.clock.now, action.tool, action.computed["amount"]))',
        "pass",
    ),
    (
        "only a person can approve",
        "helia_gateway/gateway.py",
        'if caller.kind != "human":\n            return self._deny(action, caller, "only a person',
        'if False:\n            return self._deny(action, caller, "only a person',
    ),
    (
        "one person can't fill two roles",
        "helia_gateway/gateway.py",
        "if caller.id in action.approvals.values():",
        "if False:",
    ),
    (
        "approval bound to the exact action",
        "helia_gateway/gateway.py",
        "if fingerprint != action.fingerprint:",
        "if False:",
    ),
    (
        "a person's takeover stops the agent",
        "helia_gateway/gateway.py",
        'return "a person has taken over the ticket"',
        "pass",
    ),
    (
        "waiting actions re-checked on rule changes",
        "helia_gateway/gateway.py",
        "if action.rules_version != self.registry.version:",
        "if False:",
    ),
    (
        "a decline binds the agent",
        "helia_gateway/gateway.py",
        "self.ticketing.assign(action.case_id, caller.id)",
        "pass",
    ),
    (
        "kill switch stops waiting actions",
        "helia_gateway/gateway.py",
        "if tool is not None and tool.kill_switch in self.switched_off:  # stops waiting actions too",
        "if False:  # stops waiting actions too",
    ),
    (
        "sweep expires approvals nobody acted on",
        "helia_gateway/gateway.py",
        "if self._expire_if_due(action):\n                outcomes.append",
        "if False:\n                outcomes.append",
    ),
    (
        "sweep hands over a switched-off tool's waits",
        "helia_gateway/gateway.py",
        "elif tool is not None and tool.kill_switch in self.switched_off:  # sweep",
        "elif False:  # sweep",
    ),
    (
        "re-proposing reports an expired approval",
        "helia_gateway/gateway.py",
        "if existing and self._expire_if_due(existing):",
        "if False:",
    ),
    (
        "a decline leaves a person's ticket alone",
        "helia_gateway/gateway.py",
        "if ticket is not None and ticket.assignee == AGENT_ID:  # the decision binds",
        "if ticket is not None:  # the decision binds",
    ),
    ("timeout: ask billing before retrying", "helia_gateway/gateway.py", "if found:", "if False:"),
    (
        "unexpected errors count as unknown",
        "helia_gateway/gateway.py",
        "except Exception as error:  # anything else",
        "except ZeroDivisionError as error:  # anything else",
    ),
    (
        "replay returns the existing action",
        "helia_gateway/gateway.py",
        "if existing and existing.status in REPLAYABLE:",
        "if False:",
    ),
    (
        "no agent replies on a person's ticket",
        "helia_gateway/gateway.py",
        "if ticket is None or ticket.assignee != AGENT_ID:  # a person's ticket",
        "if False:  # a person's ticket",
    ),
    ("amounts blocked in reply free text", "helia_gateway/replies.py", '        (MONEY, "an amount"),\n', ""),
    ("graders can hold a reply", "helia_gateway/replies.py", "if concern:", "if False:"),
    (
        "rejected model output logged as a hash",
        "helia_gateway/gateway.py",
        "params_sha256=sha256_hex([tool_name, params])",
        "params=params",
    ),
    ("audit: each event's own hash", "helia_gateway/audit.py", 'or sha256_hex(body) != event["hash"]', ""),
    ("audit: link to the previous event", "helia_gateway/audit.py", 'or body.get("prev_hash") != prev', ""),
    (
        "audit: published anchors",
        "helia_gateway/audit.py",
        'if position >= len(self._events) or self._events[position]["hash"] != expected:',
        "if False:",
    ),
]


def main() -> int:
    missed = 0
    for name, path, code, switched_off in PROTECTIONS:
        with tempfile.TemporaryDirectory() as scratch:
            copy = Path(scratch) / "poc"
            shutil.copytree(HERE, copy, ignore=shutil.ignore_patterns("__pycache__"))
            source = (copy / path).read_text()
            if source.count(code) != 1:
                print(f"SKIPPED  {name}: the code it looks for has changed; update this script")
                missed += 1
                continue
            (copy / path).write_text(source.replace(code, switched_off))
            run = subprocess.run([sys.executable, "-m", "unittest"], cwd=copy, capture_output=True, text=True)
            failed = re.search(r"FAILED \((.*)\)", run.stderr)
            outcome = failed.group(1) if failed else "all tests pass"
            print(f"{'caught' if failed else 'MISSED':<8} {name:<44} {outcome}")
            missed += not failed
    covered = len(PROTECTIONS) - missed
    print(f"\n{covered} of {len(PROTECTIONS)} protections are covered by a test that fails without them.")
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())
