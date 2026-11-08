# encoding=utf-8
import random
import sys
from playwright.async_api import Page, TimeoutError
from modules.lesson_navigation import (
    CatalogSelectors,
    lesson_progress,
    parse_progress_value,
)
from modules.logger import Logger

logger = Logger()

# 进度输出节流记录: key -> 上一次已输出的百分比/步长
_LAST_PCT = {}


def _emit(text):
    """安全写标准输出。

    打包为窗口模式(-w)时 sys.stdout 为 None, 直接 print() 会抛 AttributeError,
    这里统一吞掉, 保证播放/下载流程不被"没有控制台"拖垮。
    """
    stream = sys.stdout
    if stream is None:
        return
    try:
        stream.write(text)
        stream.flush()
    except (AttributeError, OSError, ValueError):
        pass


def _should_emit(key, value, done=False):
    """同一进度只在百分比(或无总大小时的 MB 步长)变化时输出, 避免刷屏。"""
    if done:
        _LAST_PCT[key] = value
        return True
    if _LAST_PCT.get(key) == value:
        return False
    _LAST_PCT[key] = value
    return True


# 视频区域内移动鼠标
async def move_mouse(page: Page):
    try:
        await page.wait_for_selector(".videoArea", state="attached", timeout=5000)
        elem = page.locator(".videoArea")
        await elem.hover(timeout=4000)
        pos = await elem.bounding_box()
        if not pos:
            return
        # Calculate the target position to move the mouse
        target_x = pos['x'] + random.uniform(-10, 10)
        target_y = pos['y'] + random.uniform(-10, 10)
        await page.mouse.move(target_x, target_y)
    except TimeoutError:
        return


# 获取课程进度
async def get_course_progress(page: Page, catalog: CatalogSelectors) -> str:
    await move_mouse(page)
    current_lesson = page.locator(catalog.active).first
    if await current_lesson.count() == 0:
        return "0%"
    return f"{await lesson_progress(current_lesson, catalog)}%"


# 打印课程播放进度
def show_course_progress(desc, cur_time=None, limit_time=0):
    assert limit_time >= 0, "limit_time 必须为非负数!"
    if limit_time == 0:
        cur_time = "0%" if cur_time == '' or cur_time is None else cur_time
        percent = parse_progress_value(str(cur_time).rstrip("%"))
        if not _should_emit(desc, percent):
            return
        length = int(percent * 30 // 100)
        progress = ("█" * length).ljust(30, " ")
        _emit(f"\r{desc} |{progress}| {percent}%\t".ljust(50))
    else:
        cur_time = 0 if cur_time == '' or cur_time is None else cur_time
        if isinstance(cur_time, str):
            cur_time = 0
        left_time = round(limit_time - cur_time, 1)
        percent = int(cur_time / limit_time * 100)
        if left_time <= 0:
            percent = 100
        percent = max(0, min(percent, 100))
        if not _should_emit(desc, percent):
            return
        length = int(percent * 20 // 100)
        progress = ("█" * length).ljust(20, " ")
        _emit(f"\r{desc} |{progress}| {percent}%\t剩余 {left_time} min\t".ljust(50))


# 打印通用版进度条
def show_progress(desc, current, total, suffix="", width=30):
    # 下载时按 512 字节回调一次, 必须节流, 否则控制台会被刷爆
    if total <= 0:
        step = int(current // (1024 * 1024))
        if not _should_emit(desc, step):
            return
        _emit(f"\r{desc} 已下载 {current} bytes\t{suffix}".ljust(50))
        return
    percent = min(int(current / total * 100), 100)
    if not _should_emit(desc, percent, done=percent >= 100):
        return
    length = int(percent * width // 100)
    progress = ("█" * length).ljust(width, " ")
    _emit(f"\r{desc} |{progress}| {percent}%\t{suffix}".ljust(50))
