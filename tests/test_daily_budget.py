# encoding=utf-8
"""每日学习时长累计模块。"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules import daily_budget


class DailyBudgetTest(unittest.TestCase):
    def setUp(self):
        # 状态写到临时目录, 不要动真实的 %LOCALAPPDATA%\AutovisorGUI
        self._tmp = tempfile.TemporaryDirectory()
        self._env = mock.patch.dict(os.environ, {"LOCALAPPDATA": self._tmp.name})
        self._env.start()
        daily_budget.reset()

    def tearDown(self):
        daily_budget.reset()
        self._env.stop()
        self._tmp.cleanup()

    def test_starts_at_zero(self):
        self.assertEqual(daily_budget.seconds_today(), 0)
        self.assertFalse(daily_budget.reached(60))

    def test_add_accumulates(self):
        moment = datetime(2026, 11, 6, 9, 0, 0)
        self.assertEqual(daily_budget.add(30, moment), 30)
        self.assertEqual(daily_budget.add(45, moment), 75)
        self.assertEqual(daily_budget.seconds_today(moment), 75)

    def test_reached_uses_minutes(self):
        moment = datetime(2026, 11, 6, 9, 0, 0)
        daily_budget.add(59 * 60, moment)
        self.assertFalse(daily_budget.reached(60, moment))
        daily_budget.add(60, moment)  # 再补 1 分钟正好到 60
        self.assertTrue(daily_budget.reached(60, moment))
        # 上限改成 90 又不够了
        self.assertFalse(daily_budget.reached(90, moment))

    def test_zero_or_invalid_limit_never_reached(self):
        daily_budget.add(10 * 3600)
        self.assertFalse(daily_budget.reached(0))
        self.assertFalse(daily_budget.reached(""))
        self.assertFalse(daily_budget.reached("abc"))

    def test_resets_across_days(self):
        daily_budget.add(3600, datetime(2026, 11, 6, 23, 0, 0))
        self.assertEqual(daily_budget.seconds_today(datetime(2026, 11, 6, 23, 59)), 3600)
        # 第二天从零开始, 不会把昨天的时长算进来
        self.assertEqual(daily_budget.seconds_today(datetime(2026, 11, 7, 0, 1)), 0)
        self.assertFalse(daily_budget.reached(60, datetime(2026, 11, 7, 0, 2)))

    def test_reset_clears_today(self):
        daily_budget.add(600)
        self.assertTrue(daily_budget.reset())
        self.assertEqual(daily_budget.seconds_today(), 0)

    def test_broken_state_file_is_tolerated(self):
        with open(daily_budget.state_path(), "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self.assertEqual(daily_budget.seconds_today(), 0)
        self.assertEqual(daily_budget.add(60), 60)

    def test_state_file_outside_program_dir(self):
        daily_budget.add(60)
        self.assertTrue(os.path.isfile(daily_budget.state_path()))
        self.assertTrue(
            os.path.abspath(daily_budget.state_path()).startswith(
                os.path.abspath(self._tmp.name)
            )
        )
        with open(daily_budget.state_path(), encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual(data["seconds"], 60)
        self.assertIn("date", data)

    def test_format_duration(self):
        self.assertEqual(daily_budget.format_duration(0), "0 分钟")
        self.assertEqual(daily_budget.format_duration(59), "不足 1 分钟")
        self.assertEqual(daily_budget.format_duration(60), "1 分钟")
        self.assertEqual(daily_budget.format_duration(42 * 60), "42 分钟")
        self.assertEqual(daily_budget.format_duration(65 * 60), "1 小时 05 分")

    def test_limit_seconds(self):
        self.assertEqual(daily_budget.limit_seconds(60), 3600)
        self.assertEqual(daily_budget.limit_seconds(" 30 "), 1800)
        self.assertEqual(daily_budget.limit_seconds(0), 0)
        self.assertEqual(daily_budget.limit_seconds(-5), 0)
        self.assertEqual(daily_budget.limit_seconds("x"), 0)


if __name__ == "__main__":
    unittest.main()
