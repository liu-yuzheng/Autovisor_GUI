# encoding=utf-8
"""运行状态总线(StatusBus): 刷课核心写, 任意界面读。

设计目标
--------
界面(GUI.py 的主窗口与悬浮小窗)只依赖本模块的稳定接口, 不需要改动刷课逻辑:

    from modules.status import status_bus, format_brief

    snap = status_bus.snapshot()       # dict, 纯 JSON 可序列化
    status_bus.subscribe(fn)           # fn(snapshot), 任意线程回调
    text = format_brief(snap)          # 一句话简要说明(主窗/小窗共用)
    status_bus.set_mirror_file(path)   # 可选: 每次变化写一份 JSON 镜像

snapshot 字段
-------------
    state          idle|preparing|running|stopping|finished|error
    active         bool, 是否正在刷课(界面据此闪烁进度条)
    percent        float 0~100, 总进度
    course_index   当前第几门课程(从 1 开始, 空闲时为 0)
    course_total   课程总数
    course_title   当前课程名称
    lesson_index   当前第几个课时
    lesson_total   本轮课程课时总数
    lesson_title   当前课时名称
    lesson_percent 当前课时平台记录进度(0~100)
    message        最近一条简要日志(含出错原因 / 安装依赖 / 登录等)
    message_level  INFO|WARN|ERROR
    error          最近一次错误原文(无则空串)
    started_at     本次运行开始的时间戳(秒)
    updated_at     最后一次状态更新时间戳(秒)
    elapsed_sec    本次运行已过去的秒数
"""

import json
import os
import re
import threading
import time

STATE_IDLE = "idle"
STATE_PREPARING = "preparing"
STATE_RUNNING = "running"
STATE_STOPPING = "stopping"
STATE_FINISHED = "finished"
STATE_ERROR = "error"

# 正在运行(界面需要闪烁)的状态集合
ACTIVE_STATES = (STATE_PREPARING, STATE_RUNNING)

# Logger 的正式日志行: [时间] [级别] [调用位置] 内容
_RECORD_RE = re.compile(
    r"^\[\d{2}:\d{2}:\d{2}\.\d{3}\]\s+\[([A-Z]+)\]\s+\[[^\]]*\]\s?(.*)$",
    re.S,
)

# 段落标题一类噪音不作为"简要说明"
_IGNORED_PREFIXES = ("=====",)

# 简要说明只吸收这几类日志。
# EVENT 是给日志文件看的机器事件(课程目录、播放状态流水), 只写文件和在主界面
# 日志面板里显示, 不挤进小窗那一行; 其余级别(含 WARN/DEBUG)都算有效信息。
_BRIEF_LEVELS = ("INFO", "DEBUG", "WARN", "ERROR")


def _clean_record(text):
    """从完整日志行里抽出正文; 非正式行(明细/堆栈)返回空串。"""
    match = _RECORD_RE.match(text or "")
    if not match:
        return ""
    message = (match.group(2) or "").strip()
    if not message or message.startswith(_IGNORED_PREFIXES):
        return ""
    # 单行化, 避免多行内容撑坏界面
    return " ".join(message.split())


