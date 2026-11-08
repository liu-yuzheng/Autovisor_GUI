# encoding=utf-8
"""整门课学完时自动跳到下一个课程链接的行为。

背景: 原逻辑在"待学课时=0"时会转入复习模式把整门课重播一遍,
多课程配置下表现为"这门课明明播完了却一直不往下走"。
skipCompletedCourse(默认 True)关掉这个复习分支。
"""

import asyncio
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from modules.configs import Config
from modules.course_runner import CourseOutcome, run_course


class _Logger:
    """只记录调用, 不写文件。"""

    def __init__(self):
        self.events = []

    def info(self, message="", **kwargs):
        self.events.append(("info", message))

    def warn(self, message="", **kwargs):
        self.events.append(("warn", message))

    def error(self, message="", **kwargs):
        self.events.append(("error", message))

    def event(self, name="", **kwargs):
        self.events.append(("event", name))

    def context(self, **kwargs):
        pass

    def debug(self, message="", **kwargs):
        pass


def _make_config(test, extra_course_options=""):
    """生成一份临时 config.ini + mirrors.json, 测试结束再删。

    Config 的属性是惰性读取, 文件必须留到断言之后。
    """
    content = (
        "[user-account]\nusername=\npassword=\n"
        "[browser-option]\ndriver=edge\nEXE_PATH=\n"
        "[script-option]\nenableAutoCaptcha=true\nenableHideWindow=false\nshowDonateCode=true\n"
        "[course-option]\nsoundOff=true\n" + extra_course_options +
        "[course-url]\nURL1=\n"
    )
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as file:
        file.write(content)
        path = file.name
    mirrors_path = path + ".mirrors.json"
    with open(mirrors_path, "w", encoding="utf-8") as file:
        file.write('{"primary": "https://mirror.example/simple"}')
    test.addCleanup(os.remove, path)
    test.addCleanup(os.remove, mirrors_path)
    return Config(path, mirrors_path)


class SkipCompletedCourseConfigTests(unittest.TestCase):
    def test_defaults_to_true(self):
        self.assertTrue(_make_config(self).skipCompletedCourse)

    def test_can_be_turned_off(self):
        self.assertFalse(
            _make_config(self, "skipCompletedCourse = False\n").skipCompletedCourse
        )


class RunCourseSkipTests(unittest.TestCase):
    def setUp(self):
        self.page = AsyncMock()
        self.catalog = SimpleNamespace(item=".item", name="legacy")
        self.logger = _Logger()
        self.calls = []

    def _patch_filtered(self, result):
        async def fake_filtered(page, catalog, include_all=False):
            self.calls.append(include_all)
            return result

        return patch("modules.course_runner.get_filtered_class", fake_filtered)

    def _run(self, config):
        return asyncio.run(
            run_course(
                self.page,
                self.catalog,
                config,
                self.logger,
                asyncio.Event(),
            )
        )

    def test_completed_course_returns_completed_without_replay(self):
        config = SimpleNamespace(skipCompletedCourse=True)
        with self._patch_filtered([]):
            outcome = self._run(config)
        self.assertIs(outcome, CourseOutcome.COMPLETED)
        # 只查了"待学课时", 没有再取全量课时去重播
        self.assertEqual(self.calls, [False])
        self.assertTrue(
            any("已完成" in message for level, message in self.logger.events if level == "info")
        )

    def test_keeps_review_mode_when_disabled(self):
        config = SimpleNamespace(skipCompletedCourse=False)
        with self._patch_filtered([]):
            outcome = self._run(config)
        # 旧行为: 取全量课时(复习), 本例没有课时 → 判失败
        self.assertEqual(self.calls, [False, True])
        self.assertIs(outcome, CourseOutcome.FAILED)
