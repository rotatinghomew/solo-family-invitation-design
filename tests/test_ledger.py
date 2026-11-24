"""决策台账：只追加、可追溯、冲突历史不丢失。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from trial_tools.design import DesignEngine
from trial_tools.households import HouseholdResolver
from trial_tools.ledger import DecisionLedger

from ._fixtures import RecordsFixture


class LedgerTests(RecordsFixture):
    def setUp(self) -> None:
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "ledger.jsonl"
        self.ledger = DecisionLedger(self.path)

    def test_append_only_and_sequential_ids(self) -> None:
        e1 = self.ledger.append("enrollment", {"decision": "batch_0"})
        e2 = self.ledger.append("look", {"decision": "continue"})
        self.assertEqual(e1.entry_id, "E0001")
        self.assertEqual(e2.entry_id, "E0002")
        entries = self.ledger.read_all()
        self.assertEqual(len(entries), 2)
        # 物理文件也是两行，且没有任何覆盖写
        self.assertEqual(len(self.path.read_text(encoding="utf-8").strip().splitlines()), 2)

    def test_audit_trail_captures_three_questions(self) -> None:
        resolver = HouseholdResolver(self.data)
        engine = DesignEngine(self.data, resolver)
        plan = engine.build_next_plan(seed=7)
        self.ledger.append(
            kind="next_round_enrollment",
            payload={"selected_index": plan.selected_index},
            batch_ref={
                "seed": plan.rng_seed,
                "probabilities": list(plan.probabilities),
            },
            matured_results={"look_day": 16, "observed": 26},
            unresolved_conflicts=[
                {"household": h.household, "classes": list(h.classes)}
                for h in resolver.conflicted_households()
            ],
        )
        trail = self.ledger.audit_trail("E0001")
        self.assertEqual(trail["batch_used"]["seed"], 7)
        self.assertEqual(trail["matured_results_used"]["observed"], 26)
        conflicts = {c["household"] for c in trail["unresolved_conflicts"]}
        self.assertEqual(conflicts, {"F000", "F109"})

    def test_conflict_history_accumulates(self) -> None:
        self.ledger.append(
            "enrollment",
            {},
            unresolved_conflicts=[{"household": "F000", "classes": ["C00"]}],
        )
        self.ledger.append(
            "enrollment",
            {},
            unresolved_conflicts=[{"household": "F109", "classes": ["C13"]}],
        )
        history = self.ledger.conflict_history()
        self.assertEqual([item["entry_id"] for item in history], ["E0001", "E0002"])

    def test_replay_is_idempotent_across_runs(self) -> None:
        # 重新办理只新增记录，不改写旧记录
        self.ledger.append("enrollment", {"v": 1})
        again = DecisionLedger(self.path)
        again.append("enrollment", {"v": 2})
        entries = DecisionLedger(self.path).read_all()
        self.assertEqual([e.payload["v"] for e in entries], [1, 2])


if __name__ == "__main__":
    unittest.main()
