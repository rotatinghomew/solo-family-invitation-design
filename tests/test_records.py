import json
from pathlib import Path
import unittest


class RecordsTest(unittest.TestCase):
    def test_known_input_contract(self):
        source = Path(__file__).resolve().parents[1] / "data" / "records.json"
        payload = json.loads(source.read_text(encoding="utf-8"))
        students = {item['student']: item for item in payload['students']}
        classes = {item['class']: item for item in payload['classes']}
        assert all(item['class'] in classes for item in students.values())
        assert len({(item['past_student_arm'], item['past_adult_arm']) for item in classes.values()}) == 4
        assert all(item['student'] in students for item in payload['followup'])
        assert payload['study']['look_days'] == sorted(payload['study']['look_days'])
