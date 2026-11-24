"""试验设计引擎与下一轮入组安排。

设计规则（来自业务约定）：
- 两因素四组合必须有共同支持，且任一组合都不能与某个学校或时段完全重合；
- 同班一轮学生邀请一致（班级层面统一分配一对臂别）；
- 同一家庭不发相互矛盾的家长邀请（跨班手足家庭的家长臂别必须一致）；
- 已发出邀请、已承诺时段冻结；仅尚未通知的安排允许调整；
- 已接到不同邀请的家庭保留实际暴露，其波及范围退出完整两因素比较，
  服务保留，但不删家庭、不撤销通知。

引擎输出可以实际发出的安排、每种组合的分配机会，以及不具备比较条件的范围。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Iterable

from .households import HouseholdResolver
from .records import StudyData

CELLS = ((0, 0), (0, 1), (1, 0), (1, 1))


@dataclass(frozen=True)
class ClassCapacity:
    klass: str
    school: str
    slot: str
    available: int
    confirmed: int

    @property
    def free(self) -> int:
        return self.available - self.confirmed


@dataclass(frozen=True)
class AllocationRow:
    klass: str
    school: str
    slot: str
    student_arm: int
    adult_arm: int
    free_seats: int
    confirmed_seats: int
    status: str  # randomizable / service_only
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class BlockDiagnostic:
    scope: str  # school / slot / block
    key: str
    cells_present: tuple[tuple[int, int], ...]
    fully_confounded: bool


@dataclass(frozen=True)
class CurrentAudit:
    """对登记在案的当前轮设计的审计结论。"""

    selected_index: int
    probabilities: tuple[float, ...]
    n_alternatives: int
    cell_class_counts: dict[tuple[int, int], int]
    cell_seat_counts: dict[tuple[int, int], int]
    diagnostics: tuple[BlockDiagnostic, ...]
    common_support: bool
    school_confounded: bool
    slot_confounded: bool
    tainted_classes: tuple[str, ...]
    conflicted_households: tuple[str, ...]
    non_comparable_students: tuple[str, ...]
    adjustable_unsent: tuple[dict, ...]
    locked_sent: int


@dataclass(frozen=True)
class NextRoundPlan:
    batches: tuple[dict[str, tuple[int, int]], ...]
    probabilities: tuple[float, ...]
    selected_index: int
    rows: tuple[AllocationRow, ...]
    cell_free_seats: dict[tuple[int, int], int]
    diagnostics: tuple[BlockDiagnostic, ...]
    feasible: bool
    infeasibility_reasons: tuple[str, ...]
    service_only: tuple[AllocationRow, ...]
    non_comparable_scope: dict[str, object]
    household_constraints: dict[str, dict[str, object]]
    rng_seed: int

    def selected_map(self) -> dict[str, tuple[int, int]]:
        return dict(self.batches[self.selected_index])


class DesignEngine:
    def __init__(self, data: StudyData, resolver: HouseholdResolver):
        self.data = data
        self.resolver = resolver
        self.capacities = self._load_capacities()
        self.extra_class_meta: dict[str, dict] = {}

    def _load_capacities(self) -> dict[str, ClassCapacity]:
        cls = {c["class"]: c for c in self.data.classes}
        out: dict[str, ClassCapacity] = {}
        for row in self.data.next_round:
            c = cls[row["class"]]
            out[row["class"]] = ClassCapacity(
                klass=row["class"],
                school=c["school"],
                slot=c["slot"],
                available=int(row["available"]),
                confirmed=int(row["confirmed_limit"]),
            )
        return out

    # ------------------------------------------------------------------
    # 当前轮设计审计
    # ------------------------------------------------------------------

    def audit_current(self) -> CurrentAudit:
        draw = self.data.original_draw
        alts = draw["alternatives"]
        selected = alts[draw["selected_index"]]
        sel_map = {row["class"]: (row["student_arm"], row["adult_arm"]) for row in selected}

        cls = {c["class"]: c for c in self.data.classes}
        seat_limit = {k: c["seat_limit"] for k, c in cls.items()}

        cell_classes: dict[tuple[int, int], list[str]] = {cell: [] for cell in CELLS}
        for klass, cell in sel_map.items():
            cell_classes[cell].append(klass)
        cell_class_counts = {cell: len(v) for cell, v in cell_classes.items()}
        cell_seat_counts = {
            cell: sum(seat_limit[k] for k in v) for cell, v in cell_classes.items()
        }

        diagnostics = tuple(self._block_diagnostics(sel_map))
        school_confounded = any(
            d.fully_confounded and d.scope == "school" for d in diagnostics
        )
        slot_confounded = any(
            d.fully_confounded and d.scope == "slot" for d in diagnostics
        )
        common_support = all(v > 0 for v in cell_class_counts.values())

        tainted = tuple(sorted(self.resolver.tainted_class_set()))
        conflicted = tuple(sorted(h.household for h in self.resolver.conflicted_households()))
        tainted_students = tuple(sorted(
            m.student
            for m in self.resolver.members.values()
            if m.klass in tainted
        ))
        adjustable = tuple(
            {
                "student": d["student"],
                "class": d["class"],
                "student_arm": d["student_arm"],
                "adult_arm": d["adult_arm"],
                "day": d["day"],
                "household": self.resolver.student_household(d["student"]),
            }
            for d in self.data.dispatches
            if not d["sent"]
        )
        locked_sent = sum(1 for d in self.data.dispatches if d["sent"])

        return CurrentAudit(
            selected_index=int(draw["selected_index"]),
            probabilities=tuple(float(p) for p in draw["probabilities"]),
            n_alternatives=len(alts),
            cell_class_counts=cell_class_counts,
            cell_seat_counts=cell_seat_counts,
            diagnostics=diagnostics,
            common_support=common_support,
            school_confounded=school_confounded,
            slot_confounded=slot_confounded,
            tainted_classes=tainted,
            conflicted_households=conflicted,
            non_comparable_students=tainted_students,
            adjustable_unsent=adjustable,
            locked_sent=locked_sent,
        )

    def _block_diagnostics(self, assignment: dict[str, tuple[int, int]]) -> list[BlockDiagnostic]:
        cls = self._class_meta()
        groups: dict[tuple[str, str], list[str]] = {}
        for klass in assignment:
            key = (cls[klass]["school"], cls[klass]["slot"])
            groups.setdefault(key, []).append(klass)

        out: list[BlockDiagnostic] = []
        schools: dict[str, list[str]] = {}
        slots: dict[str, list[str]] = {}
        for (school, slot), members in groups.items():
            cells = tuple(sorted({assignment[k] for k in members}))
            out.append(
                BlockDiagnostic(
                    scope="block",
                    key=f"{school}/{slot}",
                    cells_present=cells,
                    fully_confounded=len(cells) == 1,
                )
            )
            schools.setdefault(school, []).extend(members)
            slots.setdefault(slot, []).extend(members)
        for scope, bucket in (("school", schools), ("slot", slots)):
            for key, members in sorted(bucket.items()):
                cells = tuple(sorted({assignment[k] for k in members}))
                out.append(
                    BlockDiagnostic(
                        scope=scope,
                        key=key,
                        cells_present=cells,
                        fully_confounded=len(cells) == 1,
                    )
                )
        return out

    # ------------------------------------------------------------------
    # 下一轮安排
    # ------------------------------------------------------------------

    def _default_links(self) -> dict[str, tuple[str, ...]]:
        """登记资料中已知的跨班手足连接。

        冲突家庭的连接转成 service_only 退出信号；无冲突连接保留家长臂别锁定。
        调用方可通过 build_next_plan(links=...) 覆盖或追加新发现的手足对。
        """

        links: dict[str, tuple[str, ...]] = {}
        for h in self.resolver.households.values():
            if h.cross_class:
                links[h.household] = h.classes
        return links

    def register_new_sibling_link(
        self,
        household: str,
        classes: Iterable[str],
        extra_links: dict[str, tuple[str, ...]] | None = None,
    ) -> dict[str, tuple[str, ...]]:
        """登记新发现的一对手足，返回合并后的连接表（不改动原始资料）。

        已发出的邀请不会因此重新归组：若该家庭已有冲突凭证，新连接只会扩大
        service_only 的波及范围；若家庭尚未通知，连接在本轮随机化中按锁定处理。
        """

        classes = tuple(sorted(set(classes)))
        unknown = [c for c in classes if c not in self.capacities]
        if unknown:
            raise ValueError(f"手足连接引用了未知班级: {unknown}")
        merged = self._default_links()
        if extra_links:
            for hid, linked in extra_links.items():
                merged[hid] = tuple(sorted(set(linked)))
        if household in merged:
            merged[household] = tuple(sorted(set(merged[household]) | set(classes)))
        else:
            merged[household] = classes
        return merged

    def add_class_capacity(
        self,
        klass: str,
        school: str,
        slot: str,
        available: int,
        confirmed: int = 0,
    ) -> "DesignEngine":
        """返回一个带新增班级容量的引擎副本，用于下一轮新增班级的入组。

        新班级没有历史派发凭证，因此不受冻结批次约束，但仍须满足全部设计规则。
        已承诺名额一经登记即受保护，不能被随机安排占用。
        """

        if klass in self.capacities:
            raise ValueError(f"班级 {klass} 已存在，新增操作不能覆盖既有容量")
        if available < 0 or confirmed < 0 or confirmed > available:
            raise ValueError("新增班级名额非法")
        new_cap = ClassCapacity(
            klass=klass,
            school=school,
            slot=slot,
            available=int(available),
            confirmed=int(confirmed),
        )
        clone = DesignEngine(self.data, self.resolver)
        clone.capacities = dict(self.capacities)
        clone.capacities[klass] = new_cap
        clone.extra_class_meta = dict(self.extra_class_meta)
        clone.extra_class_meta[klass] = {
            "class": klass,
            "school": school,
            "slot": slot,
            "seat_limit": int(available),
        }
        return clone

    def _class_meta(self) -> dict[str, dict]:
        """合并登记班级与本轮新增班级的元数据。"""

        meta = {c["class"]: dict(c) for c in self.data.classes}
        meta.update(getattr(self, "extra_class_meta", {}))
        return meta

    def build_next_plan(
        self,
        links: dict[str, tuple[str, ...]] | None = None,
        seed: int = 20261124,
        n_batches: int = 4,
        balance_tolerance: int = 2,
    ) -> NextRoundPlan:
        rng = random.Random(seed)
        links = self._default_links() if links is None else dict(links)
        capacities = self.capacities

        # 1) 已承诺名额检查：承诺时段不能被随机安排占用。
        infeasibility: list[str] = []
        for cap in capacities.values():
            if cap.confirmed > cap.available:
                infeasibility.append(
                    f"{cap.klass} 已承诺 {cap.confirmed} 超过可投放 {cap.available}"
                )

        # 2) 冲突家庭波及班级 -> 保留服务、退出完整两因素比较。
        conflicted = {h.household: h for h in self.resolver.conflicted_households()}
        service_classes: set[str] = set()
        household_constraints: dict[str, dict[str, object]] = {}
        # 先确定所有 service_only 班级：冲突家庭波及，以及连接到这些班级的新手足连接
        # （同一家庭的成人邀请会把污染沿手足关系传导，半条连接不能支持完整比较）。
        for hid, classes in links.items():
            if hid in conflicted:
                service_classes.update(classes)
        propagating = True
        while propagating:
            propagating = False
            for hid, classes in links.items():
                if hid in conflicted:
                    continue
                if any(c in service_classes for c in classes) and not set(classes) <= service_classes:
                    service_classes.update(classes)
                    propagating = True

        for hid, classes in links.items():
            if hid in conflicted:
                household_constraints[hid] = {
                    "type": "conflicted_service_only",
                    "classes": list(classes),
                    "adult_arms_sent": list(conflicted[hid].adult_arms_sent),
                }
            elif set(classes) <= service_classes:
                household_constraints[hid] = {
                    "type": "linked_service_only",
                    "classes": list(classes),
                    "locked_adult_arm": (
                        self.resolver.households[hid].effective_adult_arm
                        if hid in self.resolver.households
                        else None
                    ),
                    "new_household": hid not in self.resolver.households,
                }
            else:
                known = self.resolver.households.get(hid)
                # 新登记、尚无任何凭证的家庭没有已发臂别可锁：只保留连接事实，
                # 由受限随机化保证这些班级本轮取得一致的家长臂别。
                lock = known.effective_adult_arm if known is not None else None
                household_constraints[hid] = {
                    "type": "adult_arm_lock",
                    "classes": list(classes),
                    "locked_adult_arm": lock,
                    "new_household": known is None,
                }

        clean = [c for c in sorted(capacities) if c not in service_classes]

        # 3) 家庭约束 -> 班级层面的家长臂别锁定，以及无锁家庭的臂别相等组。
        class_adult_lock: dict[str, int] = {}
        equality_pairs: set[tuple[str, str]] = set()
        for hid, info in household_constraints.items():
            linked = [c for c in info["classes"] if c not in service_classes]
            if info["type"] != "adult_arm_lock":
                continue
            arm = info["locked_adult_arm"]
            if arm is None:
                # 新家庭：连接班级必须取得相同家长臂别，具体取值由随机化决定。
                for i, left in enumerate(linked):
                    for right in linked[i + 1 :]:
                        equality_pairs.add(tuple(sorted((left, right))))
                continue
            for klass in linked:
                if klass in class_adult_lock and class_adult_lock[klass] != arm:
                    infeasibility.append(f"班级 {klass} 上的家庭锁定互相矛盾")
                class_adult_lock[klass] = arm

        # 4) 受限随机化：在干净班级集合上求若干彼此不同的可行批次。
        batches = self._sample_feasible_batches(
            clean,
            class_adult_lock,
            rng,
            n_batches,
            balance_tolerance,
            same_adult_pairs=frozenset(equality_pairs),
        )
        if not batches:
            infeasibility.append(
                "干净班级集合无法满足四组合共同支持与学校/时段不重合约束"
            )
            # 仍然返回 service_only 清单，便于负责人安排保留服务。
            batches = ()

        selected_index = 0 if batches else -1
        probabilities = tuple(1.0 / len(batches) for _ in batches) if batches else ()

        rows: list[AllocationRow] = []
        service_rows: list[AllocationRow] = []
        if batches:
            chosen = batches[selected_index]
            for klass in sorted(capacities):
                cap = capacities[klass]
                if klass in service_classes:
                    row = AllocationRow(
                        klass=klass,
                        school=cap.school,
                        slot=cap.slot,
                        student_arm=-1,
                        adult_arm=-1,
                        free_seats=cap.free,
                        confirmed_seats=cap.confirmed,
                        status="service_only",
                        reasons=tuple(
                            f"冲突家庭 {hid} 波及"
                            for hid, info in household_constraints.items()
                            if klass in info["classes"] and info["type"] == "conflicted_service_only"
                        ),
                    )
                    service_rows.append(row)
                else:
                    s, a = chosen[klass]
                    rows.append(
                        AllocationRow(
                            klass=klass,
                            school=cap.school,
                            slot=cap.slot,
                            student_arm=s,
                            adult_arm=a,
                            free_seats=cap.free,
                            confirmed_seats=cap.confirmed,
                            status="randomizable",
                            reasons=(),
                        )
                    )
            cell_seats = {cell: 0 for cell in CELLS}
            for row in rows:
                cell_seats[(row.student_arm, row.adult_arm)] += row.free_seats
            diagnostics = tuple(self._block_diagnostics(chosen))
        else:
            cell_seats = {}
            diagnostics = ()
            for klass in sorted(service_classes):
                cap = capacities[klass]
                service_rows.append(
                    AllocationRow(
                        klass=klass,
                        school=cap.school,
                        slot=cap.slot,
                        student_arm=-1,
                        adult_arm=-1,
                        free_seats=cap.free,
                        confirmed_seats=cap.confirmed,
                        status="service_only",
                        reasons=("干净集合不可行，保留服务",),
                    )
                )

        scope = self._non_comparable_scope(service_classes, conflicted, household_constraints)

        return NextRoundPlan(
            batches=batches,
            probabilities=probabilities,
            selected_index=selected_index,
            rows=tuple(rows),
            cell_free_seats=cell_seats,
            diagnostics=diagnostics,
            feasible=bool(batches),
            infeasibility_reasons=tuple(infeasibility),
            service_only=tuple(service_rows),
            non_comparable_scope=scope,
            household_constraints=household_constraints,
            rng_seed=seed,
        )

    def _non_comparable_scope(
        self,
        service_classes: set[str],
        conflicted: dict[str, object],
        household_constraints: dict[str, dict[str, object]],
    ) -> dict[str, object]:
        students = tuple(sorted(
            m.student
            for m in self.resolver.members.values()
            if m.klass in service_classes
        ))
        return {
            "classes": tuple(sorted(service_classes)),
            "households": tuple(sorted(conflicted)),
            "students_in_scope": students,
            "affected_estimands": (
                "家长邀请主效应",
                "两邀请合用（交互）效应",
            ),
            "retained_estimands": ("学生邀请主效应（干净班级集合内）",),
            "note": "保留实际发生的暴露与影响范围，不删家庭、不重新归组、不撤销通知",
        }

    # ------------------------------------------------------------------
    # 受限随机化求解
    # ------------------------------------------------------------------

    def _sample_feasible_batches(
        self,
        clean_classes: list[str],
        class_adult_lock: dict[str, int],
        rng: random.Random,
        n_batches: int,
        balance_tolerance: int,
        same_adult_pairs: frozenset[tuple[str, str]] = frozenset(),
    ) -> tuple[dict[str, tuple[int, int]], ...]:
        if not clean_classes:
            return ()
        cls = self._class_meta()
        schools = sorted({cls[k]["school"] for k in clean_classes})
        slots = sorted({cls[k]["slot"] for k in clean_classes})

        # 前置可行性：每个学校、每个时段至少要有 4 个干净班级，才可能容纳全部四组合。
        per_school = {s: [k for k in clean_classes if cls[k]["school"] == s] for s in schools}
        per_slot = {t: [k for k in clean_classes if cls[k]["slot"] == t] for t in slots}
        if any(len(v) < len(CELLS) for v in per_school.values()) or any(
            len(v) < len(CELLS) for v in per_slot.values()
        ):
            return ()

        # 相等约束中的班级必须都在干净集合内，否则该连接已整体转为服务保留。
        live_pairs = frozenset(
            pair
            for pair in same_adult_pairs
            if pair[0] in set(clean_classes) and pair[1] in set(clean_classes)
        )

        solver = _ConstraintSolver(
            clean_classes=clean_classes,
            cls=cls,
            schools=schools,
            slots=slots,
            per_school=per_school,
            per_slot=per_slot,
            locks=class_adult_lock,
            balance_tolerance=balance_tolerance,
            node_budget=20_000,
            same_adult_pairs=live_pairs,
        )

        found: list[dict[str, tuple[int, int]]] = []
        seen: set[tuple[tuple[int, int], ...]] = set()
        attempts = 0
        max_attempts = max(50, 10 * n_batches)
        while len(found) < n_batches and attempts < max_attempts:
            attempts += 1
            solution = solver.solve(rng)
            if solution is None:
                continue
            key = tuple(solution[k] for k in clean_classes)
            if key in seen:
                continue
            seen.add(key)
            found.append(solution)
        return tuple(found)

    # ------------------------------------------------------------------
    # 调整边界
    # ------------------------------------------------------------------

    def adjustable_unsent(self) -> list[dict[str, object]]:
        """尚未通知、仍可调整的安排；已发邀请不在此列，禁止重新归组。"""

        return [
            {
                "student": d["student"],
                "class": d["class"],
                "household": self.resolver.student_household(d["student"]),
                "student_arm": d["student_arm"],
                "adult_arm": d["adult_arm"],
                "dispatch_day": d["day"],
                "allowed_action": "可撤销或改期；调整仍须满足同班一致与同家庭不矛盾",
            }
            for d in self.data.dispatches
            if not d["sent"]
        ]

    def marginal_opportunities(
        self, plan: NextRoundPlan
    ) -> dict[str, dict[tuple[int, int], float]]:
        """每个干净班级在可行批次族下落到各组合的机会（批次等概率抽取）。"""

        out: dict[str, dict[tuple[int, int], float]] = {}
        if not plan.batches:
            return out
        m = len(plan.batches)
        classes = plan.batches[0].keys()
        for klass in classes:
            tally = {cell: 0.0 for cell in CELLS}
            for batch in plan.batches:
                tally[batch[klass]] += 1.0 / m
            out[klass] = tally
        return out


# ----------------------------------------------------------------------
# 受限随机化的回溯约束求解器（带回填、MRV 与节点预算）
# ----------------------------------------------------------------------


class _ConstraintSolver:
    """在干净班级集合上求满足全部设计约束的完整分配。

    约束：
    - 每个学校、每个时段出现全部四组合（不与某组合完全重合）；
    - 四组合共同支持且班级数平衡（差值不超过 balance_tolerance）；
    - 班级层面的家长臂别锁定（来自无冲突跨班手足家庭的已发邀请）。
    """

    def __init__(
        self,
        clean_classes: list[str],
        cls: dict[str, dict],
        schools: list[str],
        slots: list[str],
        per_school: dict[str, list[str]],
        per_slot: dict[str, list[str]],
        locks: dict[str, int],
        balance_tolerance: int,
        node_budget: int,
        same_adult_pairs: frozenset[tuple[str, str]] = frozenset(),
    ):
        self.classes = list(clean_classes)
        self.cls = cls
        self.schools = schools
        self.slots = slots
        self.per_school = per_school
        self.per_slot = per_slot
        self.locks = locks
        self.tol = balance_tolerance
        self.node_budget = node_budget
        self.same_adult_pairs = same_adult_pairs
        self.n = len(clean_classes)
        # 每个班级允许的组合
        self.domains: dict[str, list[tuple[int, int]]] = {
            k: [c for c in CELLS if locks.get(k) is None or c[1] == locks[k]]
            for k in self.classes
        }
        # 每个班级属于哪个学校/时段
        self.klass_school = {k: cls[k]["school"] for k in self.classes}
        self.klass_slot = {k: cls[k]["slot"] for k in self.classes}

    def solve(self, rng: random.Random) -> dict[str, tuple[int, int]] | None:
        assignment: dict[str, tuple[int, int]] = {}
        cell_count = {cell: 0 for cell in CELLS}
        school_cells: dict[str, set[tuple[int, int]]] = {s: set() for s in self.schools}
        slot_cells: dict[str, set[tuple[int, int]]] = {t: set() for t in self.slots}
        school_unassigned = {s: len(v) for s, v in self.per_school.items()}
        slot_unassigned = {t: len(v) for t, v in self.per_slot.items()}
        nodes = 0

        def backtrack() -> bool:
            nonlocal nodes
            nodes += 1
            if nodes > self.node_budget:
                raise _NodeBudgetExceeded
            if len(assignment) == self.n:
                return self._final_ok(cell_count, school_cells, slot_cells)

            klass = self._pick_variable(assignment, school_unassigned, slot_unassigned)
            values = list(self.domains[klass])
            rng.shuffle(values)
            school = self.klass_school[klass]
            slot = self.klass_slot[klass]
            for cell in values:
                assignment[klass] = cell
                cell_count[cell] += 1
                school_cells[school].add(cell)
                slot_cells[slot].add(cell)
                school_unassigned[school] -= 1
                slot_unassigned[slot] -= 1
                if (
                    self._forward_ok(
                        cell_count,
                        school_cells,
                        slot_cells,
                        school_unassigned,
                        slot_unassigned,
                    )
                    and self._pairs_ok(assignment)
                    and backtrack()
                ):
                    return True
                school_unassigned[school] += 1
                slot_unassigned[slot] += 1
                slot_cells[slot].discard(cell)
                school_cells[school].discard(cell)
                cell_count[cell] -= 1
                del assignment[klass]
            return False

        try:
            return dict(assignment) if backtrack() else None
        except _NodeBudgetExceeded:
            return None

    def _pairs_ok(self, assignment: dict[str, tuple[int, int]]) -> bool:
        for left, right in self.same_adult_pairs:
            if left in assignment and right in assignment:
                if assignment[left][1] != assignment[right][1]:
                    return False
        return True

    def _pick_variable(
        self,
        assignment: dict[str, tuple[int, int]],
        school_unassigned: dict[str, int],
        slot_unassigned: dict[str, int],
    ) -> str:
        """MRV：优先取可行域最小的班级；并列时取剩余班级最少（最紧迫）的学校/时段。"""

        best: str | None = None
        best_key: tuple[int, int, int] | None = None
        for klass in self.classes:
            if klass in assignment:
                continue
            key = (
                len(self.domains[klass]),
                min(
                    school_unassigned[self.klass_school[klass]],
                    slot_unassigned[self.klass_slot[klass]],
                ),
                len(klass),
            )
            if best_key is None or key < best_key:
                best_key = key
                best = klass
        assert best is not None
        return best

    def _forward_ok(
        self,
        cell_count: dict[tuple[int, int], int],
        school_cells: dict[str, set[tuple[int, int]]],
        slot_cells: dict[str, set[tuple[int, int]]],
        school_unassigned: dict[str, int],
        slot_unassigned: dict[str, int],
    ) -> bool:
        assigned = sum(cell_count.values())
        remaining = self.n - assigned
        # 1) 组合平衡上限：当前计数加全部剩余也仍可拉回的组合不能超限。
        cap = self.n // 4 + self.tol
        for cell in CELLS:
            if cell_count[cell] > cap:
                return False
            # 可实现性：还没出现的组合必须有足够剩余班级去填充。
            missing_cells = sum(1 for c in CELLS if cell_count[c] == 0)
            if remaining < missing_cells:
                return False
        # 2) 学校/时段回填：缺失的组合数不能超过该范围尚未分配的班级数。
        for school, present in school_cells.items():
            if len(CELLS) - len(present) > school_unassigned[school]:
                return False
        for slot, present in slot_cells.items():
            if len(CELLS) - len(present) > slot_unassigned[slot]:
                return False
        # 3) 组合平衡下限预判：剩余班级不足以让当前最少的组合达到共同支持。
        min_count = min(cell_count.values())
        if min_count == 0:
            zero_cells = [c for c in CELLS if cell_count[c] == 0]
            needed = sum(1 for _ in zero_cells)
            if remaining < needed:
                return False
        return True

    def _final_ok(
        self,
        cell_count: dict[tuple[int, int], int],
        school_cells: dict[str, set[tuple[int, int]]],
        slot_cells: dict[str, set[tuple[int, int]]],
    ) -> bool:
        if any(cell_count[c] == 0 for c in CELLS):
            return False
        if max(cell_count.values()) - min(cell_count.values()) > self.tol:
            return False
        if any(len(present) < len(CELLS) for present in school_cells.values()):
            return False
        if any(len(present) < len(CELLS) for present in slot_cells.values()):
            return False
        return True


class _NodeBudgetExceeded(RuntimeError):
    """单次回溯超出节点预算，交由上层重启。"""
