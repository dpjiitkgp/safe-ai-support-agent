"""The Action Gateway: the only path from the agent to Helia's APIs.

The model proposes; this code decides and executes. It never takes a value from the model that it
can work out itself, it applies the approval rules from the registry, and it writes an audit event
at every step. There is no LLM anywhere in this decision path."""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from . import policy, replies
from .common import AGENT_ID, Principal, sha256_hex
from .fakes import BillingTimeout
from .handlers import RefundHandler

MAX_RETRIES = 2
APPROVAL_TTL = timedelta(hours=24)
HUMAN_QUEUE = "support_queue"  # where the gateway hands cases it can't finish
CHARGE_REF = re.compile(r"ch_[A-Za-z0-9]+")
REPLAYABLE = ("pending_approval", "executed", "uncertain", "declined")


@dataclass(frozen=True)
class Decision:
    """What the caller gets back. Results only include the fields the tool declares in `returns`.

    status: executed, pending_approval, rejected, denied, void, expired, declined, uncertain, failed or sent.
    """

    status: str
    reason: str = ""
    action_id: str | None = None
    result: dict = field(default_factory=dict)
    needs_roles: tuple[str, ...] = ()
    fingerprint: str | None = None


@dataclass
class Action:
    id: str
    case_id: str
    tool: str
    params: dict
    computed: dict
    idempotency_key: str
    fingerprint: str
    runs_alone: bool
    why: tuple[str, ...]
    needs_roles: tuple[str, ...]
    rules_version: str
    expires_at: datetime
    status: str = "pending_approval"
    approvals: dict[str, str] = field(default_factory=dict)  # role -> approver id
    result: dict = field(default_factory=dict)

    def missing_roles(self) -> tuple[str, ...]:
        return tuple(r for r in self.needs_roles if r not in self.approvals)


