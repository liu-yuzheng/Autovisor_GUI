"""让窗口模式下的子进程不再弹黑色命令行窗口。

`build.py --gui` 用 PyInstaller 的 `-w` 打包, 只解决**主程序自己**的控制台。
程序跑起来以后还会派生一批控制台子进程:

- playwright 的驱动 `node.exe`(每次启动浏览器都会拉起);
- 源码模式下 pip / ensurepip;
- 其它第三方库随手起的 `cmd` / `curl` 之类。

GUI 程序没有控制台可以继承, Windows 会给每个控制台子进程**新建**一个黑窗,
用户看到的就是"刷课跑到一半突然冒出命令提示符"。这里在 `subprocess.Popen`
上做一次统一兜底: 调用方没有显式表达控制台意图时, 补上 CREATE_NO_WINDOW。
自己传了 creationflags 的调用(例如 GUI.py 里的 pip 已经带了 CREATE_NO_WINDOW)
保持原样, 不会互相覆盖。
"""

from __future__ import annotations

import subprocess
import sys

# Win32 常量: 不创建控制台窗口
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_CONSOLE = 0x00000010
DETACHED_PROCESS = 0x00000008

# 只要带了这几个中的任意一个, 说明调用方对控制台有明确要求, 不要替他决定
_CONSOLE_INTENT = CREATE_NO_WINDOW | CREATE_NEW_CONSOLE | DETACHED_PROCESS

_installed = False
# 补丁前的原始实现: 单独存一份, 方便测试替换, 也避免递归捕获到自己
_original_init = None


def with_no_window(flags: int) -> int:
    """按需给 creationflags 补上 CREATE_NO_WINDOW(纯函数, 便于单测)。"""
    if flags & _CONSOLE_INTENT:
        return flags
    return flags | CREATE_NO_WINDOW


def install() -> bool:
    """打一次补丁; 重复调用无副作用, 非 Windows 直接跳过。

    返回 True 表示补丁已在生效。
    """
    global _installed, _original_init
    if sys.platform != "win32":
        return False
    if _installed:
        return True

    _original_init = subprocess.Popen.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["creationflags"] = with_no_window(int(kwargs.get("creationflags") or 0))
        return _original_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = patched_init  # type: ignore[method-assign]
    _installed = True
    return True


def installed() -> bool:
    return _installed
