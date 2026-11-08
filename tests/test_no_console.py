import subprocess
import sys
import unittest

from modules import no_console


class WithNoWindowTest(unittest.TestCase):
    def test_plain_flags_get_no_window(self):
        self.assertEqual(no_console.with_no_window(0), no_console.CREATE_NO_WINDOW)

    def test_existing_no_window_untouched(self):
        flags = no_console.CREATE_NO_WINDOW
        self.assertEqual(no_console.with_no_window(flags), flags)

    def test_new_console_is_respected(self):
        # 调用方明确要新开控制台时不要覆盖, 否则 CREATE_NO_WINDOW|CREATE_NEW_CONSOLE
        # 是非法组合, CreateProcess 会直接失败
        self.assertEqual(
            no_console.with_no_window(no_console.CREATE_NEW_CONSOLE),
            no_console.CREATE_NEW_CONSOLE,
        )

    def test_detached_process_is_respected(self):
        self.assertEqual(
            no_console.with_no_window(no_console.DETACHED_PROCESS),
            no_console.DETACHED_PROCESS,
        )

    def test_other_flags_are_preserved(self):
        # 例如 CREATE_NEW_PROCESS_GROUP(0x200), 不能被吃掉
        self.assertEqual(
            no_console.with_no_window(0x200),
            0x200 | no_console.CREATE_NO_WINDOW,
        )


@unittest.skipUnless(sys.platform == "win32", "CREATE_NO_WINDOW 只在 Windows 上有效")
class InstallTest(unittest.TestCase):
    def test_install_is_idempotent(self):
        self.assertTrue(no_console.install())
        self.assertTrue(no_console.install())
        self.assertTrue(no_console.installed())

    def test_subprocess_still_works_after_patch(self):
        no_console.install()
        proc = subprocess.run(
            [sys.executable, "-c", "print('ok')"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn(b"ok", proc.stdout)

    def test_patched_popen_injects_flag(self):
        """真的走一次 Popen, 用桩替换底层实现, 确认 creationflags 被补上。"""
        no_console.install()
        captured = {}

        def fake_init(self, *args, **kwargs):
            captured.update(kwargs)
            raise RuntimeError("stop here")

        real = no_console._original_init
        no_console._original_init = fake_init
        try:
            with self.assertRaises(RuntimeError):
                subprocess.Popen(["whatever"])
        finally:
            no_console._original_init = real

        self.assertEqual(captured.get("creationflags"), no_console.CREATE_NO_WINDOW)

    def test_explicit_flags_survive_the_patch(self):
        no_console.install()
        captured = {}

        def fake_init(self, *args, **kwargs):
            captured.update(kwargs)
            raise RuntimeError("stop here")

        real = no_console._original_init
        no_console._original_init = fake_init
        try:
            with self.assertRaises(RuntimeError):
                subprocess.Popen(["whatever"], creationflags=no_console.CREATE_NEW_CONSOLE)
        finally:
            no_console._original_init = real

        self.assertEqual(captured.get("creationflags"), no_console.CREATE_NEW_CONSOLE)


if __name__ == "__main__":
    unittest.main()
