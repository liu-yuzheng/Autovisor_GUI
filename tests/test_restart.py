# encoding=utf-8
"""失败重启: [restart] 配置解析 + supervise() 的尝试次数与退出码。"""

import asyncio
import json
import os
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

import Autovisor
from modules.configs import Config, ConfigError
from modules.logger import Logger

_BASE_CONFIG = """
[user-account]
username =
password =
[browser-option]
driver = Edge
EXE_PATH =
[script-option]
enableAutoCaptcha = False
enableHideWindow = False
showDonateCode = False
[course-option]
limitMaxTime = 30
limitSpeed = 1.0
soundOff = True
[course-url]
URL1 = https://studyvideoh5.zhihuishu.com/stuStudy?courseId=1
"""


def _build_config(restart_section=""):
    """在临时目录里生成一份最小可用的配置, 返回 Config 对象。"""
    tmp = tempfile.mkdtemp(prefix="autovisor-cfg-")
    config_path = os.path.join(tmp, "config.ini")
    mirrors_path = os.path.join(tmp, "mirrors.json")
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(_BASE_CONFIG + restart_section)
    with open(mirrors_path, "w", encoding="utf-8") as f:
        json.dump({"tuna": "https://pypi.tuna.tsinghua.edu.cn/simple"}, f)
    return Config(config_path, mirrors_path)


class RestartConfigTest(unittest.TestCase):
    def test_default_schedule(self):
        cfg = _build_config()
        self.assertTrue(cfg.enableRestart)
        self.assertEqual(cfg.backoffSchedule, (10, 10, 10, 20, 20, 30))
        self.assertEqual(cfg.maxAttempts, 7)

    def test_explicit_schedule(self):
        cfg = _build_config("[restart]\nbackoffSchedule = 5,5,15\n")
        self.assertEqual(cfg.backoffSchedule, (5, 5, 15))
        self.assertEqual(cfg.maxAttempts, 4)

    def test_schedule_accepts_chinese_separators(self):
        cfg = _build_config("[restart]\nbackoffSchedule = 10，10; 30\n")
        self.assertEqual(cfg.backoffSchedule, (10, 10, 30))

    def test_empty_schedule_means_no_retry(self):
        cfg = _build_config("[restart]\nbackoffSchedule =\n")
        self.assertEqual(cfg.backoffSchedule, ())
        self.assertEqual(cfg.maxAttempts, 1)

    def test_single_value_is_fixed_interval(self):
        cfg = _build_config("[restart]\nbackoffSchedule = 7\n")
        self.assertEqual(cfg.backoffSchedule, (7,))

    def test_restart_can_be_disabled(self):
        cfg = _build_config("[restart]\nenableRestart = False\n")
        self.assertFalse(cfg.enableRestart)

    def test_legacy_fields_are_honoured(self):
        cfg = _build_config("[restart]\nmaxRetries = 2\nbackoffSec = 15\n")
        self.assertEqual(cfg.backoffSchedule, (15, 15))

    def test_schedule_is_clamped(self):
        cfg = _build_config("[restart]\nbackoffSchedule = 0,99999\n")
        self.assertEqual(cfg.backoffSchedule, (0, 3600))


class SuperviseTest(unittest.TestCase):
    def setUp(self):
        Autovisor.reset_stop()
        Autovisor.logger = Logger()
        self.addCleanup(Autovisor.reset_stop)

    @staticmethod
    def _cfg(schedule, enabled=True):
        return types.SimpleNamespace(backoffSchedule=schedule, enableRestart=enabled)

    def test_success_on_first_attempt(self):
        calls = []

        async def fake_main(_config):
            calls.append(1)
            return True

        with mock.patch.object(Autovisor, "main", fake_main):
            code = asyncio.run(Autovisor.supervise(self._cfg((0, 0))))
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 1)

    def test_retries_until_schedule_is_exhausted(self):
        calls = []

        async def fake_main(_config):
            calls.append(1)
            return False

        with mock.patch.object(Autovisor, "main", fake_main):
            code = asyncio.run(Autovisor.supervise(self._cfg((0, 0))))
        self.assertEqual(code, 1)
        self.assertEqual(len(calls), 3)  # 首轮 + 2 次重试

    def test_stops_after_successful_retry(self):
        calls = []

        async def fake_main(_config):
            calls.append(1)
            return len(calls) >= 2

        with mock.patch.object(Autovisor, "main", fake_main):
            code = asyncio.run(Autovisor.supervise(self._cfg((0, 0, 0))))
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 2)

    def test_exception_counts_as_failure_and_retries(self):
        calls = []

        async def fake_main(_config):
            calls.append(1)
            raise RuntimeError("浏览器没了")

        with mock.patch.object(Autovisor, "main", fake_main):
            code = asyncio.run(Autovisor.supervise(self._cfg((0,))))
        self.assertEqual(code, 1)
        self.assertEqual(len(calls), 2)

    def test_config_error_is_not_retried(self):
        calls = []

        async def fake_main(_config):
            calls.append(1)
            raise ConfigError("配置文件缺少必要配置段")

        with mock.patch.object(Autovisor, "main", fake_main):
            code = asyncio.run(Autovisor.supervise(self._cfg((0, 0, 0))))
        self.assertEqual(code, 2)
        self.assertEqual(len(calls), 1)

    def test_dependency_install_failure_is_not_retried(self):
        calls = []

        async def fake_main(_config):
            calls.append(1)
            raise SystemExit(-1)

        with mock.patch.object(Autovisor, "main", fake_main):
            code = asyncio.run(Autovisor.supervise(self._cfg((0, 0, 0))))
        self.assertEqual(code, 2)
        self.assertEqual(len(calls), 1)

    def test_disabled_restart_runs_once(self):
        calls = []

        async def fake_main(_config):
            calls.append(1)
            return False

        with mock.patch.object(Autovisor, "main", fake_main):
            code = asyncio.run(Autovisor.supervise(self._cfg((0, 0), enabled=False)))
        self.assertEqual(code, 1)
        self.assertEqual(len(calls), 1)

    def test_stop_request_before_start(self):
        calls = []

        async def fake_main(_config):
            calls.append(1)
            return True

        Autovisor.request_stop()
        with mock.patch.object(Autovisor, "main", fake_main):
            code = asyncio.run(Autovisor.supervise(self._cfg((0,))))
        self.assertEqual(code, 1)
        self.assertEqual(len(calls), 0)

    def test_stop_interrupts_backoff_wait(self):
        calls = []

        async def fake_main(_config):
            calls.append(1)
            return False

        timer = threading.Timer(0.3, Autovisor.request_stop)
        timer.start()
        started = time.monotonic()
        try:
            with mock.patch.object(Autovisor, "main", fake_main):
                code = asyncio.run(Autovisor.supervise(self._cfg((60,))))
        finally:
            timer.cancel()
        self.assertEqual(code, 1)
        self.assertEqual(len(calls), 1)
        self.assertLess(time.monotonic() - started, 10, "停止没能打断退避等待")


if __name__ == "__main__":
    unittest.main()
