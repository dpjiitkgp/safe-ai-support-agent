"""Shared test setup: a gateway wired to fresh fakes, and a one-line refund case."""

from __future__ import annotations

import tomllib
from pathlib import Path

from helia_gateway.audit import AuditLog
from helia_gateway.common import AGENT, human
from helia_gateway.fakes import Clock, FakeBilling, FakeKnowledgeBase, FakeTicketing
from helia_gateway.gateway import ActionGateway
from helia_gateway.registry import Registry

POLICY_DIR = Path(__file__).resolve().parents[1] / "policy"

LEAD = human("u_lead", "support_lead")
FINANCE = human("u_finance", "finance")
SUPPORT_AGENT = human("u_support", "support_agent")


def policy_dicts() -> tuple[dict, dict]:
    """Fresh, editable copies of the shipped policy files."""
    with open(POLICY_DIR / "tools.toml", "rb") as f:
        tools = tomllib.load(f)
    with open(POLICY_DIR / "ceilings.toml", "rb") as f:
        ceilings = tomllib.load(f)
    return tools, ceilings


class World:
    def __init__(self, registry: Registry | None = None, reply_graders=()):
        self.clock = Clock()
        self.billing = FakeBilling(self.clock)
        self.ticketing = FakeTicketing()
        self.kb = FakeKnowledgeBase({"RF-14", "RF-DUP"})
        self.audit = AuditLog(self.clock)
        self.registry = registry or Registry.load(POLICY_DIR)
        self.gateway = ActionGateway(
            self.registry, self.billing, self.ticketing, self.kb, self.audit, self.clock, reply_graders
        )
        self._cases = 0

    def refund_case(
        self, amount, *, request_type="duplicate_charge", days_ago=2, customer=None, duplicate=True
    ):
        """A ticket owned by the agent and a charge on the customer's account. By default the same
        amount was also charged a day earlier, so a "duplicate_charge" reason is genuinely true."""
        self._cases += 1
        case_id, charge_id = f"case_{self._cases}", f"ch_{self._cases}"
        customer = customer or f"cus_{self._cases}"
        self.billing.add_charge(charge_id, customer, amount, days_ago=days_ago)
        if duplicate:
            self.billing.add_charge(f"{charge_id}d", customer, amount, days_ago=days_ago + 1)
        self.ticketing.add_ticket(case_id, customer, request_type, email=f"{customer}@example.com")
        return case_id, charge_id

    def propose_refund(self, case_id, charge_id, reason="duplicate_charge"):
        return self.gateway.propose(
            AGENT, case_id, "issue_refund", {"charge_id": charge_id, "reason": reason}
        )

    def refunds(self, charge_id):
        return [r for r in self.billing.refunds if r.charge_id == charge_id]
