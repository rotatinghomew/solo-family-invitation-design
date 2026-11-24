"""ITT 两因素分析与监测门槛：按原随机安排、整群相关、阶段不报有效。"""

from __future__ import annotations

import math

from trial_tools.analysis import ESTIMANDS, FactorialAnalysis, ols
from trial_tools.households import HouseholdResolver
from trial_tools.monitor import Monitor
from trial_tools.outcomes import OutcomeBuilder
from trial_tools.records import StudyData

from ._fixtures import RecordsFixture


class AnalysisTests(RecordsFixture):
    def setUp(self) -> None:
        super().setUp()
        self.resolver = HouseholdResolver(self.data)
        self.builder = OutcomeBuilder(self.data, self.resolver, window_days=12)
        self.analyzer = FactorialAnalysis()

    def _monitor(self) -> Monitor:
        study = self.data.study
        return Monitor(
            study["look_days"],
            study["look_alpha"],
            study["familywise_alpha"],
            n_comparisons=4,
        )

    def test_student_model_runs_on_mature_observed_only(self) -> None:
        look = self.builder.build(24)
        model = self.analyzer.analyze_students(look)
        # 样本量严格等于已观察（成熟且有有效来源）且不在污染班级的学生数
        expected = sum(
            1
            for r in look.students
            if r.status == "observed" and not r.tainted
        )
        self.assertEqual(model.n_obs, expected)
        self.assertEqual(len(model.results), len(ESTIMANDS) + 1)  # 含一个多向核对

    def test_analysis_uses_assigned_not_attended_comparison(self) -> None:
        # ITT：按冻结臂别分组，未到访/拒绝都按各自臂别保留，不做参与者筛选
        look = self.builder.build(24)
        model = self.analyzer.analyze_students(look)
        keys = set(model.cell_rates)
        self.assertEqual(keys, {(0, 0), (0, 1), (1, 0), (1, 1)})
        total = sum(v["n"] for v in model.cell_rates.values())
        self.assertEqual(total, model.n_obs)

    def test_tainted_scope_excluded_by_default_retained_on_request(self) -> None:
        look = self.builder.build(24)
        strict = self.analyzer.analyze_students(look, include_tainted=False)
        relaxed = self.analyzer.analyze_students(look, include_tainted=True)
        self.assertTrue(strict.excluded_tainted)
        self.assertGreaterEqual(relaxed.n_obs, strict.n_obs)

    def test_household_model_excludes_conflicted_and_counts_families_once(self) -> None:
        look = self.builder.build(24)
        model = self.analyzer.analyze_households(look)
        self.assertIn("F000", model.excluded_tainted)
        self.assertIn("F109", model.excluded_tainted)
        self.assertLessEqual(model.n_obs, len(look.households) - 2)
        total = sum(v["n"] for v in model.cell_rates.values())
        self.assertEqual(total, model.n_obs)

    def test_contrast_se_are_finite_and_ci_covers_estimate(self) -> None:
        look = self.builder.build(24)
        model = self.analyzer.analyze_students(look)
        for contrast in model.results[: len(ESTIMANDS)]:
            self.assertTrue(math.isfinite(contrast.se))
            self.assertLessEqual(contrast.ci_low, contrast.estimate + 1e-12)
            self.assertGreaterEqual(contrast.ci_high, contrast.estimate - 1e-12)
            self.assertTrue(math.isfinite(contrast.p_value))

    def test_interpretation_of_factorial_coefficients(self) -> None:
        # 构造完全平衡的合成结果，核对系数解释
        import numpy as np

        cells = [(0, 0), (0, 1), (1, 0), (1, 1)] * 4
        y = np.array([0.0, 0.2, 0.3, 0.9] * 4)
        X = self.analyzer._design_matrix(cells)
        beta = ols(X, y)
        # b0=对照均值0, bA=0.2, bS=0.3, bSA=0.9-0.3-0.2=0.4
        self.assertAlmostEqual(beta[0], 0.0, places=10)
        self.assertAlmostEqual(beta[1], 0.3, places=10)
        self.assertAlmostEqual(beta[2], 0.2, places=10)
        self.assertAlmostEqual(beta[3], 0.4, places=10)

    def test_randomization_check_uses_all_frozen_alternatives(self) -> None:
        look = self.builder.build(24)
        alternatives = [
            {row["class"]: (row["student_arm"], row["adult_arm"]) for row in alt}
            for alt in self.data.original_draw["alternatives"]
        ]
        result = self.analyzer.randomization_check(look, alternatives)
        self.assertEqual(result["feasible_alternatives"], 4)
        self.assertIn("randomization_p_two_sided", result)

    def test_interim_look_can_never_declare_efficacy(self) -> None:
        look = self.builder.build(16)
        model = self.analyzer.analyze_students(look)
        monitor = self._monitor()
        decision = monitor.evaluate(1, 16, model.results[: len(ESTIMANDS)])
        self.assertFalse(decision.is_final)
        self.assertEqual(decision.per_comparison_alpha, 0.012 / 4)
        for item in decision.decisions:
            self.assertNotIn("可报有效", item["verdict"])

    def test_final_look_uses_registered_threshold(self) -> None:
        look = self.builder.build(24)
        model = self.analyzer.analyze_students(look)
        monitor = self._monitor()
        decision = monitor.evaluate(2, 24, model.results[: len(ESTIMANDS)])
        self.assertTrue(decision.is_final)
        self.assertEqual(decision.per_comparison_alpha, 0.03 / 4)
        # 本演练最终没有任何对比达到 0.0075
        self.assertFalse(any(item["significant"] for item in decision.decisions))

    def test_alpha_spending_respects_familywise_budget(self) -> None:
        monitor = self._monitor()
        self.assertLessEqual(
            sum(monitor.look_alpha), monitor.familywise_alpha + 1e-12
        )
        plan = monitor.plan
        self.assertEqual(len(plan.per_comparison_alpha), 3)

    def test_monitor_rejects_overspent_budget(self) -> None:
        with self.assertRaises(ValueError):
            Monitor([1, 2], [0.04, 0.04], familywise_alpha=0.05, n_comparisons=4)


if __name__ == "__main__":
    import unittest

    unittest.main()
