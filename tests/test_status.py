# encoding=utf-8
"""状态总线(modules.status): 进度换算 / 日志摘要 / 订阅。"""

import unittest

from modules.status import (
    STATE_ERROR,
    STATE_FINISHED,
    STATE_IDLE,
    STATE_RUNNING,
    StatusBus,
    format_brief,
)


def _fresh_bus():
    """构造一个独立实例, 避免影响进程内单例。"""
    bus = object.__new__(StatusBus)
    bus._setup()
    return bus


class _StubLogger:
    def __init__(self):
        self.sink = None

    def add_sink(self, fn):
        self.sink = fn


class StatusBusTest(unittest.TestCase):
    def test_new_bus_is_idle(self):
        snap = _fresh_bus().snapshot()
        self.assertEqual(snap["state"], STATE_IDLE)
        self.assertFalse(snap["active"])
        self.assertEqual(snap["percent"], 0.0)

    def test_begin_resets_progress(self):
        bus = _fresh_bus()
        bus.set_percent(88)
        bus.begin(course_total=2)
        snap = bus.snapshot()
        self.assertEqual(snap["state"], "preparing")
        self.assertEqual(snap["percent"], 0.0)
        self.assertEqual(snap["course_total"], 2)

    def test_percent_follows_course_and_lesson(self):
        bus = _fresh_bus()
        bus.begin(course_total=2)
        bus.set_state(STATE_RUNNING)
        bus.set_course(index=2, total=2)
        bus.set_lesson_scope(4)
        bus.set_lesson(2, 4)
        bus.set_lesson_percent(50)
        # 第 1 门已完成 + 第 2 门里 1.5/4 ≈ 37.5%
        self.assertAlmostEqual(bus.snapshot()["percent"], 68.8, places=1)

    def test_lesson_percent_ignores_tiny_changes(self):
        bus = _fresh_bus()
        bus.set_lesson(1, 1)
        bus.set_lesson_percent(10.0)
        bus.set_lesson_percent(10.2)
        self.assertEqual(bus.snapshot()["lesson_percent"], 10.0)

    def test_percent_override_wins(self):
        bus = _fresh_bus()
        bus.begin(course_total=3)
        bus.set_percent(100)
        self.assertEqual(bus.snapshot()["percent"], 100.0)

    def test_subscribe_receives_snapshot(self):
        bus = _fresh_bus()
        seen = []
        bus.subscribe(seen.append)
        bus.set_state(STATE_ERROR, "出错了")
        self.assertTrue(seen)
        self.assertEqual(seen[-1]["state"], STATE_ERROR)
        self.assertEqual(seen[-1]["error"], "出错了")

    def test_broken_listener_does_not_raise(self):
        bus = _fresh_bus()

        def boom(_snapshot):
            raise RuntimeError("listener 崩了")

        bus.subscribe(boom)
        bus.set_state(STATE_FINISHED, "完成")  # 不应抛出
        self.assertEqual(bus.snapshot()["state"], STATE_FINISHED)


class LogBridgeTest(unittest.TestCase):
    def test_event_does_not_replace_message(self):
        bus = _fresh_bus()
        stub = _StubLogger()
        bus.attach_logger(stub)
        bus.set_message("正在学习第二课时")
        stub.sink("EVENT", "[12:00:00.000] [EVENT] [modules.x:1] 播放状态 | 进度=50%")
        self.assertEqual(bus.snapshot()["message"], "正在学习第二课时")

    def test_info_updates_message(self):
        bus = _fresh_bus()
        stub = _StubLogger()
        bus.attach_logger(stub)
        stub.sink("WARN", "[12:00:00.000] [WARN] [modules.x:1] 视频停滞,停止当前课时.")
        snap = bus.snapshot()
        self.assertEqual(snap["message"], "视频停滞,停止当前课时.")
        self.assertEqual(snap["message_level"], "WARN")

    def test_section_title_is_ignored(self):
        bus = _fresh_bus()
        stub = _StubLogger()
        bus.attach_logger(stub)
        bus.set_message("原消息")
        stub.sink("INFO", "[12:00:00.000] [INFO] [modules.x:1] ===== 登录 =====")
        self.assertEqual(bus.snapshot()["message"], "原消息")


class FormatBriefTest(unittest.TestCase):
    def test_running_contains_course_and_lesson(self):
        bus = _fresh_bus()
        bus.begin(course_total=3)
        bus.set_state(STATE_RUNNING)
        bus.set_course(index=2, total=3)
        bus.set_lesson(1, 5, "第一章")
        text = format_brief(bus.snapshot())
        self.assertIn("课程 2/3", text)
        self.assertIn("课时 1/5", text)

    def test_error_shows_reason(self):
        bus = _fresh_bus()
        bus.set_state(STATE_ERROR, "浏览器启动失败")
        self.assertIn("浏览器启动失败", format_brief(bus.snapshot()))

    def test_idle_text(self):
        self.assertTrue(format_brief(_fresh_bus().snapshot()))


if __name__ == "__main__":
    unittest.main()
