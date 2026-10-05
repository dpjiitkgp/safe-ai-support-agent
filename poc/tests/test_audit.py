"""Design 2.5 and team standard 4: every step is logged, tampering is detectable, and the log holds
references rather than personal data."""

import json
import unittest

from helia_gateway.common import AGENT, sha256_hex
from tests.support import LEAD, World


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.world = World()

    def test_every_step_of_an_auto_refund_is_logged(self):
        case, charge = self.world.refund_case("49.00")
        self.world.propose_refund(case, charge)
        self.assertEqual(
            [e["type"] for e in self.world.audit.events(case)], ["proposed", "decided", "executed"]
        )

    def test_decision_records_the_rules_version(self):
        case, charge = self.world.refund_case("49.00")
        self.world.propose_refund(case, charge)
        decided = self.world.audit.events(case, "decided")[0]
        self.assertEqual(decided["rules_version"], self.world.registry.version)

    def test_approval_records_who_in_which_role_and_what_they_saw(self):
        case, charge = self.world.refund_case("250.00")
        pending = self.world.propose_refund(case, charge)
        self.world.gateway.approve(LEAD, pending.action_id, pending.fingerprint)
        approved = self.world.audit.events(case, "approved")[0]
        self.assertEqual(
            (approved["actor"], approved["role"], approved["fingerprint"]),
            ("u_lead", "support_lead", pending.fingerprint),
        )

    def test_chain_detects_an_edited_event(self):
        case, charge = self.world.refund_case("49.00")
        self.world.propose_refund(case, charge)
        self.assertIsNone(self.world.audit.verify())
        self.world.audit._events[1]["computed"]["amount"] = "1.00"  # someone edits a row
        self.assertEqual(self.world.audit.verify(), 1)

    def test_chain_detects_a_deleted_event(self):
        case, charge = self.world.refund_case("49.00")
        self.world.propose_refund(case, charge)
        del self.world.audit._events[1]
        self.assertEqual(self.world.audit.verify(), 1)

    def test_chain_detects_a_relinked_event(self):
        case, charge = self.world.refund_case("49.00")
        self.world.propose_refund(case, charge)
        events = self.world.audit._events
        del events[1]  # remove an event, then make the next one look self-consistent again
        events[1]["seq"] = 1
        events[1]["hash"] = sha256_hex({k: v for k, v in events[1].items() if k != "hash"})
        self.assertEqual(self.world.audit.verify(), 1)

    def test_a_rewritten_log_passes_the_chain_but_not_an_anchor(self):
        case, charge = self.world.refund_case("49.00")
        self.world.propose_refund(case, charge)
        anchor = self.world.audit.anchor()  # published somewhere the log's writers can't change
        events = self.world.audit._events
        events[1]["computed"]["amount"] = "1.00"
        for previous, event in zip(
            events, events[1:], strict=False
        ):  # an insider recomputes every later hash
            event["prev_hash"] = previous["hash"]
            event["hash"] = sha256_hex({k: v for k, v in event.items() if k != "hash"})
        self.assertIsNone(self.world.audit.verify())  # the chain alone can't tell
        self.assertEqual(self.world.audit.verify(anchors=[anchor]), anchor[0])

    def test_rejected_model_output_is_logged_as_a_hash(self):
        case, charge = self.world.refund_case("49.00")
        self.world.gateway.propose(
            AGENT,
            case,
            "issue_refund",
            {"charge_id": charge, "reason": "duplicate_charge", "card": "4111 1111 1111 1111"},
        )
        log = json.dumps(self.world.audit.events())
        self.assertNotIn("4111", log)
        self.assertIn("params_sha256", log)

    def test_log_holds_references_not_personal_data(self):
        case, charge = self.world.refund_case("49.00")
        refund = self.world.propose_refund(case, charge)
        text = f"A copy of the receipt is on its way to {case.replace('case', 'cus')}@example.com."
        sent = self.world.gateway.send_reply(AGENT, case, [{"text": text}, {"commitment": refund.action_id}])
        self.assertEqual(sent.status, "sent")
        log = json.dumps(self.world.audit.events())
        self.assertNotIn("@example.com", log)
        self.assertIn("text_sha256", log)


if __name__ == "__main__":
    unittest.main()
