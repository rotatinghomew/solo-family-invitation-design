"""资料载入与输入核对。

核对内容仅针对登记的业务契约，不做任何业务判断：
- 关键字段齐全、取值合法；
- 学生、班级、跟踪、下一轮之间的引用完整；
- 冻结的历史随机批次结构完整，且已登记的派发与抽中批次一致；
- 派发的班级层面臂别一致（同一班一轮学生邀请一致）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ARM_RANGE = (0, 1)
STUDENT_OUTCOMES = ("到访", "拒绝", "未到访", "未取得来源")
FAMILY_OUTCOMES = ("收到回执", "拒绝", "未回执", "来源缺失")
# 有明确观察语义的取值；其余取值代表没有有效观察机会。
STUDENT_OBSERVED = ("到访", "拒绝", "未到访")
FAMILY_OBSERVED = ("收到回执", "拒绝", "未回执")
STUDENT_EVENT = "到访"
FAMILY_EVENT = "收到回执"
STUDENT_REFUSAL = "拒绝"
FAMILY_REFUSAL = "拒绝"
STUDENT_NO_SOURCE = "未取得来源"
FAMILY_NO_SOURCE = "来源缺失"


class RecordsError(ValueError):
    """输入资料不满足登记契约。"""


@dataclass(frozen=True)
class StudyData:
    """一次载入的不可变业务资料视图。"""

    raw: dict[str, Any]
    path: Path | None

    @property
    def study(self) -> dict[str, Any]:
        return self.raw["study"]

    @property
    def students(self) -> list[dict[str, Any]]:
        return self.raw["students"]

    @property
    def classes(self) -> list[dict[str, Any]]:
        return self.raw["classes"]

    @property
    def dispatches(self) -> list[dict[str, Any]]:
        return self.raw["dispatches"]

    @property
    def followup(self) -> list[dict[str, Any]]:
        return self.raw["followup"]

    @property
    def amendments(self) -> list[dict[str, Any]]:
        return self.raw["amendments"]

    @property
    def next_round(self) -> list[dict[str, Any]]:
        return self.raw["next_round"]

    @property
    def original_draw(self) -> dict[str, Any]:
        return self.raw["original_draw"]

    def window_days(self) -> int:
        """约定跟踪窗口长度（项目日），固定为 12 天。

        依据登记资料的内部一致性：首批派发日 3 + 12 = 15，在第 16 日查看时首批成熟；
        补发批次派发日 12 + 12 = 24，恰在最终查看日成熟。这样三次查看构成
        "尚无成熟 -> 首批中期 -> 两批全部最终" 的结构。调用方仍可用参数覆盖。
        """

        return 12


def load_records(source: str | Path) -> StudyData:
    path = Path(source)
    payload = json.loads(path.read_text(encoding="utf-8"))
    data = StudyData(raw=payload, path=path)
    validate(data)
    return data


def validate(data: StudyData) -> None:
    raw = data.raw
    required = {
        "study",
        "students",
        "classes",
        "original_draw",
        "dispatches",
        "followup",
        "amendments",
        "next_round",
    }
    missing = required - raw.keys()
    if missing:
        raise RecordsError(f"缺少顶层字段: {sorted(missing)}")

    study = data.study
    for key in ("days", "look_days", "look_alpha", "round_days", "familywise_alpha"):
        if key not in study:
            raise RecordsError(f"study 缺少字段: {key}")
    if study["look_days"] != sorted(study["look_days"]):
        raise RecordsError("look_days 必须按时间升序登记")
    if len(study["look_days"]) != len(study["look_alpha"]):
        raise RecordsError("look_alpha 必须与 look_days 一一对应")
    if any(a <= 0 for a in study["look_alpha"]) or study["familywise_alpha"] <= 0:
        raise RecordsError("误差限额必须为正数")

    students = {item["student"]: item for item in data.students}
    if len(students) != len(data.students):
        raise RecordsError("学生编号重复")
    classes = {item["class"]: item for item in data.classes}
    if len(classes) != len(data.classes):
        raise RecordsError("班级编号重复")

    for item in data.students:
        if item["class"] not in classes:
            raise RecordsError(f"学生 {item['student']} 引用了未知班级 {item['class']}")
        if not item.get("household"):
            raise RecordsError(f"学生 {item['student']} 缺少家庭编号")
    for item in data.classes:
        for key in ("school", "slot", "seat_limit", "past_student_arm", "past_adult_arm"):
            if key not in item:
                raise RecordsError(f"班级 {item['class']} 缺少字段 {key}")
        if item["past_student_arm"] not in ARM_RANGE or item["past_adult_arm"] not in ARM_RANGE:
            raise RecordsError(f"班级 {item['class']} 历史臂别取值非法")
        if item["seat_limit"] <= 0:
            raise RecordsError(f"班级 {item['class']} 名额必须为正")

    # 冻结随机批次：4 个备选、概率和为 1、每个备选覆盖全部班级且臂别合法。
    draw = data.original_draw
    alts = draw["alternatives"]
    probs = draw["probabilities"]
    if len(alts) != len(probs):
        raise RecordsError("original_draw 备选与概率数量不一致")
    if abs(sum(probs) - 1.0) > 1e-9:
        raise RecordsError("original_draw 概率之和必须为 1")
    if not (0 <= draw["selected_index"] < len(alts)):
        raise RecordsError("selected_index 超出备选范围")
    for idx, alt in enumerate(alts):
        if {row["class"] for row in alt} != set(classes):
            raise RecordsError(f"备选 {idx} 没有覆盖全部班级")
        for row in alt:
            if row["student_arm"] not in ARM_RANGE or row["adult_arm"] not in ARM_RANGE:
                raise RecordsError(f"备选 {idx} 中 {row['class']} 臂别非法")

    selected = {row["class"]: (row["student_arm"], row["adult_arm"]) for row in alts[draw["selected_index"]]}

    # 派发引用、合法取值、与冻结批次一致、同班一轮一致。
    seen_students: set[str] = set()
    class_arms: dict[str, set[tuple[int, int]]] = {}
    for item in data.dispatches:
        sid = item["student"]
        if sid not in students:
            raise RecordsError(f"派发记录引用未知学生 {sid}")
        if sid in seen_students:
            raise RecordsError(f"学生 {sid} 存在重复派发登记")
        seen_students.add(sid)
        if item["class"] not in classes:
            raise RecordsError(f"派发 {sid} 引用未知班级 {item['class']}")
        arms = (item["student_arm"], item["adult_arm"])
        if arms[0] not in ARM_RANGE or arms[1] not in ARM_RANGE:
            raise RecordsError(f"派发 {sid} 臂别非法")
        if arms != selected[item["class"]]:
            raise RecordsError(
                f"派发 {sid} 的臂别 {arms} 与冻结抽中批次 {selected[item['class']]} 不一致；"
                "已发邀请不能重新归组"
            )
        class_arms.setdefault(item["class"], set()).add(arms)
    if seen_students != set(students):
        raise RecordsError("派发登记没有覆盖全部学生")
    inconsistent = {k: v for k, v in class_arms.items() if len(v) > 1}
    if inconsistent:
        raise RecordsError(f"同一班一轮学生邀请不一致: {inconsistent}")

    # 跟踪与补录引用、结果取值合法。
    fu_ids: set[str] = set()
    for item in data.followup:
        if item["student"] not in students:
            raise RecordsError(f"跟踪记录引用未知学生 {item['student']}")
        if item["student"] in fu_ids:
            raise RecordsError(f"学生 {item['student']} 存在重复跟踪登记")
        fu_ids.add(item["student"])
        if item["student_outcome"] not in STUDENT_OUTCOMES:
            raise RecordsError(f"学生结果取值非法: {item['student_outcome']}")
        if item["family_outcome"] not in FAMILY_OUTCOMES:
            raise RecordsError(f"家庭结果取值非法: {item['family_outcome']}")
    for item in data.amendments:
        if item["student"] not in students:
            raise RecordsError(f"补录引用未知学生 {item['student']}")
        if not item.get("corrected_household") or not item.get("reason"):
            raise RecordsError(f"补录 {item['student']} 缺少更正家庭或原因")

    nr_classes: set[str] = set()
    for item in data.next_round:
        if item["class"] not in classes:
            raise RecordsError(f"下一轮记录引用未知班级 {item['class']}")
        if item["class"] in nr_classes:
            raise RecordsError(f"下一轮班级 {item['class']} 重复登记")
        nr_classes.add(item["class"])
        if item["available"] < 0 or item["confirmed_limit"] < 0:
            raise RecordsError(f"下一轮 {item['class']} 名额不能为负")
        if item["confirmed_limit"] > item["available"]:
            raise RecordsError(
                f"下一轮 {item['class']} 已承诺名额 {item['confirmed_limit']} 超过可投放 {item['available']}"
            )
    if nr_classes != set(classes):
        raise RecordsError("下一轮登记没有覆盖全部班级")
