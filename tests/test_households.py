"""家庭解析：补录生效、冲突只登记不修正、已发臂别锁定。"""

from __future__ import annotations

from trial_tools.households import HouseholdResolver

from ._fixtures import RecordsFixture


class HouseholdTests(RecordsFixture):
    def setUp(self) -> None:
        super().setUp()
        self.resolver = HouseholdResolver(self.data)

    def test_amendments_reassign_household_facts(self) -> None:
        # U005 补录到 F000；U110 补录到 F109
        self.assertEqual(self.resolver.student_household("U005"), "F000")
        self.assertEqual(self.resolver.student_household("U110"), "F109")
        f000 = self.resolver.households["F000"]
        self.assertEqual({m.student for m in f000.members}, {"U000", "U005", "U026"})

    def test_conflicted_households_keep_actual_exposure(self) -> None:
        conflicted = {h.household: h for h in self.resolver.conflicted_households()}
        self.assertEqual(set(conflicted), {"F000", "F109"})
        for household in conflicted.values():
            self.assertTrue(household.conflicted)
            self.assertIsNone(household.effective_adult_arm)
            self.assertEqual(set(household.adult_arms_sent), {0, 1})

    def test_clean_cross_class_household_is_not_conflicted(self) -> None:
        f003 = self.resolver.households["F003"]
        self.assertTrue(f003.cross_class)
        self.assertFalse(f003.conflicted)
        self.assertEqual(f003.effective_adult_arm, 1)

    def test_unsent_arm_does_not_create_conflict(self) -> None:
        # F000 的冲突来自两条已发记录；仅未发的 U000 不参与冲突判定。
        f000 = self.resolver.households["F000"]
        unsent = [m for m in f000.members if not m.sent]
        self.assertEqual([m.student for m in unsent], ["U000"])
        self.assertTrue(f000.conflicted)  # 冲突仍由已发凭证造成

    def test_tainted_class_scope(self) -> None:
        self.assertEqual(
            self.resolver.tainted_class_set(),
            {"C00", "C02", "C05", "C13", "C14"},
        )

    def test_sent_adult_arm_locks(self) -> None:
        locks = self.resolver.sent_adult_arm_lock()
        self.assertEqual(locks["F003"], 1)
        self.assertNotIn("F000", locks)  # 冲突家庭不产生锁定，进入 service_only
        self.assertNotIn("F109", locks)

    def test_amendment_does_not_rewrite_dispatch_voucher(self) -> None:
        member = self.resolver.members["U005"]
        self.assertEqual(member.resolved_household, "F000")
        self.assertEqual(member.registered_household, "F005")
        # 派发凭证保持原始臂别，补录不重新归组
        self.assertEqual(member.adult_arm, 1)
        self.assertTrue(member.amended)


if __name__ == "__main__":
    import unittest

    unittest.main()
