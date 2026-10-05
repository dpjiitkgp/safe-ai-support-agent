"""Design 2.5 and team standard 3.2: an incomplete tool, or an auto limit above a ceiling, never loads."""

import unittest

from helia_gateway.registry import Registry, RegistryError
from tests.support import POLICY_DIR, policy_dicts


class RegistryTests(unittest.TestCase):
    def test_shipped_policy_loads(self):
        registry = Registry.load(POLICY_DIR)
        self.assertIn("issue_refund", registry.tools)
        self.assertIn("refunds-over-1000-need-finance", [c.id for c in registry.ceilings_for("issue_refund")])

    def test_tool_missing_a_required_field_is_refused(self):
        tools, ceilings = policy_dicts()
        del tools["issue_refund"]["undo"]
        with self.assertRaisesRegex(RegistryError, "issue_refund: missing undo"):
            Registry.from_dicts(tools, ceilings)

    def test_auto_limit_above_a_finance_ceiling_is_refused(self):
        tools, ceilings = policy_dicts()
        tools["issue_refund"]["auto_approve"]["max_amount"] = 1500.00
        with self.assertRaisesRegex(
            RegistryError, "above Finance/Legal ceiling 'refunds-over-1000-need-finance'"
        ):
            Registry.from_dicts(tools, ceilings)

    def test_unknown_auto_condition_is_refused(self):
        tools, ceilings = policy_dicts()
        tools["issue_refund"]["auto_approve"]["vip_customers_always"] = True
        with self.assertRaisesRegex(RegistryError, "unknown auto-approve condition"):
            Registry.from_dicts(tools, ceilings)

    def test_tier_3_auto_rule_must_cap_the_amount(self):
        tools, ceilings = policy_dicts()
        del tools["issue_refund"]["auto_approve"]["max_amount"]
        with self.assertRaisesRegex(RegistryError, "must set max_amount"):
            Registry.from_dicts(tools, ceilings)

    def test_ceiling_must_belong_to_finance_or_legal(self):
        tools, ceilings = policy_dicts()
        ceilings["ceiling"][0]["owner"] = "payments-team"
        with self.assertRaisesRegex(RegistryError, "owner must be finance or legal"):
            Registry.from_dicts(tools, ceilings)

    def test_rules_version_changes_when_a_rule_changes(self):
        tools, ceilings = policy_dicts()
        before = Registry.from_dicts(tools, ceilings).version
        tools["issue_refund"]["auto_approve"]["max_amount"] = 50.00
        self.assertNotEqual(before, Registry.from_dicts(tools, ceilings).version)


if __name__ == "__main__":
    unittest.main()
