"""端到端：CLI 全流程可重复办理，输出结构稳定。"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from trial_tools.cli import build_parser

from ._fixtures import RECORDS_PATH, RecordsFixture


class CliEndToEndTests(RecordsFixture):
    def setUp(self) -> None:
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger_path = Path(self.tmp.name) / "ledger.jsonl"

    def _run(self, argv: list[str]) -> dict:
        parser = build_parser()
        args = parser.parse_args(argv)
        buf = io.StringIO()
        with redirect_stdout(buf):
            args.func(args)
        return json.loads(buf.getvalue())

    def test_full_cycle(self) -> None:
        audit = self._run(["audit", str(RECORDS_PATH)])
        self.assertTrue(audit["common_support"])

        plan = self._run(
            [
                "plan",
                str(RECORDS_PATH),
                "--seed",
                "7",
                "--ledger",
                str(self.ledger_path),
            ]
        )
        self.assertTrue(plan["feasible"])
        self.assertEqual(len(plan["rows"]) + len(plan["service_only"]), 24)

        status = self._run(["status", str(RECORDS_PATH), "--look", "24"])
        self.assertIn("log_outside_window", status["student"]["censored_breakdown"])

        analysis = self._run(
            [
                "analyze",
                str(RECORDS_PATH),
                "--look",
                "24",
                "--comparisons",
                "4",
                "--ledger",
                str(self.ledger_path),
            ]
        )
        self.assertTrue(analysis["student_look_decision"]["is_final"])

        trace = self._run(["trace", str(self.ledger_path)])
        kinds = [item["kind"] for item in trace]
        self.assertEqual(kinds, ["next_round_enrollment", "look_analysis"])
        # 入组记录必须带得齐三个追溯要素
        first = trace[0]
        self.assertTrue(first["batch_used"])
        self.assertIn("unresolved_conflicts", first)

    def test_plan_reproducible_across_invocation(self) -> None:
        a = self._run(["plan", str(RECORDS_PATH), "--seed", "123"])
        b = self._run(["plan", str(RECORDS_PATH), "--seed", "123"])
        self.assertEqual(
            [(r["class"], r["student_arm"], r["adult_arm"]) for r in a["rows"]],
            [(r["class"], r["student_arm"], r["adult_arm"]) for r in b["rows"]],
        )


if __name__ == "__main__":
    unittest.main()
