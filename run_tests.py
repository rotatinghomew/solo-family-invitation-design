#!/usr/bin/env python3
"""隔离模式测试运行器：登记仓库根目录后执行 unittest discover。

用法：
    python3 -I run_tests.py
    python3 -I run_tests.py -v
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=str(ROOT / "tests"), top_level_dir=str(ROOT))
    result = unittest.TextTestRunner(verbosity=2 if "-v" in sys.argv else 1).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
