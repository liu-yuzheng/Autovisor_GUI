"""窗口保活: 让浏览器窗口"缩到最小, 停在屏幕左下角之外, 但从未被最小化"。

背景
----
智慧树等平台的播放器会检测页面是否可见 / 是否有焦点。窗口一旦被最小化或
真正隐藏(SW_HIDE), 平台就可能暂停视频; 本项目的停滞判据是"视频和平台进度
连续 120 秒未推进就停止当前课时", 于是表现为"最小化 / 隐藏后刷课停止"。

做法
----
不隐藏、不最小化, 而是用 SetWindowPos 把整个窗口缩到最小尺寸, 再整体移到
屏幕左下角之外(继续沿左侧出去, 垂直方向贴住屏幕底部)。窗口在系统里仍是
普通状态(IsWindowVisible=True, IsIconic=False), Chromium 不会把它当成后台
窗口, 平台也收不到"窗口不见了"的信号。

实测(Edge + Playwright, 见开发记录): 窗口缩到 515x100 后
document.visibilityState 仍为 visible, hasFocus() 为 true,
video.currentTime 正常推进(3.5s / 3.5s), 点击也正常。
前提是程序用 new_context(viewport=None), 页面视口固定 1280x720,
不随窗口尺寸变化, 所以缩小窗口不会影响页面布局与元素定位。

再叠一层看门狗: 用户手动最小化 / 隐藏窗口时, 自动恢复成"左下角屏幕外窗口",
保证长时间运行不被打断。
"""

import asyncio
import ctypes
import sys

if sys.platform == "win32":
    import ctypes.wintypes as wt

SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
SW_MAXIMIZE = 3
SW_RESTORE = 9
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010

# 移到屏幕外时, 再往外多留一点, 避免屏幕边框/缩放把窗口边露出来
OFFSCREEN_MARGIN = 80

# 隐藏时把窗口缩到最小, 降低 GPU 合成与任务栏预览的开销。
# 注意: 程序用 new_context(viewport=None), 页面视口固定 1280x720,
# 不随窗口尺寸变化, 所以缩小窗口不会影响页面布局、元素定位和播放。
MIN_WIDTH = 480
MIN_HEIGHT = 100

# 系统工作区(排除任务栏), 保证窗口不会被任务栏盖住
SPI_GETWORKAREA = 0x0030

_state = {
    "hwnd": None,
    "origin": None,  # 隐藏前的 (x, y, w, h)
    "mode": "onscreen",  # onscreen / offscreen
    "miss": 0,  # 连续找不到窗口的次数, 用于自动放弃
    "maximized": False,  # 隐藏前是否处于最大化, 恢复时还原
}

_last_notice = {"at": 0.0, "kind": None}


def available() -> bool:
    return sys.platform == "win32"


def _user32():
    return ctypes.windll.user32


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", wt.LONG),
        ("top", wt.LONG),
        ("right", wt.LONG),
        ("bottom", wt.LONG),
    ]


def _rect(hwnd):
    rect = _RECT()
    if not _user32().GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)


def _is_alive(hwnd) -> bool:
    return bool(hwnd) and bool(_user32().IsWindow(hwnd))


def watch(hwnd) -> None:
    """记住要托管的窗口句柄(第一次调用时记录它原来的位置)。"""
    if not available() or not _is_alive(hwnd):
        return
    _state["hwnd"] = hwnd
    _state["miss"] = 0
    if _state["origin"] is None:
        rect = _rect(hwnd)
        if rect:
            _state["origin"] = rect
            _state["maximized"] = bool(_user32().IsZoomed(hwnd))


def monitored_hwnd():
    hwnd = _state["hwnd"]
    return hwnd if _is_alive(hwnd) else None


def is_offscreen(hwnd) -> bool:
    """窗口当前是否已经停在左下角的屏幕外隐藏位。"""
    rect = _rect(hwnd) if _is_alive(hwnd) else None
    if not rect:
        return False
    _, _, _, bottom = _work_area()
    outside = rect[0] + rect[2] <= 0 or rect[0] >= _screen_width()
    return outside and abs((rect[1] + rect[3]) - bottom) <= 60


def _screen_width() -> int:
    try:
        return _user32().GetSystemMetrics(0)
    except Exception:
        return 1920


def _screen_height() -> int:
    try:
        return _user32().GetSystemMetrics(1)
    except Exception:
        return 1080


def _work_area():
    """返回工作区 (left, top, right, bottom); 取不到就退回全屏。"""
    rect = _RECT()
    try:
        if _user32().SystemParametersInfoW(
            SPI_GETWORKAREA, 0, ctypes.byref(rect), 0
        ):
            return (rect.left, rect.top, rect.right, rect.bottom)
    except Exception:
        pass
    return (0, 0, _screen_width(), _screen_height())


