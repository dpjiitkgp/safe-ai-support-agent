"""Walks through the design's scenarios using the real gateway code and fake Helia systems.

Run from this folder:  python3 demo.py"""

from pathlib import Path

from helia_gateway.audit import AuditLog
from helia_gateway.common import AGENT, human
from helia_gateway.fakes import Clock, FakeBilling, FakeKnowledgeBase, FakeTicketing
from helia_gateway.gateway import ActionGateway
from helia_gateway.registry import Registry

LEAD = human("u_lead_17", "support_lead")
FINANCE = human("u_fin_03", "finance")


def main():
    clock = Clock()
    billing, ticketing, audit = FakeBilling(clock), FakeTicketing(), AuditLog(clock)
    registry = Registry.load(Path(__file__).parent / "policy")
    gw = ActionGateway(registry, billing, ticketing, FakeKnowledgeBase({"RF-14"}), audit, clock)
    print(f"Rules: {len(registry.tools)} tool, {len(registry.ceilings)} ceiling, version {registry.version}")

    billing.add_charge("ch_201", "cus_beta", "49.00", days_ago=3)  # charged twice: a real duplicate
    billing.add_charge("ch_202", "cus_beta", "49.00", days_ago=2)
    billing.add_charge("ch_301", "cus_delta", "40.00", days_ago=4)  # charged once
    billing.add_charge("ch_881", "cus_acme", "1200.00", days_ago=10)
    billing.add_charge("ch_777", "cus_gamma", "300.00", days_ago=5)
    ticketing.add_ticket("case_4510", "cus_beta", "duplicate_charge", email="billing@beta.example")
    ticketing.add_ticket("case_4512", "cus_acme", "downgrade_with_refund", email="ops@acme.example")
    ticketing.add_ticket("case_4520", "cus_gamma", "refund_request", email="hello@gamma.example")
    ticketing.add_ticket("case_4530", "cus_delta", "duplicate_charge", email="team@delta.example")

    section("1. 'I was charged twice' ($49): runs on its own")
    refund = gw.propose(
        AGENT, "case_4510", "issue_refund", {"charge_id": "ch_202", "reason": "duplicate_charge"}
    )
    show("agent proposes issue_refund(ch_202, duplicate_charge)", refund)
    reply = gw.send_reply(
        AGENT,
        "case_4510",
        [
            {"text": "Sorry about the double charge, and thanks for flagging it."},
            {"commitment": refund.action_id},
            {"text": "Is there anything else I can help with?"},
        ],
    )
    show("agent proposes a reply that uses the refund's template", reply)
    quote(reply.result["text"])

    section("2. A manipulated or mistaken model: nothing gets through on its own")
    for label, tool, params in [
        (
            "model adds its own amount",
            "issue_refund",
            {"charge_id": "ch_202", "reason": "duplicate_charge", "amount": "2000"},
        ),
        (
            "model targets another customer's charge",
            "issue_refund",
            {"charge_id": "ch_881", "reason": "duplicate_charge"},
        ),
        ("model invents a tool", "call_api", {"url": "https://billing.internal/refund-all"}),
    ]:
        show(label, gw.propose(AGENT, "case_4510", tool, params))
    show(
        "model believes 'I was charged twice' when billing shows one charge",
        gw.propose(AGENT, "case_4530", "issue_refund", {"charge_id": "ch_301", "reason": "duplicate_charge"}),
    )

    section("3. Undo an upgrade ($1,200): a support lead and Finance must approve")
    pending = gw.propose(
        AGENT, "case_4512", "issue_refund", {"charge_id": "ch_881", "reason": "cancelled_within_14_days"}
    )
    show("agent proposes issue_refund(ch_881, cancelled_within_14_days)", pending)
    show(
        "agent drafts a confident reply",
        gw.send_reply(AGENT, "case_4512", [{"text": "Done! Your $1,200 refund has been processed."}]),
    )
    reply = gw.send_reply(
        AGENT, "case_4512", [{"text": "Thanks for letting us know."}, {"commitment": pending.action_id}]
    )
    show("agent redrafts using the holding template", reply)
    quote(reply.result["text"])
    print("  console panel shows the approver:")
    for key, value in gw.review(pending.action_id).items():
        print(f"    {key}: {value}")
    show("agent tries to approve its own action", gw.approve(AGENT, pending.action_id, pending.fingerprint))
    show("support lead approves", gw.approve(LEAD, pending.action_id, pending.fingerprint))
    billing.refund_faults.append("timeout_after_commit")
    show(
        "Finance approves; billing times out after committing",
        gw.approve(FINANCE, pending.action_id, pending.fingerprint),
    )
    print(f"  refunds on ch_881: {sum(1 for r in billing.refunds if r.charge_id == 'ch_881')}")

    section("4. A person takes the ticket while the refund waits for approval")
    pending = gw.propose(
        AGENT, "case_4520", "issue_refund", {"charge_id": "ch_777", "reason": "service_outage"}
    )
    show("agent proposes issue_refund(ch_777, service_outage)", pending)
    ticketing.assign("case_4520", "u_support_42")
    show("support lead approves after the takeover", gw.approve(LEAD, pending.action_id, pending.fingerprint))

    section("5. The audit log")
    print(f"  {len(audit.events())} events, chain intact: {audit.verify() is None}")
    for event in audit.events("case_4512"):
        print(f"    #{event['seq']:<3}{event['type']:<17}by {event['actor']}")
    decided = next(e for e in audit._events if e["type"] == "decided" and e["case_id"] == "case_4512")
    decided["computed"]["amount"] = "12.00"  # someone edits a row in the database
    print(f"  after editing event #{decided['seq']}, the chain breaks at #{audit.verify()}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


def show(label, decision):
    detail = decision.reason
    if "refund_id" in decision.result:
        detail = (
            f"{decision.result['refund_id']} for {decision.result['amount']} {decision.result['currency']}"
        )
        if decision.reason != "done":
            detail += f" ({decision.reason})"
    if decision.needs_roles:
        detail += f" [needs: {', '.join(decision.needs_roles)}]"
    print(f"  {label}\n    -> {decision.status.upper()}{': ' + detail if detail else ''}")


def quote(text):
    for line in text.splitlines():
        print(f"       | {line}")


if __name__ == "__main__":
    main()
