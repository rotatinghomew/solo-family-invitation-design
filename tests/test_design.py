"""设计引擎：共同支持、不重合、家庭约束、服务保留、承诺名额与可重复性。"""

from __future__ import annotations

import collections

from trial_tools.design import CELLS, DesignEngine
from trial_tools.households import HouseholdResolver
from trial_tools.records import StudyData

from ._fixtures import RecordsFixture


class DesignTests(RecordsFixture):
    def setUp(self) -> None:
        super().setUp()
        self.resolver = HouseholdResolver(self.data)
        self.engine = DesignEngine(self.data, self.resolver)

    def test_current_audit_passes_common_support_and_no_confounding(self) -> None:
        audit = self.engine.audit_current()
        self.assertTrue(audit.common_support)
        self.assertFalse(audit.school_confounded)
        self.assertFalse(audit.slot_confounded)
        self.assertEqual(set(audit.cell_class_counts), set(CELLS))
        self.assertTrue(all(v > 0 for v in audit.cell_class_counts.values()))
        self.assertEqual(audit.selected_index, self.data.original_draw["selected_index"])
        self.assertAlmostEqual(sum(audit.probabilities), 1.0)

    def test_audit_marks_conflicted_scope(self) -> None:
        audit = self.engine.audit_current()
        self.assertEqual(set(audit.conflicted_households), {"F000", "F109"})
        self.assertEqual(
            set(audit.tainted_classes), {"C00", "C02", "C05", "C13", "C14"}
        )
        # 已发凭证数量冻结，未发清单单独给出
        self.assertEqual(audit.locked_sent, 136)
        self.assertEqual(len(audit.adjustable_unsent), 8)

    def test_next_round_plan_is_feasible_and_respects_rules(self) -> None:
        plan = self.engine.build_next_plan(seed=20261124)
        self.assertTrue(plan.feasible, plan.infeasibility_reasons)
        self.assertEqual(len(plan.batches), 4)
        self.assertAlmostEqual(sum(plan.probabilities), 1.0)

        chosen = plan.selected_map()
        cls = {c["class"]: c for c in self.data.classes}
        # 干净班级集合 = 全部班级去掉冲突波及班级
        clean = set(cls) - {"C00", "C02", "C05", "C13", "C14"}
        self.assertEqual(set(chosen), clean)

        # 每个学校、每个时段覆盖全部四组合
        by_school = collections.defaultdict(set)
        by_slot = collections.defaultdict(set)
        counts = collections.Counter()
        for klass, cell in chosen.items():
            by_school[cls[klass]["school"]].add(cell)
            by_slot[cls[klass]["slot"]].add(cell)
            counts[cell] += 1
        self.assertTrue(all(len(v) == 4 for v in by_school.values()))
        self.assertTrue(all(len(v) == 4 for v in by_slot.values()))
        self.assertTrue(all(v > 0 for v in counts.values()))

    def test_all_batches_individually_feasible(self) -> None:
        plan = self.engine.build_next_plan(seed=11)
        cls = {c["class"]: c for c in self.data.classes}
        for batch in plan.batches:
            schools = collections.defaultdict(set)
            slots = collections.defaultdict(set)
            for klass, cell in batch.items():
                schools[cls[klass]["school"]].add(cell)
                slots[cls[klass]["slot"]].add(cell)
            self.assertTrue(all(len(v) == 4 for v in schools.values()))
            self.assertTrue(all(len(v) == 4 for v in slots.values()))
            # 四组合共同支持
            self.assertEqual(set(batch.values()), set(CELLS))

    def test_conflicted_classes_kept_as_service_only(self) -> None:
        plan = self.engine.build_next_plan(seed=3)
        service = {r.klass for r in plan.service_only}
        self.assertEqual(service, {"C00", "C02", "C05", "C13", "C14"})
        # 服务保留但不进入随机化行
        randomized = {r.klass for r in plan.rows}
        self.assertFalse(service & randomized)
        # 不删家庭、不撤销通知：范围里保留受影响学生
        self.assertIn("U005", plan.non_comparable_scope["students_in_scope"])
        self.assertIn("U110", plan.non_comparable_scope["students_in_scope"])

    def test_adult_arm_lock_from_clean_cross_class_family(self) -> None:
        plan = self.engine.build_next_plan(seed=5)
        for batch in plan.batches:
            self.assertEqual(batch["C03"][1], 1)  # F003 已发家长臂别 1
            self.assertEqual(batch["C07"][1], 1)
        opportunities = self.engine.marginal_opportunities(plan)
        locked_mass = opportunities["C03"][(0, 0)] + opportunities["C03"][(1, 0)]
        self.assertEqual(locked_mass, 0.0)

    def test_confirmed_seats_are_preserved(self) -> None:
        plan = self.engine.build_next_plan(seed=9)
        capacities = self.engine.capacities
        for row in plan.rows:
            cap = capacities[row.klass]
            self.assertEqual(row.confirmed_seats, cap.confirmed)
            self.assertEqual(row.free_seats, cap.available - cap.confirmed)
            self.assertGreaterEqual(row.free_seats, 0)

    def test_reproducible_with_same_seed(self) -> None:
        first = self.engine.build_next_plan(seed=42)
        second = self.engine.build_next_plan(seed=42)
        self.assertEqual(first.batches, second.batches)
        self.assertEqual(first.selected_index, second.selected_index)

    def test_infeasible_when_a_school_cannot_cover_four_cells(self) -> None:
        import random

        cls = {c["class"]: c for c in self.data.classes}
        # 构造干净集合：S0 只保留 2 个班（无法覆盖四组合），其余学校正常
        s0_two = [c for c in self.engine.capacities if cls[c]["school"] == "S0"][:2]
        others = [c for c in self.engine.capacities if cls[c]["school"] != "S0"]
        clean = s0_two + others
        batches = self.engine._sample_feasible_batches(
            clean_classes=clean,
            class_adult_lock={},
            rng=random.Random(1),
            n_batches=4,
            balance_tolerance=2,
        )
        self.assertEqual(batches, ())

    def test_new_class_joins_next_round_randomization(self) -> None:
        expanded = self.engine.add_class_capacity("C24", "S2", "T0", 8)
        self.assertIn("C24", expanded.capacities)
        plan = expanded.build_next_plan(seed=2)
        self.assertTrue(plan.feasible)
        chosen = plan.selected_map()
        self.assertIn("C24", chosen)
        # 新增班级仍受学校/时段不重合约束
        cls = {c["class"]: c for c in self.data.classes}
        for batch in plan.batches:
            s2_cells = {cell for k, cell in batch.items() if k == "C24" or cls.get(k, {}).get("school") == "S2"}
            self.assertEqual(s2_cells, set(CELLS))

    def test_cannot_overwrite_existing_class_capacity(self) -> None:
        with self.assertRaises(ValueError):
            self.engine.add_class_capacity("C00", "S0", "T0", 5)

    def test_newly_discovered_sibling_pair_creates_lock_or_service_scope(self) -> None:
        # 无历史冲突的新连接：合并进连接表，对应班级得到家长臂别约束入口
        merged = self.engine.register_new_sibling_link("F200", ("C02", "C08"))
        self.assertEqual(merged["F200"], ("C02", "C08"))
        self.assertIn("F000", merged)  # 既有连接不丢失
        # 未知班级必须被拒绝
        with self.assertRaises(ValueError):
            self.engine.register_new_sibling_link("F201", ("C02", "C99"))

    def test_sibling_link_extending_conflicted_family_widens_service_scope(self) -> None:
        # F000 已有冲突；把新发现的另一个班并入后，波及范围扩大而不是"修正"组别
        merged = self.engine.register_new_sibling_link("F000", ("C00", "C02", "C05", "C08"))
        self.assertEqual(merged["F000"], ("C00", "C02", "C05", "C08"))
        plan = self.engine.build_next_plan(links=merged, seed=4)
        service = {r.klass for r in plan.service_only}
        self.assertIn("C08", service)
        randomized = {r.klass for r in plan.rows}
        self.assertNotIn("C08", randomized)

    def test_new_sibling_link_into_tainted_class_propagates_service_scope(self) -> None:
        merged = self.engine.register_new_sibling_link("F200", ("C02", "C08"))
        plan = self.engine.build_next_plan(links=merged, seed=4)
        service = {r.klass for r in plan.service_only}
        # C02 已被 F000 污染；同家庭的 C08 不能再支持完整家长因素比较
        self.assertIn("C02", service)
        self.assertIn("C08", service)
        self.assertEqual(
            plan.household_constraints["F200"]["type"], "linked_service_only"
        )

    def test_clean_new_sibling_link_forces_equal_adult_arm(self) -> None:
        merged = self.engine.register_new_sibling_link("F201", ("C08", "C16"))
        plan = self.engine.build_next_plan(links=merged, seed=4)
        self.assertTrue(plan.feasible)
        for batch in plan.batches:
            self.assertEqual(batch["C08"][1], batch["C16"][1])
        self.assertIsNone(plan.household_constraints["F201"]["locked_adult_arm"])


if __name__ == "__main__":
    import unittest

    unittest.main()
