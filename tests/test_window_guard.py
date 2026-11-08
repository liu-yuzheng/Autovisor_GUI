"""window_guard 的真实窗口行为测试。

用 tkinter 建一个真窗口来验证:
- 隐藏后窗口缩到最小并移到屏幕左下角之外, 且没有被最小化/隐藏
- 移回后恢复原来的位置和尺寸
- 窗口被手动最小化后, ensure_alive() 能把它转成"左下角屏幕外窗口"
"""

import ctypes
import sys
import unittest

from modules import window_guard


@unittest.skipUnless(sys.platform == "win32", "窗口操作只在 Windows 上有意义")
class WindowGuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tkinter

        cls.tk = tkinter.Tk()
        cls.tk.title("AV_WINDOW_GUARD_TEST")
        cls.tk.geometry("320x200+120+120")
        cls.tk.update()
        cls.hwnd = ctypes.windll.user32.GetParent(cls.tk.winfo_id())

    @classmethod
    def tearDownClass(cls):
        try:
            ctypes.windll.user32.ShowWindow(cls.hwnd, 9)  # SW_RESTORE
            cls.tk.destroy()
        except Exception:
            pass

    def setUp(self):
        window_guard._state.update(
            {
                "hwnd": None,
                "origin": None,
                "mode": "onscreen",
                "miss": 0,
                "maximized": False,
            }
        )
        self.tk.state("normal")
        window_guard.watch(self.hwnd)
        window_guard.move_onscreen(self.hwnd)
        self.tk.update()

    def tearDown(self):
        # 最大化会带来边框偏移(-8), 会影响下一个用例对坐标的断言
        self.tk.state("normal")
        self.tk.update()

    def _rect(self):
        rect = ctypes.wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(self.hwnd, ctypes.byref(rect))
        return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top

    def test_work_area_public_wrapper(self):
        """小窗定位要用公开接口, 拿到的工作区应与内部实现一致。"""
        area = window_guard.work_area()
        left, top, right, bottom = area
        self.assertLess(left, right)
        self.assertLess(top, bottom)
        self.assertEqual(area, window_guard._work_area())

    def test_watch_records_origin(self):
        left, top, width, height = self._rect()
        self.assertEqual(window_guard._state["origin"][2], width)
        self.assertGreaterEqual(left, 0)

    def test_move_offscreen_keeps_window_normal(self):
        self.assertTrue(window_guard.move_offscreen(self.hwnd))
        self.tk.update()
        left, top, width, height = self._rect()
        _, _, _, work_bottom = window_guard._work_area()
        self.assertLessEqual(left + width, 0, "窗口应整体位于屏幕左侧之外")
        self.assertLessEqual(
            abs((top + height) - work_bottom), 60, "窗口底边应贴住屏幕底部"
        )
        user32 = ctypes.windll.user32
        self.assertFalse(user32.IsIconic(self.hwnd), "不能靠最小化实现隐藏")
        self.assertTrue(user32.IsWindowVisible(self.hwnd), "窗口应保持可见状态")
        self.assertLessEqual(width, 560, "隐藏时应缩到最小宽度")
        self.assertLessEqual(height, 160, "隐藏时应缩到最小高度")
        self.assertTrue(window_guard.is_offscreen(self.hwnd), "应判定为已停在左下角外")

    def test_move_onscreen_restores_size(self):
        # 直接指定"隐藏前"的尺寸, 避免依赖 tk 的窗口状态
        window_guard._state["origin"] = (120, 120, 400, 300)
        window_guard._state["maximized"] = False

        window_guard.move_offscreen(self.hwnd)
        self.tk.update()
        self.assertLessEqual(self._rect()[2], 560, "隐藏时应缩到最小")
        self.assertLessEqual(self._rect()[3], 160, "隐藏时应缩到最矮")

        window_guard.move_onscreen(self.hwnd)
        self.tk.update()
        width, height = self._rect()[2], self._rect()[3]
        self.assertEqual(width, 400, "显示时应恢复原宽度")
        self.assertEqual(height, 300, "显示时应恢复原高度")

    def test_move_onscreen_restores_position(self):
        window_guard.move_offscreen(self.hwnd)
        self.tk.update()
        self.assertTrue(window_guard.move_onscreen(self.hwnd))
        self.tk.update()
        left, _, _, _ = self._rect()
        self.assertGreaterEqual(left, 0, "移回后应落在可见区域")

    def test_ensure_alive_rescues_minimized_window(self):
        ctypes.windll.user32.ShowWindow(self.hwnd, 6)  # SW_MINIMIZE
        self.tk.update()
        self.assertTrue(ctypes.windll.user32.IsIconic(self.hwnd))

        result = window_guard.ensure_alive()
        self.tk.update()

        self.assertEqual(result, "restored")
        self.assertFalse(ctypes.windll.user32.IsIconic(self.hwnd))
        self.assertTrue(
            window_guard.is_offscreen(self.hwnd), "救回后应停在左下角外, 不打扰用户"
        )

    def test_ensure_alive_ignores_visible_window(self):
        self.assertIsNone(window_guard.ensure_alive())

    def test_restores_maximized_state(self):
        """隐藏时会取消最大化, 回到屏幕内要还原, 否则验证窗口变成小窗。"""
        self.tk.state("zoomed")
        self.tk.update()
        window_guard._state.update(
            {"hwnd": None, "origin": None, "maximized": False}
        )
        window_guard.watch(self.hwnd)
        self.assertTrue(window_guard._state["maximized"])

        window_guard.move_offscreen(self.hwnd)
        self.tk.update()
        self.assertFalse(ctypes.windll.user32.IsZoomed(self.hwnd))

        window_guard.move_onscreen(self.hwnd)
        self.tk.update()
        self.assertTrue(ctypes.windll.user32.IsZoomed(self.hwnd))

    def test_ensure_alive_forgets_closed_window(self):
        window_guard._state["hwnd"] = 99999999
        window_guard._state["miss"] = 2
        self.assertEqual(window_guard.ensure_alive(), "lost")
        self.assertIsNone(window_guard._state["hwnd"])


if __name__ == "__main__":
    unittest.main()
