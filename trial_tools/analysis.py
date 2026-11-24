"""按原随机安排的 ITT 两因素分析。

估计量（线性概率模型，便于直接解释为邀请带来的概率差）：
    Y = b0 + bS*S + bA*A + bSA*S*A
- bS  学生邀请主效应（家庭同伴邀请关闭时）
- bA  家长邀请主效应（学生邀请关闭时）
- bSA 两邀请合用的交互增量
- 合用组合 vs 双对照 = bS + bA + bSA（线性函数，方差按协方差矩阵传播）

相关性处理：
- 学生端主分析按班级整群（随机化就在班级层面）给出 CR1 稳健方差；
- 同时给出 班级×家庭 多向（Cameron-Gelbach-Miller 型）稳健方差作为跨班手足核对；
- 家庭端按家庭为分析单位，提供 HC1 与多向稳健两套结果；
- 冲突家庭波及范围不进入家长因素比较，但单独登记实际暴露，不删数据。

随机性核对：在冻结的 4 个等概率备选批次上重算对比，给出随机化推断的 p 值区间。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np
from scipy import stats

from .outcomes import LookData

# 对照 -> (S, A, S*A, 截距) 的设计矩阵列顺序
COL_INTERCEPT = 0
COL_S = 1
COL_A = 2
COL_SA = 3


@dataclass(frozen=True)
class Estimand:
    key: str
    label: str
    contrast: tuple[float, ...]  # 与 (b0, bS, bA, bSA) 对齐


ESTIMANDS = (
    Estimand("student_main", "学生邀请主效应 (A=0)", (0.0, 1.0, 0.0, 0.0)),
    Estimand("adult_main", "家长邀请主效应 (S=0)", (0.0, 0.0, 1.0, 0.0)),
    Estimand("interaction", "两邀请合用交互增量", (0.0, 0.0, 0.0, 1.0)),
    Estimand("joint_vs_control", "合用组合对双对照", (0.0, 1.0, 1.0, 1.0)),
)


@dataclass(frozen=True)
class ContrastResult:
    estimand: str
    label: str
    estimate: float
    se: float
    ci_low: float
    ci_high: float
    t_value: float
    p_value: float
    df: float
    variance_method: str
    n_obs: int
    n_clusters: int | None


@dataclass(frozen=True)
class ModelResult:
    outcome: str
    coefficients: tuple[float, ...]
    results: tuple[ContrastResult, ...]
    n_obs: int
    n_classes: int
    n_households: int
    variance_method: str
    cell_rates: dict[tuple[int, int], dict[str, float]]
    excluded_tainted: tuple[str, ...]


class FactorialAnalysis:
    def __init__(self, confidence: float = 0.95):
        self.confidence = confidence

    # ------------------------------------------------------------------
    def analyze_students(
        self,
        look: LookData,
        include_tainted: bool = False,
    ) -> ModelResult:
        rows = [r for r in look.students if r.status == "observed"]
        excluded = tuple(sorted({r.student for r in look.students if r.tainted}))
        if not include_tainted:
            rows = [r for r in rows if not r.tainted]
        if not rows:
            raise ValueError("没有已成熟且有有效观察的学生记录，无法估计学生端模型")

        y = np.array([r.event for r in rows], dtype=float)
        X = self._design_matrix([(r.student_arm, r.adult_arm) for r in rows])
        classes = np.array([r.klass for r in rows])
        households = np.array([r.household for r in rows])

        beta = ols(X, y)
        v_cl, g_cl = cluster_robust_cov(X, y, beta, classes)
        v_multi, g_multi = multiway_cov(X, y, beta, [classes, households])

        primary = self._contrasts(beta, v_cl, len(rows), g_cl, "班级整群 CR1", "学生端")
        secondary = self._contrasts(beta, v_multi, len(rows), g_multi, "班级×家庭多向稳健", "学生端")
        cell_rates = self._cell_rates(rows)
        return ModelResult(
            outcome="学生窗口内主动到访",
            coefficients=tuple(float(b) for b in beta),
            results=tuple(primary + [c for c in secondary if c.estimand == "interaction"]),
            n_obs=len(rows),
            n_classes=len(set(classes)),
            n_households=len(set(households)),
            variance_method="班级整群 CR1（另附多向稳健核对）",
            cell_rates=cell_rates,
            excluded_tainted=excluded,
        )

    def analyze_households(self, look: LookData) -> ModelResult:
        rows = [r for r in look.households if r.status == "observed" and r.adult_arm is not None]
        if not rows:
            raise ValueError("没有已成熟且有有效观察的家庭记录，无法估计家长端模型")
        excluded = tuple(sorted(r.household for r in look.households if r.conflicted))

        y = np.array([r.event for r in rows], dtype=float)
        # 家庭层面的 S 取成员学生邀请的众数（同班手足必然一致；跨班一致家庭取首个成员）。
        s_arm = []
        a_arm = []
        for r in rows:
            member_cells = [
                (m.student_arm, m.adult_arm)
                for m in look.students
                if m.household == r.household
            ]
            s_vals = [c[0] for c in member_cells] or [0]
            s_arm.append(max(set(s_vals), key=s_vals.count))
            a_arm.append(int(r.adult_arm))
        X = self._design_matrix(list(zip(s_arm, a_arm)))
        households = np.array([r.household for r in rows])
        classes = np.array([r.classes[0] for r in rows])

        beta = ols(X, y)
        v_hc1, g_hc1 = hc1_cov(X, y, beta)
        v_multi, g_multi = multiway_cov(X, y, beta, [households, classes])

        primary = self._contrasts(beta, v_hc1, len(rows), g_hc1, "家庭单位 HC1", "家长端")
        secondary = self._contrasts(
            beta, v_multi, len(rows), g_multi, "家庭×班级多向稳健", "家长端"
        )
        cell_rates = self._cell_rates(
            [
                _PseudoRow(s, a, ev)
                for (s, a), ev in zip(zip(s_arm, a_arm), y)
            ]
        )
        return ModelResult(
            outcome="家庭交流回执",
            coefficients=tuple(float(b) for b in beta),
            results=tuple(primary + [c for c in secondary if c.estimand == "interaction"]),
            n_obs=len(rows),
            n_classes=len(set(classes)),
            n_households=len(set(households)),
            variance_method="家庭单位 HC1（另附多向稳健核对）",
            cell_rates=cell_rates,
            excluded_tainted=excluded,
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _design_matrix(cells: Sequence[tuple[int, int]]) -> np.ndarray:
        X = np.ones((len(cells), 4), dtype=float)
        for i, (s, a) in enumerate(cells):
            X[i, COL_S] = s
            X[i, COL_A] = a
            X[i, COL_SA] = s * a
        return X

    def _contrasts(
        self,
        beta: np.ndarray,
        cov: np.ndarray,
        n_obs: int,
        n_clusters: int | None,
        method: str,
        prefix: str,
    ) -> list[ContrastResult]:
        out: list[ContrastResult] = []
        # CR1 自由度取 G-1；HC1 取 n-p。
        df = (n_clusters - 1) if n_clusters is not None and n_clusters > 1 else max(n_obs - 4, 1)
        t_crit = stats.t.ppf((1 + self.confidence) / 2, df)
        for est in ESTIMANDS:
            c = np.array(est.contrast, dtype=float)
            point = float(c @ beta)
            var = float(c @ cov @ c)
            se = math.sqrt(max(var, 0.0))
            t_val = point / se if se > 0 else float("nan")
            p_val = 2 * stats.t.sf(abs(t_val), df) if se > 0 else float("nan")
            out.append(
                ContrastResult(
                    estimand=est.key,
                    label=f"{prefix}：{est.label}",
                    estimate=point,
                    se=se,
                    ci_low=point - t_crit * se,
                    ci_high=point + t_crit * se,
                    t_value=t_val,
                    p_value=p_val,
                    df=float(df),
                    variance_method=method,
                    n_obs=n_obs,
                    n_clusters=n_clusters,
                )
            )
        return out

    @staticmethod
    def _cell_rates(rows: Sequence) -> dict[tuple[int, int], dict[str, float]]:
        buckets: dict[tuple[int, int], list[int]] = {}
        for r in rows:
            buckets.setdefault((r.student_arm, r.adult_arm), []).append(int(r.event))
        return {
            cell: {
                "n": float(len(vals)),
                "rate": float(sum(vals) / len(vals)),
                "events": float(sum(vals)),
            }
            for cell, vals in sorted(buckets.items())
        }

    # ------------------------------------------------------------------
    def randomization_check(
        self,
        look: LookData,
        alternatives: Sequence[dict[str, tuple[int, int]]],
        outcome: str = "student",
    ) -> dict[str, object]:
        """在冻结备选批次上重算 S 主效应对比，报告随机化推断位置。

        每个备选批次等概率（登记概率），对比仅使用已成熟且有效观察的记录。
        """

        if outcome == "student":
            rows = [r for r in look.students if r.status == "observed" and not r.tainted]
            y = {r.student: float(r.event) for r in rows}
            klass = {r.student: r.klass for r in rows}
            arms_observed = {r.student: (r.student_arm, r.adult_arm) for r in rows}
        else:
            raise ValueError("随机化核对目前支持 student 端（随机化单位为班级）")

        values: list[float] = []
        for alt in alternatives:
            s1: list[float] = []
            s0: list[float] = []
            for sid, val in y.items():
                s_arm, _ = alt[klass[sid]]
                (s1 if s_arm == 1 else s0).append(val)
            if s1 and s0:
                values.append(float(np.mean(s1) - np.mean(s0)))
        observed = arms_observed
        obs_contrast = float(
            np.mean([v for sid, v in y.items() if observed[sid][0] == 1])
            - np.mean([v for sid, v in y.items() if observed[sid][0] == 0])
        )
        if not values:
            return {"feasible_alternatives": 0}
        arr = np.array(values)
        # 双侧随机化 p：与观察对比同向或更极端的备选比例（含观察批次自身）。
        p_rand = float(np.mean(np.abs(arr - arr.mean()) >= abs(obs_contrast - arr.mean()) - 1e-12))
        return {
            "feasible_alternatives": len(values),
            "contrast_per_alternative": [float(v) for v in values],
            "observed_contrast": obs_contrast,
            "randomization_p_two_sided": p_rand,
            "note": "备选数仅 4，随机化 p 值粒度粗，作为模型推断的核对而非替代",
        }


class _PseudoRow:
    __slots__ = ("student_arm", "adult_arm", "event")

    def __init__(self, s: int, a: int, event: float):
        self.student_arm = s
        self.adult_arm = a
        self.event = event


# ----------------------------------------------------------------------
# 线性代数工具：OLS 与三明治方差
# ----------------------------------------------------------------------


def ols(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    return beta


def _meat_and_bread(X: np.ndarray, resid: np.ndarray, groups: np.ndarray):
    n, k = X.shape
    unique = np.unique(groups)
    g = len(unique)
    meat = np.zeros((k, k), dtype=float)
    for gval in unique:
        idx = groups == gval
        Xg = X[idx]
        ug = resid[idx]
        score = Xg.T @ ug
        meat += np.outer(score, score)
    xtx_inv = np.linalg.pinv(X.T @ X)
    return xtx_inv, meat, n, k, g


def cluster_robust_cov(X: np.ndarray, y: np.ndarray, beta: np.ndarray, groups: np.ndarray):
    n, k = X.shape
    resid = y - X @ beta
    xtx_inv, meat, _, _, g = _meat_and_bread(X, resid, groups)
    # CR1 小样本修正
    correction = (g / (g - 1)) * ((n - 1) / (n - k)) if g > 1 else 1.0
    cov = correction * (xtx_inv @ meat @ xtx_inv)
    return cov, g


def hc1_cov(X: np.ndarray, y: np.ndarray, beta: np.ndarray):
    n, k = X.shape
    resid = y - X @ beta
    xtx_inv = np.linalg.pinv(X.T @ X)
    u = X * resid[:, None]
    meat = u.T @ u
    correction = n / (n - k) if n > k else 1.0
    return correction * (xtx_inv @ meat @ xtx_inv), n


def multiway_cov(
    X: np.ndarray,
    y: np.ndarray,
    beta: np.ndarray,
    group_layers: Sequence[np.ndarray],
) -> tuple[np.ndarray, int]:
    """Cameron-Gelbach-Miller 多向聚类三明治方差（含单路 HC1 项与交叠修正）。"""

    n, k = X.shape
    resid = y - X @ beta
    xtx_inv = np.linalg.pinv(X.T @ X)
    # 单路求和
    cov = np.zeros((k, k), dtype=float)
    min_groups = n
    for layer in group_layers:
        c, g = cluster_robust_cov(X, y, beta, layer)
        cov += c
        min_groups = min(min_groups, g)
    # 交叠层（所有层同时相等的最细分组）做减法，避免重复计数
    if len(group_layers) > 1:
        joined = np.array(["|".join(map(str, vals)) for vals in zip(*group_layers)])
        c_overlap, _ = cluster_robust_cov(X, y, beta, joined)
        cov -= c_overlap
    # 数值保护：非正定则做特征值截断
    cov = _nearest_pd(cov)
    return cov, min_groups


def _nearest_pd(matrix: np.ndarray) -> np.ndarray:
    sym = (matrix + matrix.T) / 2
    eigvals, eigvecs = np.linalg.eigh(sym)
    if np.all(eigvals >= 0):
        return sym
    eigvals = np.clip(eigvals, 1e-12, None)
    return eigvecs @ np.diag(eigvals) @ eigvecs.T