def work_area():
    """公开的工作区查询: (left, top, right, bottom), 不含任务栏。

    非 Windows 平台退回整屏, 调用方不用再判断平台。
    单位与 Tk 的 winfo_screenheight/geometry 一致(都是进程视角的物理像素)。
    """
    if not available():
        return (0, 0, _screen_width(), _screen_height())
    return _work_area()


def _move(hwnd, x, y, width, height) -> bool:
    ok = _user32().SetWindowPos(
        hwnd, 0, int(x), int(y), int(width), int(height),
        SWP_NOZORDER | SWP_NOACTIVATE,
    )
    return bool(ok)


def _restore(hwnd) -> None:
    """最大化/最小化的窗口必须先还原, 否则 SetWindowPos 不生效。"""
    if _user32().IsIconic(hwnd) or _user32().IsZoomed(hwnd):
        _user32().ShowWindow(hwnd, SW_RESTORE)


def move_offscreen(hwnd, shrink: bool = True) -> bool:
    """把窗口缩到最小, 移到屏幕左下角之外, 保持普通窗口状态。"""
    if not available() or not _is_alive(hwnd):
        return False
    watch(hwnd)
    _restore(hwnd)
    rect = _rect(hwnd)
    if not rect:
        return False
    width, height = rect[2], rect[3]
    if shrink:
        width, height = MIN_WIDTH, MIN_HEIGHT
    _, _, _, bottom = _work_area()
    # 左侧之外 + 贴住工作区底部: 整块窗口都不可见, 但垂直方向在左下角
    return _move(
        hwnd,
        -(width + OFFSCREEN_MARGIN),
        bottom - height,
        width,
        height,
    )


def move_onscreen(hwnd) -> bool:
    """移回屏幕内, 并保证可见, 但不抢焦点。"""
    if not available() or not _is_alive(hwnd):
        return False
    watch(hwnd)
    _restore(hwnd)
    rect = _rect(hwnd)
    origin = _state["origin"]
    x, y = 100, 100
    width, height = (rect[2], rect[3]) if rect else (1280, 800)
    if origin:
        # 原始位置可能在屏幕外, 拉回到主屏可见范围
        x = max(0, min(origin[0], max(0, _screen_width() - 400)))
        y = max(0, min(origin[1], 200))
        # 恢复隐藏前缩小掉的窗口尺寸(rect 此时仍是最小尺寸)
        width, height = origin[2], origin[3]
    ok = _move(hwnd, x, y, width, height)
    _user32().ShowWindow(hwnd, SW_SHOWNOACTIVATE)
    if _state.get("maximized"):
        # 隐藏时为提高显存利用率取消过最大化, 显示时还原
        _user32().ShowWindow(hwnd, SW_MAXIMIZE)
    return ok


def set_mode(mode: str) -> None:
    if mode in {"onscreen", "offscreen"}:
        _state["mode"] = mode


def mode() -> str:
    return _state["mode"]


def ensure_alive():
    """检查窗口是否被最小化/隐藏, 是则恢复。

    返回 "restored" / "lost" / None(无需处理)。
    """
    if not available():
        return None
    hwnd = _state["hwnd"]
    if not hwnd:
        return None
    if not _is_alive(hwnd):
        _state["miss"] += 1
        if _state["miss"] >= 3:
            _state["hwnd"] = None
            _state["origin"] = None
            return "lost"
        return None
    _state["miss"] = 0

    user32 = _user32()
    minimized = bool(user32.IsIconic(hwnd))
    hidden = not bool(user32.IsWindowVisible(hwnd))
    if not minimized and not hidden:
        return None

    # 用户手动最小化 / 程序隐藏, 统一转成"左下角屏幕外普通窗口":
    # 直接弹回原来的位置会打断用户, 而留在最小化状态又会被平台判成离开页面。
    move_offscreen(hwnd)
    return "restored"


async def watchdog(logger, enabled, interval: float = 3.0) -> None:
    """后台巡检: 窗口被最小化/隐藏时立刻救回来。

    停止运行时由调用方取消该任务。
    """
    if not available():
        return
    while True:
        try:
            await asyncio.sleep(interval)
            if not enabled() or monitored_hwnd() is None:
                continue
            result = ensure_alive()
            if result == "restored":
                logger.info(
                    "检测到播放窗口被最小化/隐藏, 已移回屏幕左下角外继续运行.", shift=True
                )
                logger.event("窗口保活", 动作="恢复", 模式=_state["mode"])
            elif result == "lost":
                logger.debug("播放窗口已关闭, 窗口保活停止.")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug(f"窗口保活巡检失败: {exc}")