class StatusBus:
    """线程安全的状态快照中心(单例)。"""

    _instance = None
    _instance_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._setup()
            return cls._instance

    def _setup(self):
        self._lock = threading.RLock()
        self._listeners = []
        self._logger_attached = False
        self._mirror_path = None
        self._last_mirror = None
        self._reset_fields()

    def _reset_fields(self):
        self._state = STATE_IDLE
        self._message = ""
        self._message_level = "INFO"
        self._error = ""
        self._course_index = 0
        self._course_total = 0
        self._course_title = ""
        self._lesson_index = 0
        self._lesson_total = 0
        self._lesson_title = ""
        self._lesson_percent = 0.0
        self._percent_override = None
        self._started_at = 0.0
        self._updated_at = time.time()

    # ---------- 写 ----------
    def reset(self):
        """清空进度, 回到空闲态(不动订阅者与镜像设置)。"""
        with self._lock:
            self._reset_fields()
            self._notify()

    def begin(self, course_total=0, message="正在启动..."):
        """新一轮运行开始: 清零进度, 进入准备态。"""
        with self._lock:
            self._reset_fields()
            self._state = STATE_PREPARING
            self._course_total = max(0, int(course_total or 0))
            self._message = message
            self._started_at = time.time()
            self._notify()

    def set_state(self, state, message=None):
        with self._lock:
            self._state = state
            if message is not None:
                self._message = message
                self._message_level = "ERROR" if state == STATE_ERROR else "INFO"
                if state == STATE_ERROR:
                    self._error = message
            self._notify()

    def set_message(self, message, level="INFO"):
        """更新简要说明(来自日志或阶段提示)。"""
        with self._lock:
            self._message = message
            self._message_level = level
            if level == "ERROR":
                self._error = message
            self._notify()

    def set_course(self, index=None, total=None, title=None):
        with self._lock:
            if index is not None:
                self._course_index = int(index)
            if total is not None:
                self._course_total = max(0, int(total))
            if title is not None:
                self._course_title = title
            self._notify()

    def set_lesson_scope(self, total):
        """新一轮课程的课时总数(进入课程目录后才知道)。"""
        with self._lock:
            self._lesson_total = max(0, int(total or 0))
            self._lesson_index = 0
            self._lesson_title = ""
            self._lesson_percent = 0.0
            self._notify()

    def set_lesson(self, index, total=None, title=None):
        with self._lock:
            self._lesson_index = int(index)
            if total is not None:
                self._lesson_total = max(0, int(total))
            if title is not None:
                self._lesson_title = title
            self._lesson_percent = 0.0
            self._notify()

    def set_lesson_percent(self, percent):
        """当前课时的平台记录进度; 变化不明显时跳过, 避免无谓重绘。"""
        try:
            value = max(0.0, min(100.0, float(percent)))
        except (TypeError, ValueError):
            return
        with self._lock:
            if abs(value - self._lesson_percent) < 0.5:
                return
            self._lesson_percent = value
            self._notify()

    def set_percent(self, percent):
        """直接指定总进度(用于"全部完成=100%"这类收尾)。"""
        with self._lock:
            try:
                self._percent_override = max(0.0, min(100.0, float(percent)))
            except (TypeError, ValueError):
                self._percent_override = None
            self._notify()

    # ---------- 读 ----------
    def _compute_percent(self):
        if self._percent_override is not None:
            return round(self._percent_override, 1)
        if self._course_total <= 0:
            return 0.0
        finished_courses = max(0, self._course_index - 1)
        in_course = 0.0
        if self._lesson_total > 0 and self._lesson_index > 0:
            position = (self._lesson_index - 1) + self._lesson_percent / 100.0
            in_course = min(1.0, max(0.0, position / self._lesson_total))
        total = (finished_courses + in_course) / self._course_total * 100.0
        return round(min(100.0, max(0.0, total)), 1)

    def snapshot(self):
        """返回当前状态快照(纯 JSON 可序列化, 可跨线程/跨进程使用)。"""
        with self._lock:
            now = time.time()
            elapsed = max(0.0, now - self._started_at) if self._started_at else 0.0
            return {
                "state": self._state,
                "active": self._state in ACTIVE_STATES,
                "percent": self._compute_percent(),
                "course_index": self._course_index,
                "course_total": self._course_total,
                "course_title": self._course_title,
                "lesson_index": self._lesson_index,
                "lesson_total": self._lesson_total,
                "lesson_title": self._lesson_title,
                "lesson_percent": round(self._lesson_percent, 1),
                "message": self._message,
                "message_level": self._message_level,
                "error": self._error,
                "started_at": self._started_at,
                "updated_at": self._updated_at,
                "elapsed_sec": round(elapsed, 1),
            }

    # ---------- 订阅 ----------
    def subscribe(self, fn):
        """注册状态回调 fn(snapshot); 任意线程调用均安全。"""
        with self._lock:
            if fn not in self._listeners:
                self._listeners.append(fn)

    def unsubscribe(self, fn):
        with self._lock:
            if fn in self._listeners:
                self._listeners.remove(fn)

    def _notify(self):
        self._updated_at = time.time()
        listeners = list(self._listeners)
        snapshot = self.snapshot() if listeners or self._mirror_path else None
        for fn in listeners:
            try:
                fn(snapshot)
            except Exception:
                # 订阅者异常不影响刷课主流程
                pass
        if self._mirror_path and snapshot is not None:
            self._write_mirror(snapshot)

    # ---------- 本地镜像(给独立进程的悬浮窗预留) ----------
    def set_mirror_file(self, path):
        """把快照同步写到本地 JSON 文件; 传 None 关闭。"""
        with self._lock:
            self._mirror_path = path

    def _write_mirror(self, snapshot):
        path = self._mirror_path
        if not path:
            return
        payload = dict(snapshot)
        payload.pop("updated_at", None)
        payload.pop("elapsed_sec", None)
        if payload == self._last_mirror:
            return
        self._last_mirror = payload
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, ensure_ascii=False)
            os.replace(tmp, path)
        except Exception:
            pass

    # ---------- 与 Logger 对接 ----------
    def attach_logger(self, logger):
        """把简要说明自动挂到日志流上(失败原因/安装依赖等信息自动可见)。"""
        with self._lock:
            if self._logger_attached:
                return
            self._logger_attached = True
        try:
            logger.add_sink(self._on_log)
        except Exception:
            pass

    def _on_log(self, level, text):
        if level not in _BRIEF_LEVELS:
            # EVENT 这类流水不更新简要说明, 但说明"程序还活着":
            # 刷新时间戳, 避免界面的看门狗把正常播放误判成卡死。
            with self._lock:
                self._updated_at = time.time()
            return
        message = _clean_record(text)
        if not message:
            return
        self.set_message(message, level="ERROR" if level == "ERROR" else level)


