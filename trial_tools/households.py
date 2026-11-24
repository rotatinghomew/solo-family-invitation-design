"""家庭事实解析与跨班连接。

补录（amendments）从登记日起更正家庭事实，但不改变已经发出的邀请凭证。
因此解析结果同时保留：
- 更正后的家庭归属（用于识别手足与跨班连接）；
- 每个家庭实际接到的家长邀请集合（用于暴露与冲突登记）；
- 冲突家庭与其波及的班级范围（用于划定不能支持完整两因素比较的部分）。

冲突不会被"修正"成某个整齐臂别，也不会通过删除家庭来恢复组别。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .records import StudyData


@dataclass(frozen=True)
class HouseholdMember:
    student: str
    registered_household: str
    resolved_household: str
    klass: str
    adult_arm: int
    student_arm: int
    sent: bool
    dispatch_day: int
    amended: bool


@dataclass(frozen=True)
class Household:
    household: str
    members: tuple[HouseholdMember, ...]
    classes: tuple[str, ...]
    adult_arms_seen: tuple[int, ...]
    adult_arms_sent: tuple[int, ...]
    conflicted: bool
    cross_class: bool

    @property
    def effective_adult_arm(self) -> int | None:
        """无冲突家庭的实际家长臂别；冲突家庭返回 None，禁止假装分配正确。"""

        if self.conflicted:
            return None
        arms = set(self.adult_arms_sent) or set(self.adult_arms_seen)
        return next(iter(arms)) if len(arms) == 1 else None


@dataclass
class HouseholdResolver:
    data: StudyData
    members: dict[str, HouseholdMember] = field(init=False)
    households: dict[str, Household] = field(init=False)
    amended_students: set[str] = field(init=False)

    def __post_init__(self) -> None:
        self.amended_students = {item["student"] for item in self.data.amendments}
        corrections = {item["student"]: item["corrected_household"] for item in self.data.amendments}
        dispatch = {item["student"]: item for item in self.data.dispatches}

        members: dict[str, HouseholdMember] = {}
        for row in self.data.students:
            sid = row["student"]
            d = dispatch[sid]
            resolved = corrections.get(sid, row["household"])
            members[sid] = HouseholdMember(
                student=sid,
                registered_household=row["household"],
                resolved_household=resolved,
                klass=row["class"],
                adult_arm=d["adult_arm"],
                student_arm=d["student_arm"],
                sent=bool(d["sent"]),
                dispatch_day=int(d["day"]),
                amended=sid in self.amended_students,
            )
        self.members = members

        grouped: dict[str, list[HouseholdMember]] = {}
        for member in members.values():
            grouped.setdefault(member.resolved_household, []).append(member)

        households: dict[str, Household] = {}
        for hid, rows in grouped.items():
            rows = tuple(sorted(rows, key=lambda m: m.student))
            classes = tuple(sorted({m.klass for m in rows}))
            seen = tuple(sorted({m.adult_arm for m in rows}))
            sent_arms = tuple(sorted({m.adult_arm for m in rows if m.sent}))
            households[hid] = Household(
                household=hid,
                members=rows,
                classes=classes,
                adult_arms_seen=seen,
                adult_arms_sent=sent_arms,
                # 已发出的两边不同邀请才算实际暴露冲突；尚未通知的安排仍可调整，不算既成冲突。
                conflicted=len(set(sent_arms)) > 1,
                cross_class=len(classes) > 1,
            )
        self.households = households

    # ---- 常用视图 -------------------------------------------------------

    def student_household(self, student: str) -> str:
        return self.members[student].resolved_household

    def conflicted_households(self) -> list[Household]:
        return [h for h in self.households.values() if h.conflicted]

    def cross_class_households(self) -> list[Household]:
        return [h for h in self.households.values() if h.cross_class]

    def affected_classes(self) -> dict[str, list[str]]:
        """冲突家庭 -> 波及班级（这些班级的家长因素比较受污染）。"""

        return {h.household: list(h.classes) for h in self.conflicted_households()}

    def tainted_class_set(self) -> set[str]:
        tainted: set[str] = set()
        for h in self.conflicted_households():
            tainted.update(h.classes)
        return tainted

    def class_households(self) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for member in self.members.values():
            out.setdefault(member.klass, set()).add(member.resolved_household)
        return out

    def household_links_for_classes(self, classes: Iterable[str]) -> dict[str, tuple[str, ...]]:
        """班级集合内，跨班手足家庭对班级分组的连接约束。

        返回 家庭 -> 涉及到的班级元组（仅保留至少落在给定集合内两项以上的连接）。
        """

        wanted = set(classes)
        links: dict[str, tuple[str, ...]] = {}
        for h in self.households.values():
            hit = tuple(c for c in h.classes if c in wanted)
            if len(hit) > 1:
                links[h.household] = hit
        return links

    def sent_adult_arm_lock(self) -> dict[str, int]:
        """已发出家长邀请且无冲突的家庭 -> 下一轮必须沿用的家长臂别。

        已承诺不发相互矛盾的家长邀请：同一家庭再收到邀请时只能与已发凭证一致。
        """

        locks: dict[str, int] = {}
        for h in self.households.values():
            arm = h.effective_adult_arm
            if arm is not None and any(m.sent for m in h.members):
                locks[h.household] = arm
        return locks
