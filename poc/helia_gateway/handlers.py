"""Team-owned tool handlers. Only the gateway calls them.

In production each handler lives in its team's service and holds the credentials for the one
endpoint it wraps. Here: the Payments team's handler for issue_refund. It refunds whatever is
left on a charge (no proration); the model never says how much."""

from __future__ import annotations

from datetime import timedelta

from .common import money

DUPLICATE_WINDOW = timedelta(days=3)
CANCELLATION_WINDOW_DAYS = 14


class RefundHandler:
    def __init__(self, billing, clock):
        self.billing = billing
        self.clock = clock

    def resolve(self, params: dict, ticket) -> tuple[dict | None, str | None]:
        """Turn the model's references into checked values. Returns (computed, problem)."""
        charge = self.billing.get_charge(params["charge_id"])
        if charge is None or charge.customer_id != ticket.customer_id:
            return None, "charge not found for this customer"  # same answer either way: no probing
        refundable = money(charge.amount - self.billing.refunded_total(charge.id))
        if refundable <= 0:
            return None, "nothing left to refund on this charge"
        return {
            "charge_id": charge.id,
            "customer_id": charge.customer_id,
            "amount": refundable,
            "currency": charge.currency,
        }, None

    def facts(self, params: dict, computed: dict, ticket) -> dict:
        """What the approval rules need to know, all from Helia's data."""
        charge = self.billing.get_charge(computed["charge_id"])
        last = self.billing.last_refund_for_customer(ticket.customer_id)
        return {
            "amount": computed["amount"],
            "reason": params["reason"],
            "reason_confirmed": self._reason_confirmed(params["reason"], charge),
            "charge_age_days": (self.clock.now - charge.created_at).days,
            "days_since_last_refund": None if last is None else (self.clock.now - last.created_at).days,
        }

    def execute(self, computed: dict, idempotency_key: str) -> dict:
        return _as_result(self.billing.refund(computed["charge_id"], computed["amount"], idempotency_key))

    def lookup(self, idempotency_key: str) -> dict | None:
        refund = self.billing.find_refund(idempotency_key)
        return _as_result(refund) if refund else None

    def _reason_confirmed(self, reason: str, charge) -> bool:
        """The model picks the reason, so billing data has to back it up before a refund runs alone.
        An unconfirmed reason isn't rejected; it just needs a person."""
        if reason == "duplicate_charge":
            return any(
                other.id != charge.id
                and other.amount == charge.amount
                and abs(other.created_at - charge.created_at) <= DUPLICATE_WINDOW
                for other in self.billing.charges_for_customer(charge.customer_id)
            )
        if reason == "cancelled_within_14_days":
            return (self.clock.now - charge.created_at).days <= CANCELLATION_WINDOW_DAYS
        return False  # service_outage, other: nothing here can confirm them


def _as_result(refund) -> dict:
    return {"refund_id": refund.id, "amount": refund.amount, "currency": refund.currency}
