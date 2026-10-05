"""Design 2.3, "where it's enforced": approvals carry a person's identity and role, are bound to the
exact action, go void when the world changes, and expire."""

import unittest

from helia_gateway.common import AGENT, Principal, human
from helia_gateway.gateway import HUMAN_QUEUE
from helia_gateway.registry import Registry
from tests.support import FINANCE, LEAD, SUPPORT_AGENT, World, policy_dicts


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.world = World()
        self.gw = self.world.gateway
        self.case, self.charge = self.world.refund_case("250.00")
        self.pending = self.world.propose_refund(self.case, self.charge)

    def approve(self, who, fingerprint=None):
        return self.gw.approve(who, self.pending.action_id, fingerprint or self.pending.fingerprint)

    def test_support_lead_approval_executes(self):
        decision = self.approve(LEAD)
        self.assertEqual(decision.status, "executed")
        self.assertEqual(len(self.world.refunds(self.charge)), 1)

    def test_agent_cannot_approve_its_own_action(self):
        # Even if the agent's service identity were misconfigured with an approver role.
        misconfigured_agent = Principal(AGENT.id, "agent", frozenset({"support_lead"}))
        decision = self.approve(misconfigured_agent)
        self.assertEqual(decision.reason, "only a person can approve; the agent can only propose")
        self.assertEqual(self.world.refunds(self.charge), [])

    def test_wrong_role_is_denied(self):
        self.assertEqual(self.approve(SUPPORT_AGENT).reason, "needs support_lead")

    def test_approval_must_match_what_the_approver_saw(self):
        decision = self.approve(LEAD, fingerprint="0" * 64)
        self.assertEqual(decision.status, "denied")
        self.assertEqual(self.world.refunds(self.charge), [])

    def test_approval_is_void_if_the_amount_changed(self):
        self.world.billing.manual_refund(self.charge, "50.00")  # someone refunded part of it meanwhile
        decision = self.approve(LEAD)
        self.assertEqual(decision.status, "void")
        self.assertEqual(len(self.world.refunds(self.charge)), 1)  # only the manual one

    def test_approval_is_void_if_a_person_took_the_ticket(self):
        self.world.ticketing.assign(self.case, "u_support")
        self.assertEqual(self.approve(LEAD).status, "void")

    def test_approval_expires_after_24_hours_and_the_case_goes_to_a_person(self):
        self.world.clock.advance(hours=25)
        self.assertEqual(self.approve(LEAD).status, "expired")
        self.assertEqual(self.world.ticketing.get(self.case).assignee, HUMAN_QUEUE)

    def test_sweep_expires_approvals_nobody_acted_on(self):
        self.world.clock.advance(hours=25)
        outcomes = self.gw.sweep()  # runs on a schedule in production
        self.assertEqual([d.status for d in outcomes], ["expired"])
        self.assertEqual(self.world.ticketing.get(self.case).assignee, HUMAN_QUEUE)

    def test_proposing_again_after_expiry_reports_it_instead_of_still_pending(self):
        self.world.clock.advance(hours=30)
        again = self.world.propose_refund(self.case, self.charge)
        self.assertEqual(again.status, "expired")
        self.assertEqual(self.world.ticketing.get(self.case).assignee, HUMAN_QUEUE)

    def test_removing_a_tool_voids_waiting_actions_without_crashing(self):
        tools, ceilings = policy_dicts()
        del tools["issue_refund"]
        self.gw.use_rules(Registry.from_dicts(tools, {}))
        decision = self.approve(LEAD)
        self.assertEqual((decision.status, decision.reason), ("void", "the tool is no longer registered"))

    def test_decline_of_an_unknown_action_is_rejected(self):
        self.assertEqual(self.gw.decline(LEAD, "act_nope", "?").status, "rejected")

    def test_decline_leaves_a_ticket_a_person_already_owns(self):
        self.world.ticketing.assign(self.case, "u_support")
        self.gw.decline(LEAD, self.pending.action_id, "not eligible")
        self.assertEqual(self.world.ticketing.get(self.case).assignee, "u_support")

    def test_a_decline_binds_the_agent(self):
        self.gw.decline(LEAD, self.pending.action_id, "looks like fraud")
        again = self.world.propose_refund(self.case, self.charge)
        self.assertEqual(again.status, "rejected")
        self.assertEqual(self.world.ticketing.get(self.case).assignee, LEAD.id)
        self.assertEqual(self.world.refunds(self.charge), [])

    def test_waiting_refund_is_void_if_the_rules_tighten(self):
        tools, ceilings = policy_dicts()
        ceilings["ceiling"].append(
            {
                "id": "refunds-over-200-need-finance",
                "tool": "issue_refund",
                "owner": "finance",
                "amount_above": 200.00,
                "require_roles": ["support_lead", "finance"],
            }
        )
        self.gw.use_rules(Registry.from_dicts(tools, ceilings))
        decision = self.approve(LEAD)  # the $250 refund was proposed when a lead alone was enough
        self.assertEqual(decision.status, "void")
        self.assertIn("rules changed", decision.reason)
        self.assertEqual(self.world.refunds(self.charge), [])

    def test_declined_action_never_runs(self):
        self.gw.decline(LEAD, self.pending.action_id, "customer used Pro features heavily")
        self.assertEqual(self.approve(LEAD).status, "denied")
        self.assertEqual(self.world.refunds(self.charge), [])

    def test_large_refund_needs_two_different_people(self):
        case, charge = self.world.refund_case("1200.00", request_type="downgrade_with_refund")
        pending = self.world.propose_refund(case, charge, "cancelled_within_14_days")
        both_roles = human("u_both", "support_lead", "finance")

        first = self.gw.approve(both_roles, pending.action_id, pending.fingerprint)
        self.assertEqual((first.status, first.needs_roles), ("pending_approval", ("finance",)))
        second = self.gw.approve(both_roles, pending.action_id, pending.fingerprint)
        self.assertEqual(second.reason, "one person can't fill two approval roles")
        third = self.gw.approve(FINANCE, pending.action_id, pending.fingerprint)
        self.assertEqual(third.status, "executed")
        self.assertEqual(len(self.world.refunds(charge)), 1)


if __name__ == "__main__":
    unittest.main()
