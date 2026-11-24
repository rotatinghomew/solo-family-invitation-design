"""跟踪成熟度：拒绝是结果，无来源/无机会/窗口未结束/窗口外日志都不是零事件。"""

from __future__ import annotations

from trial_tools.households import HouseholdResolver
from trial_tools.outcomes import OutcomeBuilder

from ._fixtures import RecordsFixture


class OutcomeTests(RecordsFixture):
    def setUp(self) -> None:
        super().setUp()
        self.resolver = HouseholdResolver(self.data)
        self.builder = OutcomeBuilder(self.data, self.resolver, window_days=12)

    def test_first_look_has_no_mature_records(self) -> None:
        look = self.builder.build(9)
        self.assertTrue(all(not r.mature for r in look.students))
        self.assertEqual(set(r.status for r in look.students), {"window_open"})

    def test_refusal_is_observed_zero_not_missing(self) -> None:
        look = self.builder.build(24)
        refusals = [r for r in look.students if r.raw_student_outcome == "拒绝" and r.status == "observed"]
        self.assertTrue(refusals)
        for rec in refusals:
            self.assertEqual(rec.event, 0)
            self.assertEqual(rec.refusal, 1)

    def test_no_source_is_censored_not_zero(self) -> None:
        look = self.builder.build(24)
        no_source = [r for r in look.students if r.status == "no_source"]
        self.assertTrue(no_source)
        for rec in no_source:
            self.assertIsNone(rec.event)

    def test_missing_followup_row_is_no_opportunity(self) -> None:
        look = self.builder.build(24)
        by_id = {r.student: r for r in look.students}
        # U011 等 14 名学生没有跟踪行
        self.assertEqual(by_id["U011"].status, "no_opportunity")
        self.assertIsNone(by_id["U011"].event)

    def test_log_before_dispatch_is_outside_window(self) -> None:
        look = self.builder.build(24)
        by_id = {r.student: r for r in look.students}
        # U017/U034 的观察日早于第 12 日补发邀请
        self.assertEqual(by_id["U017"].status, "log_outside_window")
        self.assertEqual(by_id["U034"].status, "log_outside_window")
        self.assertIsNone(by_id["U017"].event)

    def test_log_after_window_close_is_outside_window(self) -> None:
        # 首批第 3 日派发、窗口 12 天：观察日 16 及之后落在窗口外
        look = self.builder.build(24)
        followup = {row["student"]: row for row in self.data.followup}
        dispatch = {row["student"]: row for row in self.data.dispatches}
        late = [
            sid
            for sid, row in followup.items()
            if dispatch[sid]["day"] == 3 and row["observed_day"] > 15
        ]
        self.assertTrue(late)
        by_id = {r.student: r for r in look.students}
        for sid in late:
            self.assertEqual(by_id[sid].status, "log_outside_window")
            self.assertIsNone(by_id[sid].event)

    def test_day_twelve_cohort_matures_only_at_final_look(self) -> None:
        mid = self.builder.build(16)
        final = self.builder.build(24)
        mid_by_id = {r.student: r for r in mid.students}
        final_by_id = {r.student: r for r in final.students}
        self.assertFalse(mid_by_id["U051"].mature)  # 12+12=24
        self.assertTrue(final_by_id["U051"].mature)

    def test_conflicted_households_remain_flagged(self) -> None:
        look = self.builder.build(24)
        by_hh = {r.household: r for r in look.households}
        self.assertEqual(by_hh["F000"].status, "conflicted")
        self.assertEqual(by_hh["F109"].status, "conflicted")
        self.assertIsNone(by_hh["F000"].event)

    def test_household_uses_single_family_level_record(self) -> None:
        look = self.builder.build(24)
        observed = look.observed_households()
        # 多子女家庭不会被按学生人数重复计算
        self.assertEqual(len({r.household for r in observed}), len(observed))
        for rec in observed:
            self.assertIn(rec.event, (0, 1))

    def test_status_table_separates_censoring_reasons(self) -> None:
        table = self.builder.status_table(self.builder.build(24))
        breakdown = table["student"]["censored_breakdown"]
        self.assertIn("no_source", breakdown)
        self.assertIn("no_opportunity", breakdown)
        self.assertIn("log_outside_window", breakdown)
        # 删失类别合计 + 已观察 = 全体
        self.assertEqual(
            sum(breakdown.values()) + table["student"]["observed"],
            table["student"]["total"],
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
