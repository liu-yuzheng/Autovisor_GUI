"""单实例互斥与激活信号测试。

只验证进程内行为: 同一个进程第二次创建同名命名互斥体, Windows 同样返回
ERROR_ALREADY_EXISTS, 正好用来模拟"另一个实例已经在跑"。
"""

import os
import sys
import tempfile
import unittest
import uuid
from unittest import mock

from modules import single_instance


class SingleInstanceTest(unittest.TestCase):
    def setUp(self):
        # 用独一无二的互斥名、并把信号目录指向临时目录:
        # 否则用户桌面真跑着一份程序时, 测试会互相干扰(它会把 activate.req 吃掉)
        self._real_mutex_name = single_instance.MUTEX_NAME
        self._tmp = tempfile.TemporaryDirectory()
        self._env = mock.patch.dict(os.environ, {"LOCALAPPDATA": self._tmp.name})
        self._env.start()
        self._patch = mock.patch.object(
            single_instance,
            "MUTEX_NAME",
            f"Local\\AutovisorGUI.Test.{uuid.uuid4().hex}",
        )
        self._patch.start()
        single_instance.release()
        single_instance.take_notify()

    def tearDown(self):
        single_instance.release()
        single_instance.take_notify()
        self._patch.stop()
        self._env.stop()
        self._tmp.cleanup()

    def test_first_acquire_succeeds(self):
        self.assertTrue(single_instance.acquire())

    def test_second_acquire_is_rejected(self):
        self.assertTrue(single_instance.acquire())
        self.assertFalse(single_instance.acquire(), "第二个实例应被拒绝")
        # 被拒绝的那次不能顺手关掉已持有的句柄
        self.assertFalse(single_instance.acquire())
        # 原持有者仍然有效: 释放后还能重新拿到
        single_instance.release()
        self.assertTrue(single_instance.acquire())

    def test_release_allows_acquire_again(self):
        self.assertTrue(single_instance.acquire())
        single_instance.release()
        self.assertTrue(single_instance.acquire())

    def test_notify_is_consumed_once(self):
        self.assertTrue(single_instance.notify_running())
        self.assertTrue(os.path.exists(single_instance.signal_path()))
        self.assertTrue(single_instance.take_notify())
        self.assertFalse(single_instance.take_notify(), "激活信号只能消费一次")
        self.assertFalse(os.path.exists(single_instance.signal_path()))

    def test_take_notify_without_signal(self):
        self.assertFalse(single_instance.take_notify())

    @unittest.skipUnless(sys.platform == "win32", "命名互斥体只在 Windows 上有意义")
    def test_mutex_name_is_session_local(self):
        # Local\ 前缀: 只在本登录会话内互斥, 多用户/远程桌面互不影响
        self.assertTrue(self._real_mutex_name.startswith("Local\\"))


if __name__ == "__main__":
    unittest.main()
