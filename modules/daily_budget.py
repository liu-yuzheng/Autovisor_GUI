"""每日刷课时长累计: 用于"今天学够了就不再开机自启动"。

为什么按"时长"而不是"启动过几次":
用户真正想限制的是学习量。开机后闪退、依赖没装完、网络断一次, 都算"启动过",
但实际一分钟没学 —— 按累计时长判断才符合直觉, 也才不会因为一次失败就浪费掉
当天的机会。

计数口径: 只累加"正在刷课"的墙上时间(含失败重试的等待), 空闲、停在配置页、
程序没开都不算。

落盘节流: 由调用方每累计满 30 秒调一次 `add()`, 关窗口时再补一次,
所以断电最多丢 30 秒, 不会把一整天的量丢掉。

状态文件放在 %LOCALAPPDATA%\\AutovisorGUI\\daily_state.json, 不放程序目录:
程序可能装在只读位置, 移动/重装目录也不该把"今天已经学够了"这件事忘掉。
"""

import json
import os
import tempfile
from datetime import datetime

STATE_FILE = "daily_state.json"


def state_dir():
    """状态目录: 与单实例的激活信号同一个目录, 避免到处散落小文件。"""
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    path = os.path.join(base, "AutovisorGUI")
    try:
        os.makedirs(path, exist_ok=True)
        return path
    except OSError:
        return tempfile.gettempdir()


def state_path():
    return os.path.join(state_dir(), STATE_FILE)


def _today(now=None):
    return (now or datetime.now()).strftime("%Y-%m-%d")


def seconds_today(now=None):
    """今天累计刷了多少秒; 跨天自动归零, 文件坏掉也当 0。"""
    try:
        with open(state_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return 0
    if not isinstance(data, dict) or data.get("date") != _today(now):
        return 0
    try:
        return max(0, int(data.get("seconds") or 0))
    except (TypeError, ValueError):
        return 0


def add(seconds, now=None):
    """累加今天的学习时长(秒), 返回累加后的总数。"""
    now = now or datetime.now()
    try:
        delta = max(0, int(seconds))
    except (TypeError, ValueError):
        delta = 0
    total = seconds_today(now) + delta
    try:
        with open(state_path(), "w", encoding="utf-8") as handle:
            json.dump({"date": _today(now), "seconds": total}, handle)
    except OSError:
        pass
    return total


def limit_seconds(minutes):
    """把界面上的"分钟"转成秒; 空/非法/0 都表示不限制。"""
    try:
        value = int(str(minutes).strip())
    except (TypeError, ValueError):
        return 0
    return max(0, value) * 60


def reached(minutes, now=None):
    """今天是否已经刷够(上限 <= 0 表示不限制, 永远返回 False)。"""
    limit = limit_seconds(minutes)
    if limit <= 0:
        return False
    return seconds_today(now) >= limit


def reset():
    """清零今天的记录(界面上"清零今日时长"用它)。"""
    try:
        os.remove(state_path())
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def format_duration(seconds):
    """把秒数显示成 "42 分钟" / "1 小时 05 分"; 不足一分钟单独说清楚。"""
    total = max(0, int(seconds))
    if total <= 0:
        return "0 分钟"
    minutes = total // 60
    if minutes == 0:
        return "不足 1 分钟"
    if minutes < 60:
        return f"{minutes} 分钟"
    return f"{minutes // 60} 小时 {minutes % 60:02d} 分"