# 进程内单例
status_bus = StatusBus()


# 界面文案统一在根目录的 ui_text.py 里改, 这里只是取默认值;
# 万一单独拷走 modules 目录(没有 ui_text.py), 退回下面这份内置文案, 不影响刷课核心。
try:
    from ui_text import (
        BRIEF_COURSE,
        BRIEF_ERROR,
        BRIEF_LESSON,
        BRIEF_PART_SEPARATOR,
        BRIEF_SEPARATOR,
        BRIEF_UNKNOWN,
        STATE_BRIEF as _STATE_BRIEF,
    )
except Exception:  # pragma: no cover - 缺文件时的兜底
    _STATE_BRIEF = {
        STATE_IDLE: "空闲，等待开始",
        STATE_PREPARING: "正在准备（检查依赖 / 登录 / 打开课程）",
        STATE_RUNNING: "正在刷课",
        STATE_STOPPING: "正在停止…",
        STATE_FINISHED: "已完成",
        STATE_ERROR: "运行出错",
    }
    BRIEF_COURSE = "课程 {index}/{total}"
    BRIEF_LESSON = "课时 {index}/{total}"
    BRIEF_PART_SEPARATOR = " · "
    BRIEF_SEPARATOR = "｜"
    BRIEF_ERROR = "出错：{reason}"
    BRIEF_UNKNOWN = "未知错误"


def _fill(template, **values):
    """填充占位符; 文案写错时原样返回, 不让界面崩掉。"""
    try:
        return template.format(**values)
    except (KeyError, IndexError, ValueError):
        return template


def format_brief(snapshot):
    """把快照压缩成一句话, 供主界面与悬浮小窗共用。"""
    if not snapshot:
        return ""
    state = snapshot.get("state") or STATE_IDLE
    message = snapshot.get("message") or ""

    if state == STATE_RUNNING:
        parts = []
        if snapshot.get("course_total"):
            parts.append(
                _fill(
                    BRIEF_COURSE,
                    index=snapshot.get("course_index") or 0,
                    total=snapshot["course_total"],
                )
            )
        if snapshot.get("lesson_total"):
            parts.append(
                _fill(
                    BRIEF_LESSON,
                    index=snapshot.get("lesson_index") or 0,
                    total=snapshot["lesson_total"],
                )
            )
        # 百分比已在界面上单独显示, 这里不重复
        head = BRIEF_PART_SEPARATOR.join(parts)
        if head and message:
            return head + BRIEF_SEPARATOR + message
        return head or message or _STATE_BRIEF[STATE_RUNNING]

    if state == STATE_ERROR:
        reason = snapshot.get("error") or message or BRIEF_UNKNOWN
        return _fill(BRIEF_ERROR, reason=reason)

    if state == STATE_FINISHED:
        return message or _STATE_BRIEF[STATE_FINISHED]

    if state == STATE_STOPPING:
        return _STATE_BRIEF[STATE_STOPPING]

    if state == STATE_PREPARING:
        return message or _STATE_BRIEF[STATE_PREPARING]

    return message or _STATE_BRIEF[STATE_IDLE]
