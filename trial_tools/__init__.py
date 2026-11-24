"""家校双邀请两因素试验的可重复办理工具。

模块划分：
- records   资料载入、引用完整性与冻结随机批次核对
- households 家庭事实解析（含补录）、跨班连接与冲突识别
- design    设计约束、受限随机化、可发出安排与不可比较范围
- outcomes  跟踪窗口、成熟度与结果编码（拒绝是结果，缺失不是零）
- analysis  ITT 两因素估计、整群/多向相关性、随机化核对
- monitor   多次查看的误差限额与阶段判断规则
- ledger    只追加的决策台账，支持逐次入组决定的追溯
"""

from .records import StudyData, load_records
from .households import HouseholdResolver
from .design import DesignEngine, NextRoundPlan
from .outcomes import OutcomeBuilder
from .analysis import FactorialAnalysis
from .monitor import Monitor
from .ledger import DecisionLedger

__all__ = [
    "StudyData",
    "load_records",
    "HouseholdResolver",
    "DesignEngine",
    "NextRoundPlan",
    "OutcomeBuilder",
    "FactorialAnalysis",
    "Monitor",
    "DecisionLedger",
]
