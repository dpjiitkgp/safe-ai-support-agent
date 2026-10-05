"""Design 2.3: who decides what for refunds, tested at the edges of each rule."""

import dataclasses
import unittest
from decimal import Decimal

from helia_gateway import policy
from helia_gateway.registry import Ceiling, Registry
from tests.support import POLICY_DIR, World


class RefundRuleTests(unittest.TestCase):
    def setUp(self):
        self.world = World()

    def propose(self, amount, **kwargs):
        reason = kwargs.pop("reason", "duplicate_charge")
        case, charge = self.world.refund_case(amount, **kwargs)
        return self.world.propose_refund(case, charge, reason), charge

    def test_100_runs_alone(self):
        decision, charge = self.propose("100.00")
        self.assertEqual(decision.status, "executed")
        self.assertEqual(decision.result["amount"], Decimal("100.00"))
        self.assertEqual(len(self.world.refunds(charge)), 1)

    def test_100_01_waits_for_a_support_lead(self):
        decision, charge = self.propose("100.01")
        self.assertEqual(decision.status, "pending_approval")
        self.assertEqual(decision.needs_roles, ("support_lead",))
        self.assertEqual(self.world.refunds(charge), [])

    def test_over_1000_needs_a_lead_and_finance(self):
        decision, _ = self.propose("1000.01")
        self.assertEqual(decision.needs_roles, ("support_lead", "finance"))
        self.assertIn("refunds-over-1000-need-finance", decision.reason)

    def test_reason_other_waits(self):
        decision, _ = self.propose("20.00", reason="other")
        self.assertEqual(decision.status, "pending_approval")

    def test_old_charge_waits(self):
        decision, _ = self.propose("20.00", days_ago=45)
        self.assertIn("45 days old", decision.reason)

    def test_recent_refund_for_the_same_customer_waits(self):
        first, _ = self.propose("20.00", customer="cus_repeat")
        self.assertEqual(first.status, "executed")
        self.world.clock.advance(days=10)
        second, _ = self.propose("20.00", customer="cus_repeat")
        self.assertEqual(second.status, "pending_approval")
        self.assertIn("had a refund 10 days ago", second.reason)

    def test_hourly_cap_stops_a_run_of_auto_refunds(self):
        for _ in range(20):  # 20 x $100 reaches the $2,000 cap exactly
            self.assertEqual(self.propose("100.00")[0].status, "executed")
        decision, _ = self.propose("100.00")
        self.assertEqual(decision.status, "pending_approval")
        self.assertIn("hourly auto-refund cap", decision.reason)
        self.world.clock.advance(hours=1, minutes=1)
        self.assertEqual(self.propose("100.00")[0].status, "executed")

    def test_tool_not_expected_for_the_request_type_goes_to_a_person(self):
        decision, _ = self.propose("20.00", request_type="billing_question")
        self.assertEqual(decision.status, "pending_approval")
        self.assertIn("not expected for request type 'billing_question'", decision.reason)

    def test_duplicate_reason_needs_a_real_duplicate(self):
        decision, charge = self.propose("49.00", duplicate=False)
        self.assertEqual(decision.status, "pending_approval")
        self.assertIn("billing data doesn't confirm the reason 'duplicate_charge'", decision.reason)
        self.assertEqual(self.world.refunds(charge), [])

    def test_cancellation_reason_needs_a_charge_from_the_last_14_days(self):
        recent, _ = self.propose("40.00", reason="cancelled_within_14_days", days_ago=10, duplicate=False)
        older, _ = self.propose("40.00", reason="cancelled_within_14_days", days_ago=25, duplicate=False)
        self.assertEqual(recent.status, "executed")
        self.assertIn("doesn't confirm the reason", older.reason)

    def test_service_outage_always_goes_to_a_person(self):
        decision, _ = self.propose("20.00", reason="service_outage")
        self.assertEqual(decision.status, "pending_approval")


class CeilingTests(unittest.TestCase):
    def test_a_ceiling_beats_any_auto_rule(self):
        # The registry refuses an auto limit above a ceiling. This checks the second line of
        # defence: even a hand-built, too-generous auto rule can't let a $600 refund run alone.
        tool = Registry.load(POLICY_DIR).tool("issue_refund")
        generous = dataclasses.replace(
            tool, auto_approve={**tool.auto_approve, "max_amount": Decimal("5000.00")}
        )
        ceiling = Ceiling("test-ceiling", "issue_refund", "finance", Decimal("500.00"), ("support_lead",))
        facts = policy.Facts(
            amount=Decimal("600.00"),
            reason="duplicate_charge",
            reason_confirmed=True,
            charge_age_days=1,
            days_since_last_refund=None,
            request_type="duplicate_charge",
            auto_total_last_hour=Decimal("0.00"),
        )
        verdict = policy.evaluate(generous, [ceiling], facts)
        self.assertFalse(verdict.runs_alone)
        self.assertIn("support_lead", verdict.needs_roles)


if __name__ == "__main__":
    unittest.main()
