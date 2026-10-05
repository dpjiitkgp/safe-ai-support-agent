"""In-memory stand-ins for Helia's billing API, the ticketing system and the help centre.

They behave like the real systems in the ways the gateway depends on, including the failures:
billing can time out before or after committing a refund, and its lookup can be unreachable."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from .common import AGENT_ID, money


class Clock:
    def __init__(self, start: datetime | None = None):
        self.now = start or datetime(2026, 10, 5, 12, 0, tzinfo=UTC)

    def advance(self, **delta) -> None:
        self.now += timedelta(**delta)


class BillingTimeout(Exception):
    """The call timed out: the refund may or may not have happened."""


class BillingUnavailable(Exception):
    """Billing can't be reached, so nothing can be confirmed."""


@dataclass
class Charge:
    id: str
    customer_id: str
    amount: Decimal
    currency: str
    created_at: datetime


@dataclass
class Refund:
    id: str
    charge_id: str
    customer_id: str
    amount: Decimal
    currency: str
    created_at: datetime
    idempotency_key: str | None


class FakeBilling:
    def __init__(self, clock: Clock):
        self.clock = clock
        self.charges: dict[str, Charge] = {}
        self.refunds: list[Refund] = []
        # queued: "timeout_before_commit", "timeout_after_commit" or "connection_error_after_commit"
        self.refund_faults: list[str] = []
        self.lookup_faults: list[str] = []  # queued: "unavailable"

    def add_charge(self, charge_id, customer_id, amount, *, days_ago=1, currency="USD"):
        created = self.clock.now - timedelta(days=days_ago)
        self.charges[charge_id] = Charge(charge_id, customer_id, money(amount), currency, created)

    def get_charge(self, charge_id: str) -> Charge | None:
        return self.charges.get(charge_id)

    def charges_for_customer(self, customer_id: str) -> list[Charge]:
        return [c for c in self.charges.values() if c.customer_id == customer_id]

    def refunded_total(self, charge_id: str) -> Decimal:
        return sum((r.amount for r in self.refunds if r.charge_id == charge_id), Decimal("0.00"))

    def last_refund_for_customer(self, customer_id: str) -> Refund | None:
        theirs = [r for r in self.refunds if r.customer_id == customer_id]
        return max(theirs, key=lambda r: r.created_at) if theirs else None

    def refund(self, charge_id: str, amount: Decimal, idempotency_key: str) -> Refund:
        """Idempotent: the same key always returns the same refund."""
        existing = self._find(idempotency_key)
        if existing:
            return existing
        fault = self.refund_faults.pop(0) if self.refund_faults else None
        if fault == "timeout_before_commit":
            raise BillingTimeout()
        refund = self._create(charge_id, amount, idempotency_key)
        if fault == "timeout_after_commit":
            raise BillingTimeout()
        if fault == "connection_error_after_commit":
            raise ConnectionError("billing returned 503")
        return refund

    def find_refund(self, idempotency_key: str) -> Refund | None:
        if self.lookup_faults:
            self.lookup_faults.pop(0)
            raise BillingUnavailable()
        return self._find(idempotency_key)

    def manual_refund(self, charge_id: str, amount) -> Refund:
        """A person refunding directly in the billing tool, outside the agent."""
        return self._create(charge_id, amount, None)

    def _find(self, key):
        return next((r for r in self.refunds if key is not None and r.idempotency_key == key), None)

    def _create(self, charge_id, amount, key):
        charge = self.charges[charge_id]
        if money(amount) > charge.amount - self.refunded_total(charge_id):
            raise ValueError("refund is larger than what is left on the charge")
        refund = Refund(
            f"re_{len(self.refunds) + 1}",
            charge_id,
            charge.customer_id,
            money(amount),
            charge.currency,
            self.clock.now,
            key,
        )
        self.refunds.append(refund)
        return refund


@dataclass
class Ticket:
    id: str
    customer_id: str
    customer_email: str
    request_type: str  # set by the router's classifier
    assignee: str = AGENT_ID
    replies: list[str] = field(default_factory=list)


class FakeTicketing:
    def __init__(self):
        self.tickets: dict[str, Ticket] = {}

    def add_ticket(self, ticket_id, customer_id, request_type, *, email, assignee=AGENT_ID):
        self.tickets[ticket_id] = Ticket(ticket_id, customer_id, email, request_type, assignee)

    def get(self, ticket_id: str) -> Ticket | None:
        return self.tickets.get(ticket_id)

    def assign(self, ticket_id: str, assignee: str) -> None:
        self.tickets[ticket_id].assignee = assignee

    def post_reply(self, ticket_id: str, text: str) -> None:
        self.tickets[ticket_id].replies.append(text)


class FakeKnowledgeBase:
    def __init__(self, article_ids):
        self.article_ids = set(article_ids)

    def exists(self, article_id: str) -> bool:
        return article_id in self.article_ids
