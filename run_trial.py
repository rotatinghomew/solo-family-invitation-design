#!/usr/bin/env python3
"""隔离模式启动器：在不依赖环境变量的情况下把仓库根目录加入模块路径。

用法与包内 CLI 完全一致，例如：
    python3 -I run_trial.py audit data/records.json
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from trial_tools.cli import main  # noqa: E402

if __name__ == "__main__":
    main(sys.argv[1:])
