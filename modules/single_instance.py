"""单实例控制: 开机自启动与正常启动共用同一条通道。

开机自启(exe + `--boot --mini`)和手动双击走的是同一个入口。没有互斥时,
两者会各起一个进程同时刷同一门课, 同一账号下互相抢进度、抢浏览器。
这里用 Windows 命名互斥体做进程级互斥:

- 第一个进程拿到互斥体, 正常往下走;
- 后来的进程发现已被占用, 写一个"激活信号"文件后立刻退出;
- 已运行的实例在下一次状态轮询(250ms)里看到信号, 把自己的窗口唤到前台,
  于是"双击了但看起来没反应"变成"窗口弹出来"。

为什么用文件传信号, 而不是 FindWindow + ShowWindow 或 WM_APP:
- 小窗模式下主窗口是 withdraw 的, 外部 ShowWindow 会让 Tk 的内部状态错乱;
- Tk 不处理自定义窗口消息, PostMessage 没人接收。
文件 + 轮询是两边都能理解的最小协议, 也不依赖窗口标题。

互斥体名字用 `Local\\` 前缀: 只在本登录会话内生效, 多用户/远程桌面互不干扰,
同时也不需要管理员权限。
"""

import atexit
import os
import sys
import tempfile
import time

MUTEX_NAME = "Local\\AutovisorGUI.SingleInstance"
ERROR_ALREADY_EXISTS = 183

_handle = None


def _user_state_dir():
    """放激活信号的目录: %LOCALAPPDATA%\\AutovisorGUI, 取不到就退回临时目录。"""
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    path = os.path.join(base, "AutovisorGUI")
    try:
        os.makedirs(path, exist_ok=True)
        return path
    except OSError:
        return tempfile.gettempdir()


def signal_path():
    return os.path.join(_user_state_dir(), "activate.req")


def acquire():
    """抢占单实例所有权。

    返回 False 表示已有实例在跑, 调用方应该通知它并退出。
    非 Windows 平台或拿不到互斥体时一律放行: 宁可可能多开, 也不要打不开程序。
    """
    global _handle
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

        handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if not handle:
            return True
        # GetLastError 必须在创建后立刻读: 后续任何 Win32 调用都会覆盖它
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        _handle = handle
        atexit.register(release)
        return True
    except Exception:
        return True


def release():
    """主动释放互斥体; 进程退出时也会自动释放。"""
    global _handle
    if _handle is None:
        return
    try:
        import ctypes

        ctypes.windll.kernel32.CloseHandle(_handle)
    except Exception:
        pass
    _handle = None


def notify_running():
    """告诉已在运行的实例: 又有人启动了, 请把窗口显示出来。"""
    try:
        with open(signal_path(), "w", encoding="utf-8") as handle:
            handle.write(str(time.time()))
        return True
    except OSError:
        return False


def take_notify():
    """消费激活信号(只会生效一次); 有信号返回 True。"""
    path = signal_path()
    try:
        if not os.path.exists(path):
            return False
        os.remove(path)
        return True
    except OSError:
        return False
