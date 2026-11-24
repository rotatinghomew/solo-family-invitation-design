"""多次查看的误差限额与阶段判断。

演练登记规则：
- 查看时点与每次查看的总错误限额 ``look_alpha`` 在资料中预先登记，不可事后更改；
- 每次查看内的比较数量 m 在首次查看前登记并锁定，单个比较的门槛为
  ``look_alpha / m``（Bonferroni，保守且可重复）；
- 阶段性查看只允许"继续/不下有效结论"，即使某组先成熟、先显著也不得提前报有效；
- 只有最后一次查看允许宣布有效；任何查看都可以宣布无效（点估计与置信区间排除预设最小效应时），
  本演练未登记最小效应量，因此无效结论同样留待最终查看按区间位置描述。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .analysis import ContrastResult


@dataclass(frozen=True)
class LookDecision:
    look_index: int
    look_day: int
    is_final: bool
    look_alpha: float
    n_comparisons: int
    per_comparison_alpha: float
    decisions: tuple[dict[str, object], ...]
    overall: str

    def pass_comparisons(self) -> tuple[str, ...]:
        return tuple(d["estimand"] for d in self.decisions if d["significant"])


@dataclass(frozen=True)
class MonitoringPlan:
    look_days: tuple[int, ...]
    look_alpha: tuple[float, ...]
    familywise_alpha: float
    n_comparisons: int
    per_comparison_alpha: tuple[float, ...]


class Monitor:
    def __init__(
        self,
        look_days: Sequence[int],
        look_alpha: Sequence[float],
        familywise_alpha: float,
        n_comparisons: int,
    ):
        if len(look_days) != len(look_alpha):
            raise ValueError("look_days 与 look_alpha 数量不一致")
        if n_comparisons <= 0:
            raise ValueError("比较数量必须为正")
        if sum(look_alpha) > familywise_alpha + 1e-12:
            raise ValueError("各次查看误差限额之和超过总错误限额")
        self.look_days = tuple(int(x) for x in look_days)
        self.look_alpha = tuple(float(x) for x in look_alpha)
        self.familywise_alpha = float(familywise_alpha)
        self.n_comparisons = int(n_comparisons)

    @property
    def plan(self) -> MonitoringPlan:
        return MonitoringPlan(
            look_days=self.look_days,
            look_alpha=self.look_alpha,
            familywise_alpha=self.familywise_alpha,
            n_comparisons=self.n_comparisons,
            per_comparison_alpha=tuple(a / self.n_comparisons for a in self.look_alpha),
        )

    def evaluate(
        self,
        look_index: int,
        look_day: int,
        contrasts: Sequence[ContrastResult],
    ) -> LookDecision:
        if not 0 <= look_index < len(self.look_days):
            raise ValueError("查看序号超出登记范围")
        is_final = look_index == len(self.look_days) - 1
        alpha = self.look_alpha[look_index]
        per_alpha = alpha / self.n_comparisons

        decisions: list[dict[str, object]] = []
        any_significant = False
        for c in contrasts:
            significant = bool(c.p_value <= per_alpha)
            any_significant |= significant
            if significant and is_final:
                verdict = "可报有效（最终查看，门槛通过）"
            elif significant:
                verdict = "仅登记信号：阶段查看不得提前宣布有效"
            else:
                verdict = "未达门槛，继续跟踪"
            decisions.append(
                {
                    "estimand": c.estimand,
                    "label": c.label,
                    "estimate": c.estimate,
                    "ci": [c.ci_low, c.ci_high],
                    "p_value": c.p_value,
                    "variance_method": c.variance_method,
                    "threshold_alpha": per_alpha,
                    "significant": significant,
                    "verdict": verdict,
                }
            )

        if is_final and any_significant:
            overall = "最终查看：按门槛对通过的比较给出有效结论，其余比较不下有效结论"
        elif is_final:
            overall = "最终查看：没有比较达到门槛，按估计与区间报告，不宣称有效"
        else:
            overall = "阶段查看：一律继续，不提前报有效；信号仅登记入台账"
        return LookDecision(
            look_index=look_index,
            look_day=int(look_day),
            is_final=is_final,
            look_alpha=alpha,
            n_comparisons=self.n_comparisons,
            per_comparison_alpha=per_alpha,
            decisions=tuple(decisions),
            overall=overall,
        )
