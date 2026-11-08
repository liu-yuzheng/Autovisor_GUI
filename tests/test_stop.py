# encoding=utf-8
"""停止流程: request_stop 必须取消任务, 而不是硬停事件循环。"""

import asyncio
import threading
import unittest

import Autovisor


class RequestStopTest(unittest.TestCase):
    def setUp(self):
        Autovisor.reset_stop()
        Autovisor._running_loop = None
        Autovisor._running_task = None
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        Autovisor.reset_stop()
        Autovisor._running_loop = None
        Autovisor._running_task = None

    def _start(self):
        """在子线程里跑一个长任务, 复刻 GUI 工作线程的最小结构。"""
        state = {}
        started = threading.Event()
        loop = asyncio.new_event_loop()

        def run():
            asyncio.set_event_loop(loop)

            async def supervised():
                try:
                    started.set()
                    await asyncio.sleep(30)
                finally:
                    state["cleaned"] = True

            task = loop.create_task(supervised())
            Autovisor._running_loop = loop
            Autovisor._running_task = task
            try:
                loop.run_until_complete(task)
            except asyncio.CancelledError:
                state["cancelled"] = True
            finally:
                try:
                    Autovisor._running_loop = None
                    Autovisor._running_task = None
                    loop.close()
                except Exception:
                    pass

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.assertTrue(started.wait(5), "任务没有启动")
        return thread, state

    def test_stop_cancels_task_and_runs_finally(self):
        thread, state = self._start()
        Autovisor.request_stop()
        thread.join(5)
        self.assertFalse(thread.is_alive(), "停止后任务仍在运行")
        self.assertTrue(state.get("cancelled"), "任务没有被取消")
        # finally 必须执行: 浏览器就是在这一步关掉的
        self.assertTrue(state.get("cleaned"), "任务的 finally 没有执行")
        self.assertTrue(Autovisor.STOP_REQUESTED.is_set())

    def test_reset_stop_clears_flag(self):
        Autovisor.request_stop()
        self.assertTrue(Autovisor.STOP_REQUESTED.is_set())
        Autovisor.reset_stop()
        self.assertFalse(Autovisor.STOP_REQUESTED.is_set())

    def test_stop_without_running_loop_is_safe(self):
        Autovisor.request_stop()
        self.assertTrue(Autovisor.STOP_REQUESTED.is_set())


if __name__ == "__main__":
    unittest.main()
