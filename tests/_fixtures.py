"""测试共用夹具：载入演练资料，并提供构造小合成资料的工具。

所有测试只用合成数据，不接触任何真实人员资料。
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import unittest

# 隔离模式（python -I）不把当前工作目录加入 sys.path，显式登记仓库根目录。
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trial_tools.records import load_records

RECORDS_PATH = Path(__file__).resolve().parents[1] / "data" / "records.json"


class RecordsFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = load_records(RECORDS_PATH)
        cls.raw = json.loads(RECORDS_PATH.read_text(encoding="utf-8"))

    def deepcopy_raw(self) -> dict:
        return copy.deepcopy(self.raw)
