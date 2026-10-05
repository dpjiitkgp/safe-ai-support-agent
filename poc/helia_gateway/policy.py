"""Decides whether an action may run on its own and, if not, which roles must approve it.

Two kinds of rule with two owners: the tool's auto-approve conditions (Support Ops) and the hard
ceilings (Finance and Legal). A ceiling always wins: an action over a ceiling never runs alone,
whatever the auto rule says. Every reason an action can't run alone is returned, so the approver
and the audit log see exactly why."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Facts:
    """Everything the rules look at. All of it comes from Helia's data, none of it from the model."""

    amount: Decimal
    reason: str
    reason_confirmed: bool
    charge_age_days: int
    days_since_last_refund: int | None
    request_type: str
    auto_total_last_hour: Decimal


@dataclass(frozen=True)
class Verdict:
    runs_alone: bool
    needs_roles: tuple[str, ...]
    why: tuple[str, ...]


def evaluate(tool, ceilings, facts: Facts) -> Verdict:
    why: list[str] = []
    if facts.request_type not in tool.request_types:
        why.append(f"{tool.name} is not expected for request type '{facts.request_type}'")
    if tool.auto_approve is None:
        why.append("this tool has no auto-approve rule")
    else:
        why.extend(_auto_rule_failures(tool.auto_approve, facts))

    roles = list(tool.approval_roles)
    for ceiling in ceilings:
        if facts.amount > ceiling.amount_above:
            why.append(f"over the {ceiling.owner} ceiling '{ceiling.id}' ({ceiling.amount_above})")
            roles += [r for r in ceiling.require_roles if r not in roles]

    if not why:
        return Verdict(True, (), ())
    return Verdict(False, tuple(roles), tuple(why))


def _auto_rule_failures(rule: dict, f: Facts):
    if "max_amount" in rule and f.amount > rule["max_amount"]:
        yield f"amount {f.amount} is over the auto-approve limit {rule['max_amount']}"
    if "reasons" in rule and f.reason not in rule["reasons"]:
        yield f"reason '{f.reason}' can't be auto-approved"
    elif not f.reason_confirmed:
        yield f"billing data doesn't confirm the reason '{f.reason}'"
    if "max_charge_age_days" in rule and f.charge_age_days > rule["max_charge_age_days"]:
        yield f"charge is {f.charge_age_days} days old (auto-approve limit {rule['max_charge_age_days']})"
    if (
        "no_refund_within_days" in rule
        and f.days_since_last_refund is not None
        and f.days_since_last_refund < rule["no_refund_within_days"]
    ):
        yield f"customer had a refund {f.days_since_last_refund} days ago"
    if "hourly_cap" in rule and f.auto_total_last_hour + f.amount > rule["hourly_cap"]:
        yield f"hourly auto-refund cap {rule['hourly_cap']} would be exceeded"
