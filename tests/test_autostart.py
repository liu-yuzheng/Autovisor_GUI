"""开机自启动项的读写测试。

只操作临时值名(AutovisorGUI_Test_<pid>), 用完即删,
不会影响开发机上真实生效的 AutovisorGUI 启动项。
"""

import os
import sys
import unittest

from modules import autostart

if sys.platform == "win32":
    import winreg


@unittest.skipUnless(sys.platform == "win32", "开机自启动只在 Windows 上有意义")
class AutostartTest(unittest.TestCase):
    def setUp(self):
        self._orig_name = autostart.APP_NAME
        autostart.APP_NAME = f"AutovisorGUI_Test_{os.getpid()}"
        autostart.disable()

    def tearDown(self):
        autostart.disable()
        autostart.APP_NAME = self._orig_name

    def _write_raw(self, command):
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, autostart.RUN_KEY) as key:
            winreg.SetValueEx(key, autostart.APP_NAME, 0, winreg.REG_SZ, command)

    def test_launch_target_points_to_this_program(self):
        target = autostart.launch_target()
        self.assertTrue(target.startswith('"'), "路径应带引号, 避免空格被拆开")
        if getattr(sys, "frozen", False):
            self.assertIn(sys.executable, target)
        else:
            project_root = os.path.dirname(
                os.path.dirname(os.path.abspath(autostart.__file__))
            )
            self.assertIn("GUI.py", target)
            self.assertIn(project_root, target)

    def test_launch_target_carries_mini_flag_by_default(self):
        # 开机启动默认只显示小窗: 注册表命令必须带 --mini
        self.assertEqual(autostart.MINI_FLAG, "--mini")
        self.assertTrue(autostart.launch_target().endswith(autostart.MINI_FLAG))
        self.assertTrue(autostart.launch_target(startup_mini=True).endswith("--mini"))
        self.assertFalse(autostart.launch_target(startup_mini=False).endswith("--mini"))

    def test_launch_target_always_carries_boot_flag(self):
        # --boot 标记"这一次是开机拉起的", 自动开始刷课靠它判断
        self.assertEqual(autostart.BOOT_FLAG, "--boot")
        for mini in (True, False):
            target = autostart.launch_target(startup_mini=mini)
            self.assertTrue(
                target.endswith(autostart.BOOT_FLAG)
                or target.endswith(autostart.BOOT_FLAG + " " + autostart.MINI_FLAG),
                f"缺少开机标记: {target}",
            )

    def test_enable_respects_startup_mode(self):
        autostart.enable(startup_mini=False)
        self.assertFalse(autostart.registered_target().endswith(autostart.MINI_FLAG))
        # 已存在时改成小窗模式, 应原地覆盖而不是新增一项
        self.assertTrue(autostart.enable(startup_mini=True))
        self.assertTrue(autostart.registered_target().endswith(autostart.MINI_FLAG))

    def test_sync_adds_mini_flag_to_old_entry(self):
        # 老版本写入的命令没有 --mini, 启动时应自动补上
        self._write_raw(autostart.launch_target(startup_mini=False))
        self.assertTrue(autostart.sync())
        self.assertEqual(autostart.registered_target(), autostart.launch_target(True))

    def test_enable_then_disable(self):
        self.assertFalse(autostart.is_enabled())
        self.assertTrue(autostart.enable())
        self.assertTrue(autostart.is_enabled())
        self.assertEqual(autostart.registered_target(), autostart.launch_target())

        self.assertTrue(autostart.disable())
        self.assertFalse(autostart.is_enabled())

    def test_disable_missing_entry_is_success(self):
        # 重复关闭不该报错, 否则界面上的取消勾选会莫名弹窗
        self.assertTrue(autostart.disable())
        self.assertTrue(autostart.disable())

    def test_set_enabled_maps_to_enable_and_disable(self):
        self.assertTrue(autostart.set_enabled(True))
        self.assertTrue(autostart.is_enabled())
        self.assertTrue(autostart.set_enabled(False))
        self.assertFalse(autostart.is_enabled())

    def test_sync_rewrites_stale_path(self):
        # 模拟"程序目录被移动过": 注册表里还是旧路径
        self._write_raw(r'"C:\old\location\GUI.py"')
        self.assertTrue(autostart.sync())
        self.assertEqual(autostart.registered_target(), autostart.launch_target())

    def test_sync_noop_when_already_correct(self):
        autostart.enable()
        self.assertFalse(autostart.sync(), "路径没变就不该重写注册表")

    def test_sync_does_not_hijack_another_existing_program(self):
        # 注册表里是另一份仍然有效的程序(比如打包版 exe), 这次只是在源码里跑一下:
        # 不该被改写成源码路径
        other = f'"{sys.executable}" "D:\\somewhere\\GUI.py" --boot --mini'
        self._write_raw(other)
        self.assertFalse(autostart.sync())
        self.assertEqual(autostart.registered_target(), other)

    def test_sync_ignores_disabled(self):
        self.assertFalse(autostart.sync(), "没启用自启动时不该擅自创建启动项")
        self.assertFalse(autostart.is_enabled())


if __name__ == "__main__":
    unittest.main()
