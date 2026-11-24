"""输入核对：破坏契约的资料必须被拒绝；冻结批次不可被派发记录违背。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from trial_tools.records import RecordsError, load_records

from ._fixtures import RECORDS_PATH, RecordsFixture


class ValidationTests(RecordsFixture):
    def _write_broken(self, mutate) -> Path:
        raw = self.deepcopy_raw()
        mutate(raw)
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(raw, tmp, ensure_ascii=False)
        tmp.close()
        self.addCleanup(Path(tmp.name).unlink)
        return Path(tmp.name)

    def test_good_records_load(self) -> None:
        data = load_records(RECORDS_PATH)
        self.assertEqual(len(data.classes), 24)
        self.assertEqual(len(data.students), 144)

    def test_window_length_is_registered_convention(self) -> None:
        self.assertEqual(self.data.window_days(), 12)

    def test_dispatch_must_match_frozen_draw(self) -> None:
        path = self._write_broken(
            lambda raw: raw["dispatches"][0].__setitem__("student_arm", 1)
        )
        with self.assertRaises(RecordsError):
            load_records(path)

    def test_inconsistent_or_draw_violating_records_rejected(self) -> None:
        target_class = self.raw["dispatches"][0]["class"]

        def mutate(raw: dict) -> None:
            flipped = False
            for row in raw["dispatches"]:
                if row["class"] == target_class and not flipped:
                    row["student_arm"], row["adult_arm"] = 1 - row["student_arm"], 1 - row["adult_arm"]
                    flipped = True

        path = self._write_broken(mutate)
        with self.assertRaises(RecordsError):
            load_records(path)

    def test_probabilities_must_sum_to_one(self) -> None:
        path = self._write_broken(
            lambda raw: raw["original_draw"]["probabilities"].__setitem__(0, 0.9)
        )
        with self.assertRaises(RecordsError):
            load_records(path)

    def test_confirmed_capacity_cannot_exceed_available(self) -> None:
        path = self._write_broken(
            lambda raw: raw["next_round"][0].__setitem__(
                "confirmed_limit", raw["next_round"][0]["available"] + 1
            )
        )
        with self.assertRaises(RecordsError):
            load_records(path)

    def test_illegal_outcome_value_rejected(self) -> None:
        path = self._write_broken(
            lambda raw: raw["followup"][0].__setitem__("student_outcome", "到访了吗")
        )
        with self.assertRaises(RecordsError):
            load_records(path)

    def test_look_alpha_must_match_look_days(self) -> None:
        path = self._write_broken(lambda raw: raw["study"]["look_alpha"].pop())
        with self.assertRaises(RecordsError):
            load_records(path)


if __name__ == "__main__":
    unittest.main()