class ActionGateway:
    def __init__(self, registry, billing, ticketing, kb, audit, clock, reply_graders=()):
        self.registry = registry
        self.billing = billing
        self.ticketing = ticketing
        self.kb = kb
        self.audit = audit
        self.clock = clock
        self.reply_graders = tuple(reply_graders)  # configured here, so the agent can't skip them
        self.handlers = {"issue_refund": RefundHandler(billing, clock)}
        self.switched_off: set[str] = set()
        self.actions: dict[str, Action] = {}
        self._by_key: dict[str, str] = {}
        self._auto_ledger: list[tuple[datetime, str, Decimal]] = []
        self._ids = itertools.count(1)

    # --- Operations: kill switches and rule changes -----------------------------------------

    def switch_off(self, name: str) -> None:
        self.switched_off.add(name)

    def switch_on(self, name: str) -> None:
        self.switched_off.discard(name)

    def sweep(self) -> list[Decision]:
        """Run on a schedule: nothing else acts on an approval nobody clicks. Expires old approvals
        and voids those whose tool is switched off, handing each case to a person."""
        outcomes = []
        for action in [a for a in self.actions.values() if a.status == "pending_approval"]:
            tool = self.registry.tool(action.tool)
            if self._expire_if_due(action):
                outcomes.append(self._decision(action, "approval window passed; the case goes to a person"))
            elif tool is not None and tool.kill_switch in self.switched_off:  # sweep: switched off
                self._hand_over(action.case_id, f"kill switch is on for {action.tool}")
                outcomes.append(self._void(action, "kill switch is on; the case goes to a person"))
        return outcomes

    def use_rules(self, registry) -> None:
        """Load a new rules version. Waiting actions are re-checked against it before they run."""
        self.audit.append(
            "rules_changed", case_id="*", actor="gateway", old=self.registry.version, new=registry.version
        )
        self.registry = registry

    # --- Proposals: the agent's only way to act ---------------------------------------------

    def propose(self, caller: Principal, case_id: str, tool_name: str, params: dict) -> Decision:
        tool = self.registry.tool(tool_name) if tool_name in self.handlers else None
        if caller.kind != "agent":
            problem = "only the agent service proposes actions"
        elif tool is None:
            problem = "unknown tool"
        else:
            problem = _validate(tool, params)
        if problem:
            # Rejected model output can contain anything, so the log keeps a hash, not the text.
            self.audit.append(
                "proposal_rejected",
                case_id=case_id,
                actor=caller.id,
                params_sha256=sha256_hex([tool_name, params]),
                why=problem,
            )
            return Decision("rejected", problem)
        self.audit.append("proposed", case_id=case_id, actor=caller.id, tool=tool_name, params=params)
        ticket = self.ticketing.get(case_id)
        if ticket is None or ticket.assignee != AGENT_ID:
            return self._reject(case_id, "ticket is not assigned to the agent")
        if tool.kill_switch in self.switched_off:
            self._hand_over(case_id, f"kill switch is on for {tool_name}")
            return self._reject(case_id, "kill switch is on; the case goes to a person")

        key = ":".join(case_id if part == "case_id" else params[part] for part in tool.idempotency_key)
        existing = self.actions.get(self._by_key.get(key, ""))
        if existing and self._expire_if_due(existing):
            return self._decision(existing, "approval window passed; the case goes to a person")
        if existing and existing.status in REPLAYABLE:
            self.audit.append("replayed", case_id=case_id, actor="gateway", action_id=existing.id)
            return self._decision(existing, "already proposed; this is the existing action")

        computed, problem = self.handlers[tool_name].resolve(params, ticket)
        if problem:
            return self._reject(case_id, problem)
        verdict = self._verdict(tool, params, computed, ticket)
        action = Action(
            id=f"act_{next(self._ids)}",
            case_id=case_id,
            tool=tool_name,
            params=dict(params),
            computed=computed,
            idempotency_key=key,
            fingerprint=_fingerprint(tool_name, params, computed),
            runs_alone=verdict.runs_alone,
            why=verdict.why,
            needs_roles=verdict.needs_roles,
            rules_version=self.registry.version,
            expires_at=self.clock.now + APPROVAL_TTL,
        )
        self.actions[action.id] = action
        self._by_key[key] = action.id
        self.audit.append(
            "decided",
            case_id=case_id,
            actor="gateway",
            action_id=action.id,
            runs_alone=verdict.runs_alone,
            why=list(verdict.why),
            needs_roles=list(verdict.needs_roles),
            computed=computed,
            fingerprint=action.fingerprint,
            rules_version=self.registry.version,
        )
        if verdict.runs_alone:
            return self._execute(action)
        return self._decision(action, "; ".join(verdict.why))

    # --- Approvals: people only, from the console --------------------------------------------

    def review(self, action_id: str) -> dict:
        """What the agent panel in the console shows an approver."""
        a = self.actions[action_id]
        return {
            "action": a.id,
            "case": a.case_id,
            "tool": a.tool,
            "params": a.params,
            "amount": f"{a.computed['amount']} {a.computed['currency']}",
            "why it needs a person": list(a.why),
            "still needs": list(a.missing_roles()),
            "fingerprint": a.fingerprint[:16] + "...",
            "expires": a.expires_at.isoformat(),
        }

    def approve(self, caller: Principal, action_id: str, fingerprint: str) -> Decision:
        action = self.actions.get(action_id)
        if action is None:
            return Decision("rejected", f"unknown action {action_id}")
        if caller.kind != "human":
            return self._deny(action, caller, "only a person can approve; the agent can only propose")
        if action.status != "pending_approval":
            return self._deny(action, caller, f"action is {action.status}, not waiting for approval")
        if self._expire_if_due(action):
            return self._decision(action, "approval window passed; the case goes to a person")
        if caller.id in action.approvals.values():
            return self._deny(action, caller, "one person can't fill two approval roles")
        role = next((r for r in action.missing_roles() if caller.has_role(r)), None)
        if role is None:
            return self._deny(action, caller, f"needs {' or '.join(action.missing_roles())}")
        if fingerprint != action.fingerprint:
            return self._deny(action, caller, "this approval is for a different version of the action")
        changed = self._what_changed(action)
        if changed:
            return self._void(action, changed)
        action.approvals[role] = caller.id
        self.audit.append(
            "approved",
            case_id=action.case_id,
            actor=caller.id,
            action_id=action.id,
            role=role,
            fingerprint=fingerprint,
        )
        if action.missing_roles():
            return self._decision(action, f"approved as {role}")
        return self._execute(action)

    def decline(self, caller: Principal, action_id: str, note: str) -> Decision:
        action = self.actions.get(action_id)
        if action is None:
            return Decision("rejected", f"unknown action {action_id}")
        if caller.kind != "human" or action.status != "pending_approval":
            return self._deny(action, caller, "only a person can decline a pending action")
        action.status = "declined"
        ticket = self.ticketing.get(action.case_id)
        if ticket is not None and ticket.assignee == AGENT_ID:  # the decision binds: the agent can't retry it
            self.ticketing.assign(action.case_id, caller.id)
        self.audit.append(
            "declined",
            case_id=action.case_id,
            actor=caller.id,
            action_id=action.id,
            note_sha256=sha256_hex(note),
        )
        return self._decision(action, f"declined by {caller.id}, who now has the case")

    # --- Replies --------------------------------------------------------------------------

    def send_reply(self, caller: Principal, case_id: str, blocks: list[dict]) -> Decision:
        if caller.kind != "agent":
            return self._reject(case_id, "only the agent service proposes replies")
        ticket = self.ticketing.get(case_id)
        if ticket is None or ticket.assignee != AGENT_ID:  # a person's ticket gets no agent replies
            return self._reject(case_id, "ticket is not assigned to the agent")
        check = replies.check_reply(
            blocks,
            ticket=ticket,
            actions=self.actions,
            registry=self.registry,
            billing=self.billing,
            kb=self.kb,
            graders=self.reply_graders,
        )
        if check.problems:
            self.audit.append("reply_blocked", case_id=case_id, actor="gateway", why=check.problems)
            return Decision("rejected", "; ".join(check.problems))
        if check.for_human:
            self.audit.append(
                "reply_held",
                case_id=case_id,
                actor="gateway",
                why=check.for_human,
                text_sha256=sha256_hex(check.text),
            )
            return Decision("pending_approval", "; ".join(check.for_human))
        self.ticketing.post_reply(case_id, check.text)
        self.audit.append(
            "reply_sent",
            case_id=case_id,
            actor=caller.id,
            text_sha256=sha256_hex(check.text),
            actions=check.action_ids,
            citations=check.citations,
        )
        return Decision("sent", result={"text": check.text})

    # --- Execution ------------------------------------------------------------------------

    def _execute(self, action: Action) -> Decision:
        tool = self.registry.tool(action.tool)
        if tool is not None and tool.kill_switch in self.switched_off:  # stops waiting actions too
            self._hand_over(action.case_id, f"kill switch is on for {action.tool}")
            return self._void(action, "kill switch is on; the case goes to a person")
        changed = self._what_changed(action)
        if changed:
            return self._void(action, changed)
        if action.runs_alone:  # counted before billing, so a refund of unknown outcome still uses the cap
            self._auto_ledger.append((self.clock.now, action.tool, action.computed["amount"]))
        handler = self.handlers[action.tool]
        for attempt in range(1, MAX_RETRIES + 2):
            try:
                return self._executed(action, handler.execute(action.computed, action.idempotency_key))
            except BillingTimeout:
                # A timeout means "unknown", not "failed": ask billing before trying again.
                self.audit.append(
                    "timeout", case_id=action.case_id, actor="gateway", action_id=action.id, attempt=attempt
                )
                try:
                    found = handler.lookup(action.idempotency_key)
                except Exception as error:
                    return self._uncertain(action, f"billing can't confirm ({type(error).__name__})")
                if found:
                    return self._executed(action, found, "confirmed after a timeout")
                # Not found: it didn't happen, so retrying with the same key is safe.
            except Exception as error:  # anything else may have reached billing, so it's unknown too
                return self._uncertain(action, f"unexpected billing error ({type(error).__name__})")
        action.status = "failed"
        self.audit.append(
            "failed", case_id=action.case_id, actor="gateway", action_id=action.id, attempts=MAX_RETRIES + 1
        )
        self._hand_over(action.case_id, "refund still failing after retries")
        return self._decision(action, f"still failing after {MAX_RETRIES} retries; handed to a person")

    def _executed(self, action: Action, result: dict, note: str = "done") -> Decision:
        action.status = "executed"
        action.result = result
        self.audit.append(
            "executed", case_id=action.case_id, actor="gateway", action_id=action.id, result=result, note=note
        )
        return self._decision(action, note)

    def _uncertain(self, action: Action, why: str) -> Decision:
        action.status = "uncertain"
        self.audit.append("uncertain", case_id=action.case_id, actor="gateway", action_id=action.id, why=why)
        self._hand_over(action.case_id, "refund outcome unknown: check billing before anything else")
        return self._decision(action, f"{why}; a person must check billing first")

    def _what_changed(self, action: Action) -> str | None:
        """Re-check just before acting: the world, or the rules, may have moved since the proposal."""
        ticket = self.ticketing.get(action.case_id)
        if ticket is None or ticket.assignee != AGENT_ID:
            return "a person has taken over the ticket"
        computed, problem = self.handlers[action.tool].resolve(action.params, ticket)
        if problem:
            return problem
        if _fingerprint(action.tool, action.params, computed) != action.fingerprint:
            return "the amount or account changed since it was proposed"
        if action.rules_version != self.registry.version:
            tool = self.registry.tool(action.tool)
            if tool is None:
                return "the tool is no longer registered"
            verdict = self._verdict(tool, action.params, computed, ticket)
            if [r for r in verdict.needs_roles if r not in action.needs_roles] or (
                action.runs_alone and not verdict.runs_alone
            ):
                return (
                    f"the rules changed since it was proposed; it now needs {', '.join(verdict.needs_roles)}"
                )
        return None

    def _verdict(self, tool, params: dict, computed: dict, ticket) -> policy.Verdict:
        facts = policy.Facts(
            **self.handlers[tool.name].facts(params, computed, ticket),
            request_type=ticket.request_type,
            auto_total_last_hour=self._auto_total_last_hour(tool.name),
        )
        return policy.evaluate(tool, self.registry.ceilings_for(tool.name), facts)

    def _auto_total_last_hour(self, tool_name: str) -> Decimal:
        since = self.clock.now - timedelta(hours=1)
        return sum(
            (amount for at, tool, amount in self._auto_ledger if tool == tool_name and at > since),
            Decimal("0.00"),
        )

    # --- Outcomes -------------------------------------------------------------------------

    def _hand_over(self, case_id: str, why: str) -> None:
        ticket = self.ticketing.get(case_id)
        if ticket is not None and ticket.assignee == AGENT_ID:
            self.ticketing.assign(case_id, HUMAN_QUEUE)
        self.audit.append("handed_over", case_id=case_id, actor="gateway", to=HUMAN_QUEUE, why=why)

    def _expire_if_due(self, action: Action) -> bool:
        if action.status != "pending_approval" or self.clock.now <= action.expires_at:
            return False
        action.status = "expired"
        self.audit.append("expired", case_id=action.case_id, actor="gateway", action_id=action.id)
        self._hand_over(action.case_id, "approval expired")
        return True

    def _void(self, action: Action, why: str) -> Decision:
        action.status = "void"
        self.audit.append("void", case_id=action.case_id, actor="gateway", action_id=action.id, why=why)
        return self._decision(action, why)

    def _deny(self, action: Action, caller: Principal, why: str) -> Decision:
        self.audit.append(
            "approval_denied", case_id=action.case_id, actor=caller.id, action_id=action.id, why=why
        )
        return Decision("denied", why, action.id, needs_roles=action.missing_roles())

    def _reject(self, case_id: str, why: str) -> Decision:
        self.audit.append("rejected", case_id=case_id, actor="gateway", why=why)
        return Decision("rejected", why)

    def _decision(self, action: Action, reason: str) -> Decision:
        tool = self.registry.tool(action.tool)
        returns = tool.returns if tool else ()
        pending = action.status == "pending_approval"
        return Decision(
            status=action.status,
            reason=reason,
            action_id=action.id,
            result={k: v for k, v in action.result.items() if k in returns},
            needs_roles=action.missing_roles() if pending else (),
            fingerprint=action.fingerprint if pending else None,
        )


def _validate(tool, params) -> str | None:
    """The model may supply exactly the declared inputs, each of the declared type. Nothing else."""
    if not isinstance(params, dict):
        return "parameters must be an object"
    unexpected = sorted(set(params) - set(tool.inputs))
    if unexpected:
        return (
            f"unexpected field(s) {', '.join(unexpected)}; the model may only supply {', '.join(tool.inputs)}"
        )
    missing = sorted(set(tool.inputs) - set(params))
    if missing:
        return f"missing field(s) {', '.join(missing)}"
    for name, spec in tool.inputs.items():
        value = params[name]
        if spec["type"] == "enum" and value not in spec["values"]:
            return f"{name} must be one of {', '.join(spec['values'])}"
        if spec["type"] == "charge_ref" and not (isinstance(value, str) and CHARGE_REF.fullmatch(value)):
            return f"{name} is not a charge reference"
    return None


def _fingerprint(tool: str, params: dict, computed: dict) -> str:
    """Identifies exactly what an approver saw: if any of it changes, the approval no longer applies."""
    return sha256_hex({"tool": tool, "params": params, "computed": computed})
