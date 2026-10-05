"""Design 2.3, "Replies": commitments only from templates, free text checked, graders can only hold."""

import unittest

from helia_gateway.common import AGENT
from tests.support import LEAD, World


class ReplyTests(unittest.TestCase):
    def setUp(self):
        self.world = World()
        self.case, self.charge = self.world.refund_case("49.00")
        self.refund = self.world.propose_refund(self.case, self.charge)

    def send(self, *blocks, case=None, world=None):
        return (world or self.world).gateway.send_reply(AGENT, case or self.case, list(blocks))

    def assert_blocked(self, decision, why):
        self.assertEqual(decision.status, "rejected")
        self.assertIn(why, decision.reason)
        self.assertEqual(self.world.ticketing.get(self.case).replies, [])

    def test_commitment_is_filled_from_the_confirmed_refund(self):
        decision = self.send(
            {"text": "Sorry about the double charge."}, {"commitment": self.refund.action_id}
        )
        self.assertEqual(decision.status, "sent")
        self.assertIn(
            f"49.00 USD to your original payment method (ref {self.refund.result['refund_id']})",
            decision.result["text"],
        )

    def test_amount_in_free_text_is_blocked(self):
        self.assert_blocked(self.send({"text": "Your $49 is on its way back."}), "an amount")

    def test_promise_in_free_text_is_blocked(self):
        self.assert_blocked(self.send({"text": "Don't worry, it will be processed today."}), "a promise")

    def test_deadline_in_free_text_is_blocked(self):
        self.assert_blocked(self.send({"text": "You'll hear from us within 2 days."}), "a date or deadline")

    def test_pending_action_gets_the_holding_template_not_a_confirmation(self):
        case, charge = self.world.refund_case("1200.00", request_type="downgrade_with_refund")
        pending = self.world.propose_refund(case, charge, "cancelled_within_14_days")
        decision = self.send({"commitment": pending.action_id}, case=case)
        self.assertEqual(decision.status, "sent")
        self.assertIn("reviews it", decision.result["text"])
        self.assertNotIn("1200", decision.result["text"])

    def test_cannot_confirm_an_action_that_did_not_happen(self):
        case, charge = self.world.refund_case("250.00")
        pending = self.world.propose_refund(case, charge)
        self.world.billing.manual_refund(charge, "50.00")  # the amount changes, so approving voids it
        self.world.gateway.approve(LEAD, pending.action_id, pending.fingerprint)
        decision = self.send({"commitment": pending.action_id}, case=case)
        self.assertIn("it is void, not confirmed", decision.reason)

    def test_agent_cannot_reply_on_a_ticket_a_person_took(self):
        self.world.ticketing.assign(self.case, "u_support")
        decision = self.send({"text": "Thanks for your patience."})
        self.assertEqual(decision.reason, "ticket is not assigned to the agent")

    def test_cannot_confirm_another_cases_action(self):
        other_case, other_charge = self.world.refund_case("20.00")
        other = self.world.propose_refund(other_case, other_charge)
        self.assert_blocked(self.send({"commitment": other.action_id}), "isn't on this case")

    def test_unapproved_link_is_blocked(self):
        self.assert_blocked(
            self.send({"text": "Reset it here: https://helia-support.example.net/reset"}), "isn't approved"
        )

    def test_help_centre_link_is_allowed(self):
        decision = self.send(
            {"text": "The steps are here: https://help.helia.example/articles/RF-14", "cites": ["RF-14"]}
        )
        self.assertEqual(decision.status, "sent")

    def test_citation_must_exist(self):
        self.assert_blocked(
            self.send({"text": "Our policy allows this.", "cites": ["RF-99"]}), "doesn't exist: RF-99"
        )

    def test_other_customers_email_is_blocked(self):
        self.assert_blocked(
            self.send({"text": "We also wrote to someone@else.example about it."}), "isn't this customer's"
        )

    def test_grader_can_hold_a_reply_for_a_person(self):
        world = World(reply_graders=[lambda text: "claim not supported by the cited article"])
        case, charge = world.refund_case("49.00")
        decision = self.send({"text": "Thanks for your patience."}, case=case, world=world)
        self.assertEqual(decision.status, "pending_approval")
        self.assertEqual(world.ticketing.get(case).replies, [])

    def test_grader_cannot_release_a_blocked_reply(self):
        world = World(reply_graders=[lambda text: None])  # a grader that sees no problem
        case, charge = world.refund_case("49.00")
        decision = self.send({"text": "Your $49 is on its way back."}, case=case, world=world)
        self.assertEqual(decision.status, "rejected")


if __name__ == "__main__":
    unittest.main()
