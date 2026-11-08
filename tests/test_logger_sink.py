# encoding=utf-8
"""日志回调(sink): GUI 日志面板依赖它拿到实时日志。"""

import unittest

from modules.logger import Logger


class LoggerSinkTest(unittest.TestCase):
    def setUp(self):
        self.logger = Logger()
        self.received = []
        self.sink = lambda level, text: self.received.append((level, text))
        self.logger.add_sink(self.sink)
        self.addCleanup(self.logger.remove_sink, self.sink)

    def _levels(self):
        return [level for level, _ in self.received]

    def test_info_dispatched_with_level(self):
        self.logger.info("测试一条信息")
        matched = [level for level, text in self.received if "测试一条信息" in text]
        self.assertEqual(matched, ["INFO"])

    def test_dispatch_records_full_line(self):
        self.logger.warn("磁盘快满了")
        level, text = self.received[-1]
        self.assertEqual(level, "WARN")
        self.assertIn("磁盘快满了", text)
        self.assertIn("[WARN]", text)

    def test_event_level_is_forwarded(self):
        self.logger.event("课程目录", 类型="共享课")
        self.assertEqual(self._levels()[-1], "EVENT")

    def test_add_sink_is_idempotent(self):
        self.logger.add_sink(self.sink)
        self.logger.info("只应收到一次")
        self.assertEqual(sum(1 for _, text in self.received if "只应收到一次" in text), 1)

    def test_broken_sink_does_not_break_logging(self):
        def boom(_level, _text):
            raise RuntimeError("面板崩了")

        self.logger.add_sink(boom)
        self.addCleanup(self.logger.remove_sink, boom)
        self.logger.info("主流程不受影响")
        self.assertEqual(self._levels()[-1], "INFO")

    def test_remove_sink_stops_delivery(self):
        self.logger.remove_sink(self.sink)
        self.logger.info("不该再收到")
        self.assertFalse(any("不该再收到" in text for _, text in self.received))


class SummarizeExceptionTest(unittest.TestCase):
    def test_empty_message_exception(self):
        # 空消息的异常以前会让 summarize_exception 抛 IndexError
        self.assertEqual(
            Logger.summarize_exception(ValueError()), "ValueError: ValueError()"
        )


if __name__ == "__main__":
    unittest.main()
