"""只追加的入组与查看决策台账。

每条记录都回答负责人的三个追溯问题：
1. 这次决定用了哪一批分配（冻结批次编号、概率、随机种子）；
2. 用到了哪些已经成熟的结果（查看日、成熟/删失分布、估计与门槛判断）；
3. 还有哪些冲突没有解决（冲突家庭、波及班级、service_only 清单）。

台账文件为 JSON Lines，一旦追加不可修改；重新办理会生成新的记录而不是覆盖旧记录。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class LedgerError(RuntimeError):
    pass


@dataclass(frozen=True)
class LedgerEntry:
    entry_id: str
    recorded_at: str
    kind: str
    payload: dict[str, Any]

    def to_line(self) -> str:
        return json.dumps(
            {
                "entry_id": self.entry_id,
                "recorded_at": self.recorded_at,
                "kind": self.kind,
                "payload": self.payload,
            },
            ensure_ascii=False,
            sort_keys=True,
        )


class DecisionLedger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()

    # ------------------------------------------------------------------
    def _next_id(self) -> str:
        return f"E{len(self.read_all()) + 1:04d}"

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def append(
        self,
        kind: str,
        payload: dict[str, Any],
        batch_ref: dict[str, Any] | None = None,
        matured_results: dict[str, Any] | None = None,
        unresolved_conflicts: list[dict[str, Any]] | None = None,
    ) -> LedgerEntry:
        entry = LedgerEntry(
            entry_id=self._next_id(),
            recorded_at=self._now(),
            kind=kind,
            payload={
                **payload,
                "batch_used": batch_ref or {},
                "matured_results_used": matured_results or {},
                "unresolved_conflicts": unresolved_conflicts or [],
            },
        )
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(entry.to_line() + os.linesep)
        return entry

    # ------------------------------------------------------------------
    def read_all(self) -> list[LedgerEntry]:
        if not self.path.exists():
            return []
        entries: list[LedgerEntry] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    entries.append(
                        LedgerEntry(
                            entry_id=obj["entry_id"],
                            recorded_at=obj["recorded_at"],
                            kind=obj["kind"],
                            payload=obj["payload"],
                        )
                    )
                except (KeyError, json.JSONDecodeError) as exc:  # pragma: no cover - 防御
                    raise LedgerError(f"台账第 {line_no} 行损坏: {exc}") from exc
        return entries

    def find(self, entry_id: str) -> LedgerEntry:
        for entry in self.read_all():
            if entry.entry_id == entry_id:
                return entry
        raise LedgerError(f"台账中找不到记录 {entry_id}")

    def audit_trail(self, entry_id: str) -> dict[str, Any]:
        """把一次具体入组决定的来龙去脉展开成可核对视图。"""

        entry = self.find(entry_id)
        p = entry.payload
        return {
            "entry_id": entry.entry_id,
            "recorded_at": entry.recorded_at,
            "kind": entry.kind,
            "batch_used": p.get("batch_used", {}),
            "matured_results_used": p.get("matured_results_used", {}),
            "unresolved_conflicts": p.get("unresolved_conflicts", []),
            "decision": {k: v for k, v in p.items() if k not in (
                "batch_used",
                "matured_results_used",
                "unresolved_conflicts",
            )},
        }

    def conflict_history(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for entry in self.read_all():
            for conflict in entry.payload.get("unresolved_conflicts", []):
                out.append(
                    {
                        "entry_id": entry.entry_id,
                        "recorded_at": entry.recorded_at,
                        "conflict": conflict,
                    }
                )
        return out
