"""Design 2.4: a timeout means "unknown", not "failed", and no failure can pay twice."""

import unittest

from helia_gateway.gateway import HUMAN_QUEUE
from tests.support import LEAD, World


class ExecutionFailureTests(unittest.TestCase):
    def setUp(self):
        self.world = World()
        self.case, self.charge = self.world.refund_case("49.00")

    def propose(self):
        return self.world.propose_refund(self.case, self.charge)

    def timeouts(self):
        return self.world.audit.events(self.case, "timeout")

    def test_timeout_after_billing_committed_is_confirmed_not_retried(self):
        self.world.billing.refund_faults.append("timeout_after_commit")
        decision = self.propose()
        self.assertEqual((decision.status, decision.reason), ("executed", "confirmed after a timeout"))
        self.assertEqual(len(self.world.refunds(self.charge)), 1)

    def test_timeout_before_billing_committed_is_retried_safely(self):
        self.world.billing.refund_faults.append("timeout_before_commit")
        self.assertEqual(self.propose().status, "executed")
        self.assertEqual(len(self.world.refunds(self.charge)), 1)
        self.assertEqual(len(self.timeouts()), 1)

    def test_persistent_timeouts_fail_and_hand_over(self):
        self.world.billing.refund_faults.extend(["timeout_before_commit"] * 3)
        decision = self.propose()
        self.assertEqual(decision.status, "failed")
        self.assertEqual(self.world.refunds(self.charge), [])
        self.assertEqual(len(self.timeouts()), 3)  # first try plus two retries
        self.assertEqual(self.world.ticketing.get(self.case).assignee, HUMAN_QUEUE)

    def test_unknown_outcome_is_never_retried(self):
        self.world.billing.refund_faults.append("timeout_before_commit")
        self.world.billing.lookup_faults.append("unavailable")
        self.assertEqual(self.propose().status, "uncertain")
        self.assertEqual(self.world.ticketing.get(self.case).assignee, HUMAN_QUEUE)
        self.assertEqual(self.propose().status, "rejected")  # the agent can't try again
        self.assertEqual(self.world.refunds(self.charge), [])

    def test_unexpected_billing_error_counts_as_unknown(self):
        self.world.billing.refund_faults.append("connection_error_after_commit")
        self.assertEqual(self.propose().status, "uncertain")
        self.assertEqual(self.world.ticketing.get(self.case).assignee, HUMAN_QUEUE)

    def test_a_refund_of_unknown_outcome_still_uses_up_the_hourly_cap(self):
        for _ in range(19):  # $1,900 of the $2,000 cap
            case, charge = self.world.refund_case("100.00")
            self.world.propose_refund(case, charge)
        case, charge = self.world.refund_case("100.00")
        self.world.billing.refund_faults.append("timeout_before_commit")
        self.world.billing.lookup_faults.append("unavailable")
        self.assertEqual(self.world.propose_refund(case, charge).status, "uncertain")
        case, charge = self.world.refund_case("100.00")
        self.assertIn("hourly auto-refund cap", self.world.propose_refund(case, charge).reason)


class KillSwitchTests(unittest.TestCase):
    def setUp(self):
        self.world = World()

    def test_switched_off_tool_hands_new_cases_to_a_person(self):
        case, charge = self.world.refund_case("20.00")
        self.world.gateway.switch_off("issue_refund")
        self.assertEqual(self.world.propose_refund(case, charge).status, "rejected")
        self.assertEqual(self.world.ticketing.get(case).assignee, HUMAN_QUEUE)
        self.assertEqual(self.world.billing.refunds, [])

    def test_sweep_hands_over_waiting_refunds_of_a_switched_off_tool(self):
        case, charge = self.world.refund_case("250.00")
        self.world.propose_refund(case, charge)
        self.world.gateway.switch_off("issue_refund")
        self.assertEqual([d.status for d in self.world.gateway.sweep()], ["void"])
        self.assertEqual(self.world.ticketing.get(case).assignee, HUMAN_QUEUE)

    def test_switching_off_stops_a_refund_already_waiting_for_approval(self):
        case, charge = self.world.refund_case("250.00")
        pending = self.world.propose_refund(case, charge)
        self.world.gateway.switch_off("issue_refund")
        self.assertEqual(
            self.world.gateway.approve(LEAD, pending.action_id, pending.fingerprint).status, "void"
        )
        self.assertEqual(self.world.billing.refunds, [])


if __name__ == "__main__":
    unittest.main()
