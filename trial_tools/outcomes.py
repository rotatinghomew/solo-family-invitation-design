"""跟踪窗口、成熟度与结果编码。

原则（业务约定，不可通融）：
- 拒绝邀请是一种履行结果，按已观察处理，不能当成零事件或缺失；
- 没有观察机会（无跟踪行、来源缺失）、窗口未结束、日志落在邀请之前，
  一律按右删失处理，绝不能直接算作"没有到访/没有回执"；
- 学生端事件 = 约定窗口内主动返校交流到访；
- 家长端事件 = 独立的家庭交流回执；
- 家长邀请以家庭为单位，多子女家庭的结果只计算一次。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .households import Household, HouseholdMember, HouseholdResolver
from .records import (
    FAMILY_EVENT,
    FAMILY_NO_SOURCE,
    FAMILY_OBSERVED,
    STUDENT_EVENT,
    STUDENT_NO_SOURCE,
    STUDENT_OBSERVED,
    StudyData,
)


@dataclass(frozen=True)
class StudentRecord:
    student: str
    household: str
    klass: str
    student_arm: int
    adult_arm: int
    sent: bool
    dispatch_day: int
    window_end: int
    mature: bool
    status: str  # observed / no_source / no_opportunity / window_open / log_outside_window
    event: int | None  # 1/0，仅在 observed 时有值
    refusal: int | None
    observed_day: int | None
    raw_student_outcome: str | None
    raw_family_outcome: str | None
    tainted: bool


@dataclass(frozen=True)
class HouseholdRecord:
    household: str
    classes: tuple[str, ...]
    adult_arm: int | None  # 冲突家庭为 None，保留实际暴露而不假装分配正确
    conflicted: bool
    cross_class: bool
    mature: bool
    status: str
    event: int | None
    refusal: int | None
    observed_day: int | None
    member_students: tuple[str, ...]
    sent_arms: tuple[int, ...]


@dataclass(frozen=True)
class LookData:
    look_day: int
    window_days: int
    students: tuple[StudentRecord, ...]
    households: tuple[HouseholdRecord, ...]

    def observed_students(self) -> tuple[StudentRecord, ...]:
        return tuple(r for r in self.students if r.status == "observed")

    def censored_students(self) -> tuple[StudentRecord, ...]:
        return tuple(r for r in self.students if r.status != "observed")

    def observed_households(self) -> tuple[HouseholdRecord, ...]:
        return tuple(r for r in self.households if r.status == "observed")


class OutcomeBuilder:
    def __init__(self, data: StudyData, resolver: HouseholdResolver, window_days: int | None = None):
        self.data = data
        self.resolver = resolver
        self.window_days = int(window_days if window_days is not None else data.window_days())
        self._followup = {row["student"]: row for row in data.followup}

    def build(self, look_day: int) -> LookData:
        tainted = self.resolver.tainted_class_set()
        students = tuple(
            self._build_student(member, look_day, tainted)
            for member in sorted(self.resolver.members.values(), key=lambda m: m.student)
        )
        by_household: dict[str, list[StudentRecord]] = {}
        for rec in students:
            by_household.setdefault(rec.household, []).append(rec)
        households = tuple(
            self._build_household(self.resolver.households[hid], by_household[hid])
            for hid in sorted(by_household)
        )
        return LookData(
            look_day=int(look_day),
            window_days=self.window_days,
            students=students,
            households=households,
        )

    def _build_student(
        self, member: HouseholdMember, look_day: int, tainted: set[str]
    ) -> StudentRecord:
        window_end = member.dispatch_day + self.window_days
        row = self._followup.get(member.student)
        observed_day = row["observed_day"] if row else None
        raw_student = row["student_outcome"] if row else None
        raw_family = row["family_outcome"] if row else None

        mature = look_day >= window_end
        status: str
        event: int | None = None
        refusal: int | None = None

        if not mature:
            status = "window_open"
        elif row is None:
            status = "no_opportunity"
        elif observed_day < member.dispatch_day or observed_day > window_end:
            # 日志早于邀请（补发批次的历史行）或晚于约定窗口：不能证明窗口内行为。
            status = "log_outside_window"
        elif raw_student == STUDENT_NO_SOURCE:
            status = "no_source"
        elif raw_student not in STUDENT_OBSERVED:  # 防御：契约外取值按无机会处理
            status = "no_opportunity"
        else:
            status = "observed"
            event = 1 if raw_student == STUDENT_EVENT else 0
            refusal = 1 if raw_student == "拒绝" else 0

        return StudentRecord(
            student=member.student,
            household=member.resolved_household,
            klass=member.klass,
            student_arm=member.student_arm,
            adult_arm=member.adult_arm,
            sent=member.sent,
            dispatch_day=member.dispatch_day,
            window_end=window_end,
            mature=mature,
            status=status,
            event=event,
            refusal=refusal,
            observed_day=observed_day,
            raw_student_outcome=raw_student,
            raw_family_outcome=raw_family,
            tainted=member.klass in tainted,
        )

    def _build_household(
        self, household: Household, records: list[StudentRecord]
    ) -> HouseholdRecord:
        sent_arms = household.adult_arms_sent
        # 家庭结果按每个成员各自的派发窗口逐条核对，再合并为单一家庭结果。
        family_values: list[str] = []
        observed_days: list[int] = []
        member_window_open = False
        member_no_opportunity = False
        member_no_source = False
        for rec in records:
            if not rec.mature:
                member_window_open = True
                continue
            row = self._followup.get(rec.student)
            if row is None:
                member_no_opportunity = True
                continue
            observed_day = row["observed_day"]
            if observed_day < rec.dispatch_day or observed_day > rec.window_end:
                # 与学生端一致：窗口外（含早于邀请）的日志不能证明窗口内家庭行为。
                member_no_opportunity = True
                continue
            value = row["family_outcome"]
            observed_days.append(observed_day)
            family_values.append(value)
            if value == FAMILY_NO_SOURCE:
                member_no_source = True
            elif value not in FAMILY_OBSERVED:
                member_no_opportunity = True

        mature = all(r.mature for r in records)
        if household.conflicted:
            status = "conflicted"
        elif member_window_open:
            status = "window_open"
        elif family_values and not member_no_opportunity and not member_no_source:
            if len(set(family_values)) != 1:
                # 同一家庭的有效回执取值互相矛盾：登记为来源冲突，不擅自合并。
                status = "discordant"
            else:
                status = "observed"
        elif member_no_source:
            status = "no_source"
        else:
            status = "no_opportunity"

        event: int | None = None
        refusal: int | None = None
        if status == "observed":
            value = family_values[0]
            event = 1 if value == FAMILY_EVENT else 0
            refusal = 1 if value == "拒绝" else 0

        return HouseholdRecord(
            household=household.household,
            classes=household.classes,
            adult_arm=household.effective_adult_arm,
            conflicted=household.conflicted,
            cross_class=household.cross_class,
            mature=mature,
            status=status,
            event=event,
            refusal=refusal,
            observed_day=max(observed_days) if observed_days else None,
            member_students=tuple(r.student for r in records),
            sent_arms=sent_arms,
        )

    def status_table(self, look: LookData) -> dict[str, Any]:
        """供台账与 CLI 使用的成熟度/删失分布。"""

        from collections import Counter

        student_status = Counter(r.status for r in look.students)
        household_status = Counter(r.status for r in look.households)
        return {
            "look_day": look.look_day,
            "window_days": look.window_days,
            "student": {
                "total": len(look.students),
                "mature": sum(1 for r in look.students if r.mature),
                "observed": student_status.get("observed", 0),
                "censored_breakdown": {
                    k: v for k, v in student_status.items() if k != "observed"
                },
                "refusals_observed": sum(r.refusal or 0 for r in look.students),
                "events_observed": sum(r.event or 0 for r in look.students),
            },
            "household": {
                "total": len(look.households),
                "mature": sum(1 for r in look.households if r.mature),
                "observed": household_status.get("observed", 0),
                "censored_breakdown": {
                    k: v for k, v in household_status.items() if k != "observed"
                },
                "conflicted": household_status.get("conflicted", 0),
                "events_observed": sum(r.event or 0 for r in look.households),
            },
        }
