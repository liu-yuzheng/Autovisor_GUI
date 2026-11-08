"""开机自启动: 只写当前用户的 Run 注册表项。

为什么用注册表而不是启动文件夹 / 计划任务:
- 不需要管理员权限, 开关随时生效, 也不会留下需要手动清理的快捷方式;
- 只影响当前用户, 与"以本程序为个人工具"的使用方式一致;
- 位置固定(HKCU\\...\\CurrentVersion\\Run), 界面可以直接读回真实状态。

启动命令按运行方式自动选择:
- 打包成 exe 后: 直接指向 exe 自己;
- 源码运行: 优先 pythonw.exe + GUI.py 的绝对路径, 开机启动时不会弹控制台黑窗。

默认追加 `--boot --mini`: 开机那次不弹主窗口, 直接以屏幕左下角的小窗出现,
不会在登录瞬间挡住桌面; 配合"开机后自动开始刷课"还能省掉手动点开始。
界面上的勾选项可以改掉这两个行为。

程序目录被移动后, 这条注册表项会指向旧路径。`sync()` 会在每次启动时
比对命令, 发现过期就改写成本次运行的路径。
"""

import os
import sys

if sys.platform == "win32":
    import winreg

# 注册表里的值名, 也是"是否已加入自启动"的唯一判据
APP_NAME = "AutovisorGUI"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

# 开机自启动的标记: --boot 表示"这一次是开机拉起的", --mini 表示"只显示小窗"。
# GUI.py 在解析命令行前会把它们摘掉, 不会落到 CLI 分支。
BOOT_FLAG = "--boot"
MINI_FLAG = "--mini"


def available() -> bool:
    """只有 Windows 有这套机制。"""
    return sys.platform == "win32"


def launch_target(startup_mini: bool = True) -> str:
    """本次运行对应的启动命令(带引号, 路径含空格也不会被拆开)。"""
    if getattr(sys, "frozen", False):
        target = f'"{sys.executable}"'
    else:
        script = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "GUI.py"
        )
        python = sys.executable
        # 开机启动不需要控制台, 有 pythonw 就用它, 避免每次开机弹一个黑窗
        pythonw = os.path.join(os.path.dirname(python), "pythonw.exe")
        if os.path.exists(pythonw):
            python = pythonw
        target = f'"{python}" "{script}"'
    args = [BOOT_FLAG]
    if startup_mini:
        args.append(MINI_FLAG)
    return target + " " + " ".join(args)


def registered_target():
    """返回注册表里当前记录的命令; 未启用或读不到时返回 None。"""
    if not available():
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
            return value or None
    except FileNotFoundError:
        return None
    except OSError:
        return None


def is_enabled() -> bool:
    return bool(registered_target())


def enable(startup_mini: bool = True) -> bool:
    """写入自启动项; 已存在则覆盖(顺带修正过期路径与启动形态)。"""
    if not available():
        return False
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(
                key, APP_NAME, 0, winreg.REG_SZ, launch_target(startup_mini)
            )
        return True
    except OSError:
        return False


def disable() -> bool:
    """删除自启动项; 本来就没有也算成功。"""
    if not available():
        return False
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, APP_NAME)
        return True
    except FileNotFoundError:
        return True  # 值或键不存在, 目标状态已达成
    except OSError:
        return False


def set_enabled(enabled: bool, startup_mini: bool = True) -> bool:
    return enable(startup_mini) if enabled else disable()


def _command_program(command: str):
    """从启动命令里取出可执行文件路径: 支持带引号/不带引号两种写法。"""
    text = (command or "").strip()
    if not text:
        return None
    if text.startswith('"'):
        end = text.find('"', 1)
        return text[1:end] if end > 1 else None
    return text.split()[0]


def command_exists(command: str) -> bool:
    """注册表里那条命令指向的程序还在不在。"""
    program = _command_program(command)
    if not program:
        return False
    return os.path.exists(program)


def sync(startup_mini: bool = True) -> bool:
    """注册表里的命令失效(目标被移动/删除)时, 静默更新成本次运行的路径。

    只有两种情况会改写:
    - 旧命令指向的程序已经不存在(程序目录被移动/删除);
    - 旧命令就是本程序, 只是启动参数变了(老版本没写 --mini 之类)。
    其余一律不动 —— 否则在 PyCharm/命令行里用另一个解释器跑一次源码,
    就会把指向 exe 的自启动项悄悄改成源码路径, 用户并没有想换。

    返回 True 表示确实改写过。
    """
    current = registered_target()
    if not current:
        return False
    target = launch_target(startup_mini)
    if current.strip() == target:
        return False
    old_program = _command_program(current)
    new_program = _command_program(target)
    same_program = bool(old_program and new_program) and os.path.normcase(
        old_program
    ) == os.path.normcase(new_program)
    if not same_program and command_exists(current):
        return False
    return enable(startup_mini)
