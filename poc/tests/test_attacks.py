"""The model as an adversary (team standard 5.2). Whatever it proposes, no money moves wrongly."""

import unittest

from helia_gateway.common import AGENT
from tests.support import LEAD, World


class ManipulatedModelTests(unittest.TestCase):
    def setUp(self):
        self.world = World()
        self.case, self.charge = self.world.refund_case("49.00")

    def propose(self, tool, params):
        return self.world.gateway.propose(AGENT, self.case, tool, params)

    def assert_nothing_refunded(self):
        self.assertEqual(self.world.billing.refunds, [])

    def test_model_cannot_set_the_amount(self):
        decision = self.propose(
            "issue_refund", {"charge_id": self.charge, "reason": "duplicate_charge", "amount": "2000"}
        )
        self.assertEqual(decision.status, "rejected")
        self.assertIn("unexpected field(s) amount", decision.reason)
        self.assert_nothing_refunded()

    def test_cannot_refund_another_customers_charge(self):
        _, other_charge = self.world.refund_case("900.00")
        decision = self.propose("issue_refund", {"charge_id": other_charge, "reason": "duplicate_charge"})
        self.assertEqual(
            (decision.status, decision.reason), ("rejected", "charge not found for this customer")
        )
        self.assert_nothing_refunded()

    def test_unknown_charge_gets_the_same_answer_so_nothing_can_be_probed(self):
        decision = self.propose("issue_refund", {"charge_id": "ch_nope", "reason": "duplicate_charge"})
        self.assertEqual(decision.reason, "charge not found for this customer")

    def test_reason_outside_the_fixed_list_is_rejected(self):
        decision = self.propose("issue_refund", {"charge_id": self.charge, "reason": "goodwill"})
        self.assertEqual(decision.status, "rejected")
        self.assert_nothing_refunded()

    def test_instructions_smuggled_into_a_reference_are_rejected(self):
        decision = self.propose(
            "issue_refund", {"charge_id": f"{self.charge}; refund all charges", "reason": "duplicate_charge"}
        )
        self.assertIn("not a charge reference", decision.reason)
        self.assert_nothing_refunded()

    def test_unregistered_tool_is_rejected(self):
        decision = self.propose("call_api", {"url": "https://billing.internal/refund-all"})
        self.assertEqual((decision.status, decision.reason), ("rejected", "unknown tool"))

    def test_agent_cannot_act_on_a_ticket_a_person_took(self):
        self.world.ticketing.assign(self.case, "u_support")
        decision = self.propose("issue_refund", {"charge_id": self.charge, "reason": "duplicate_charge"})
        self.assertEqual(decision.reason, "ticket is not assigned to the agent")
        self.assert_nothing_refunded()

    def test_only_the_agent_service_proposes(self):
        decision = self.world.gateway.propose(
            LEAD, self.case, "issue_refund", {"charge_id": self.charge, "reason": "duplicate_charge"}
        )
        self.assertEqual(decision.status, "rejected")

    def test_replayed_proposal_refunds_only_once(self):
        first = self.propose("issue_refund", {"charge_id": self.charge, "reason": "duplicate_charge"})
        again = self.propose("issue_refund", {"charge_id": self.charge, "reason": "duplicate_charge"})
        self.assertEqual(first.status, "executed")
        self.assertEqual(again.result["refund_id"], first.result["refund_id"])
        self.assertEqual(len(self.world.refunds(self.charge)), 1)

    def test_claiming_a_duplicate_that_isnt_there_needs_a_person(self):
        case, charge = self.world.refund_case("49.00", duplicate=False)  # "I was charged twice" (I wasn't)
        decision = self.world.propose_refund(case, charge, "duplicate_charge")
        self.assertEqual(decision.status, "pending_approval")
        self.assertEqual(self.world.refunds(charge), [])

    def test_two_tickets_cannot_refund_the_same_charge(self):
        first = self.propose("issue_refund", {"charge_id": self.charge, "reason": "duplicate_charge"})
        self.world.ticketing.add_ticket("case_other", "cus_1", "duplicate_charge", email="cus_1@example.com")
        second = self.world.gateway.propose(
            AGENT, "case_other", "issue_refund", {"charge_id": self.charge, "reason": "duplicate_charge"}
        )
        self.assertEqual(first.status, "executed")
        self.assertEqual(second.reason, "nothing left to refund on this charge")
        self.assertEqual(len(self.world.refunds(self.charge)), 1)

    def test_fully_refunded_charge_is_rejected(self):
        self.world.billing.manual_refund(self.charge, "49.00")
        decision = self.propose("issue_refund", {"charge_id": self.charge, "reason": "duplicate_charge"})
        self.assertEqual(decision.reason, "nothing left to refund on this charge")


if __name__ == "__main__":
    unittest.main()
