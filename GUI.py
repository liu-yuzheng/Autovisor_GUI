# encoding=utf-8
"""Autovisor 图形界面。

界面只做三件事:
1. 编辑并回写 config.ini;
2. 在工作线程里跑 Autovisor.supervise(config)(含失败自动重启);
3. 从 modules.status 读状态快照渲染进度, 从 Logger 的 sink 读日志。

刷课逻辑本身在 Autovisor.py / modules 里, 这里不改它。
"""

import importlib
import importlib.util
import os
import queue
import subprocess
import sys
import threading
import time
import configparser
import tkinter as tk
import tkinter.ttk as ttk
import tkinter.scrolledtext as scrolledtext
from tkinter import messagebox

# 主题库可能缺失(全新的 venv 很常见)。缺了也要能把窗口开出来,
# 否则用户连"缺少依赖"的提示都看不到。
try:
    import sv_ttk

    _HAS_SV_TTK = True
except ImportError:
    sv_ttk = None
    _HAS_SV_TTK = False

def get_base_dir():
    """返回程序基准目录: 打包后为 exe 所在目录, 否则为源码脚本目录。

    打包(冻结)后 GUI.py 的 __file__ 指向包内 internal 目录, 不能用它来找
    config.ini / data/mirrors.json; 必须用 sys.executable 所在目录。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _report_text_file_error(path, exc):
    """文案文件写坏时给出看得懂的提示。

    这时 ui_text 还没加载成功, 提示语只能写死在这里 —— 全程序仅此一处。
    """
    try:
        log_dir = os.path.join(get_base_dir(), "logs")
        os.makedirs(log_dir, exist_ok=True)
        with open(
            os.path.join(log_dir, "ui_text_error.log"), "a", encoding="utf-8"
        ) as handle:
            handle.write(
                f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n"
                f"{path}\n{type(exc).__name__}: {exc}\n"
            )
    except Exception:
        pass
    # 开机自启那次是无人值守的, 不弹窗
    if sys.platform != "win32" or "--boot" in sys.argv[1:]:
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None,
            "界面文案文件写错了, 程序无法启动:\n"
            f"{path}\n\n{type(exc).__name__}: {exc}\n\n"
            "请修正该文件 (或删掉它恢复默认文案) 后重新打开。",
            "文案错误",
            0x10,
        )
    except Exception:
        pass


def _load_ui_text():
    """加载界面文案: 优先用程序目录下可编辑的 ui_text.py。

    这样打包成 exe 之后, 改文案只要改 exe 同目录的 ui_text.py 再重启,
    不必重新打包。打包进 internal 的那份作为兜底(文件被删也能启动)。
    """
    path = os.path.join(get_base_dir(), "ui_text.py")
    if os.path.isfile(path):
        try:
            spec = importlib.util.spec_from_file_location("ui_text", path)
            module = importlib.util.module_from_spec(spec)
            sys.modules["ui_text"] = module
            spec.loader.exec_module(module)
            return module
        except Exception as exc:
            sys.modules.pop("ui_text", None)
            _report_text_file_error(path, exc)

    try:
        import ui_text as bundled

        return bundled
    except Exception:
        # 源码运行时上面读的就是同一个文件, 这里必然再失败一次:
        # 提示已经弹过了, 直接干净退出, 不要甩一堆堆栈。
        raise SystemExit(1)


# 界面上显示的所有文字都在 ui_text.py 里, 改文案不用碰这个文件
TXT = _load_ui_text()

from modules import autostart, daily_budget, no_console, single_instance, window_guard
from modules.status import (
    STATE_ERROR,
    STATE_FINISHED,
    STATE_IDLE,
    STATE_PREPARING,
    STATE_RUNNING,
    STATE_STOPPING,
    format_brief,
    status_bus,
)
from modules.version import __version__ as _VERSION


# 窗口模式(pythonw.exe 启动 / 打包成 -w)下 sys.stdout 与 sys.stderr 都是 None,
# 任何一处裸露的 print() 都会抛 AttributeError。开机自启动正是用 pythonw 跑的,
# 所以这里不能只在"打包后"才处理: 只要输出流不可用, 就统一重定向到 logs/stdout.txt,
# 既不弹控制台, 也不丢失 CLI 分支与第三方库的输出。
if sys.stdout is None or sys.stderr is None:
    try:
        _log_dir = os.path.join(get_base_dir(), "logs")
        os.makedirs(_log_dir, exist_ok=True)
        _stdout_file = open(
            os.path.join(_log_dir, "stdout.txt"), "a", encoding="utf-8", errors="replace"
        )
        if sys.stdout is None:
            sys.stdout = _stdout_file
        if sys.stderr is None:
            sys.stderr = _stdout_file
    except Exception:
        pass

# 主程序本身由 -w 打包成窗口程序, 但 playwright 的 node.exe 驱动、pip 等
# 仍是控制台程序: GUI 没有控制台可继承, Windows 会给它们各开一个黑窗。
# 在拉起任何子进程之前统一补上 CREATE_NO_WINDOW。
no_console.install()

# 开机自启动时注册表命令带 --boot(这是开机拉起的那次) 与 --mini(只显示小窗)。
# 必须在下面的 CLI 分支之前摘掉, 否则会被当成命令行参数, 整个界面走 CLI 退出。
_BOOT_FLAGS = ("--boot", "--mini")
_BOOT_ON_START = "--boot" in sys.argv[1:]
_MINI_ON_START = "--mini" in sys.argv[1:]
if _BOOT_ON_START or _MINI_ON_START:
    sys.argv = [arg for arg in sys.argv if arg not in _BOOT_FLAGS]

# 带命令行参数运行时(如 --check-browser / --import-cookies), 直接走 CLI 逻辑,
# 保持打包后 exe 既可以作为图形界面, 也能当作命令行工具使用。
if len(sys.argv) > 1:
    import Autovisor  # 静态导入: 便于 PyInstaller 收集依赖

    sys.exit(Autovisor.cli())

# === 单实例 ===
# 开机自启(--boot) 与双击正常启动走的是同一个入口, 统一在这里互斥。
# 已经有实例在跑时, 后来者只负责"叫醒"它然后退出: 避免同一账号同时跑两份刷课,
# 也不会因为开机那次没关掉、白天又双击一次而出现两个浏览器抢播放进度。
# 开机那次(--boot)被拒时保持安静: 那是系统重复触发, 没必要把窗口抢到前台。
if not single_instance.acquire():
    if not _BOOT_ON_START:
        single_instance.notify_running()
    sys.exit(0)

# === 配置文件初始化 ===
# interpolation=None: 账号密码里出现 % 时不会被当成插值语法,
# 否则读取阶段会抛 InterpolationSyntaxError, 表现为"无法加载配置"。
config = configparser.ConfigParser(interpolation=None)
config_file = os.path.join(get_base_dir(), "config.ini")

# 各段必须有默认值: 首次运行(没有 config.ini)时, 若只建空段,
# 保存后 Autovisor.Config 读取必需项会抛 NoOptionError, 表现为"无法加载配置"。
_DEFAULT_OPTIONS = {
    "user-account": {"username": "", "password": ""},
    "browser-option": {"driver": "Edge", "EXE_PATH": ""},
    "script-option": {
        "enableAutoCaptcha": "True",
        "enableHideWindow": "False",
        "showDonateCode": "False",
        "autoInstallDeps": "True",
        "startupMiniWindow": "True",
        "startupAutoRun": "True",
        "startupDailyLimitMinutes": "60",
    },
    "course-option": {
        "limitMaxTime": "30",
        "limitSpeed": "1.0",
        "soundOff": "True",
        "skipCompletedCourse": "True",
    },
    "restart": {"enableRestart": "True", "backoffSchedule": "10,10,10,20,20,30"},
    "course-url": {},
}


def _load_config_from(path):
    """按 utf-8-sig → utf-8 → gbk 依次尝试读取配置文件。

    记事本/旧版本可能把 config.ini 存成 GBK, 直接按 utf-8 读会抛
    UnicodeDecodeError, 并且发生在模块导入阶段 —— 窗口都打不开。
    """
    if not os.path.isfile(path):
        return False
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        parser = configparser.ConfigParser(interpolation=None)
        try:
            with open(path, "r", encoding=encoding) as f:
                parser.read_file(f)
        except UnicodeDecodeError:
            continue
        except (OSError, configparser.Error):
            return False
        for section in parser.sections():
            if not config.has_section(section):
                config.add_section(section)
            for option, value in parser.items(section):
                config.set(section, option, value)
        return True
    return False


def ensure_config_file():
    """保证 config.ini 存在且段/必需键齐全, 返回 (是否新建, 补齐的键列表)。

    首次运行没有 config.ini 时优先从同目录的 config.ini.example 复制一份,
    这样 [restart] 等可选项和注释都能一并带过来; 没有 example 则内置生成。
    """
    created = False
    copied = False
    if not os.path.isfile(config_file):
        example = os.path.join(get_base_dir(), "config.ini.example")
        created = True
        if os.path.isfile(example):
            try:
                with open(example, "r", encoding="utf-8-sig") as src:
                    content = src.read()
                with open(config_file, "w", encoding="utf-8") as dst:
                    dst.write(content)
                copied = True
            except (OSError, UnicodeDecodeError):
                created = False

    # 无论新建还是旧文件, 都补齐缺失的段和必需键(不改动已有值)
    _load_config_from(config_file)
    added = []
    for section, options in _DEFAULT_OPTIONS.items():
        if not config.has_section(section):
            config.add_section(section)
            added.append(f"[{section}]")
        for option, value in options.items():
            if not config.has_option(section, option):
                config.set(section, option, value)
                added.append(f"{section}.{option}")
    # 已经整份复制过且无需补键时不再回写, 保留 example 里的注释
    if added or (created and not copied):
        try:
            with open(config_file, "w", encoding="utf-8") as f:
                config.write(f)
        except OSError:
            pass
    return created, added


_config_created, _config_added = ensure_config_file()


def _write_boot_log(text):
    """开机自启动被跳过时留一条记录: 此时还没有界面, 只能写文件。"""
    try:
        log_dir = os.path.join(get_base_dir(), "logs")
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, "boot.log"), "a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {text}\n")
    except Exception:
        pass


# === 每日学习时长上限 ===
# 只在开机自启(--boot) 时生效: 手动双击是用户的明确意图, 不参与这个限制。
# 今天已经刷够(默认 60 分钟, 0 = 不限制)就直接退出 —— 不弹窗、不抢焦点、不开浏览器,
# 只留一行记录, 因为开机那一刻多弹一个窗口比什么都不做更打扰。
if _BOOT_ON_START:
    _daily_limit = config.get(
        "script-option", "startupDailyLimitMinutes", raw=True, fallback="60"
    ).strip()
    if daily_budget.reached(_daily_limit):
        _write_boot_log(
            TXT.fill(
                TXT.LOG_BOOT_SKIPPED,
                done=daily_budget.format_duration(daily_budget.seconds_today()),
                limit=_daily_limit,
            )
        )
        sys.exit(0)


# === 运行时状态 ===
running = False
worker_thread = None
log_queue = queue.Queue()
_closing = False
_stall = {"since": time.time(), "warned": False}

_FONT = "Microsoft YaHei" if sys.platform == "win32" else "PingFang SC"
_PAD = 14

# 依赖自检清单: (pip 名, import 名, 是否必需)
_DEPENDENCIES = [
    ("playwright", "playwright", True),
    ("requests", "requests", True),
    ("pillow", "PIL", True),
    ("sv-ttk", "sv_ttk", False),
]
if os.name == "nt":
    _DEPENDENCIES.append(("pygetwindow", "pygetwindow", False))

# 安装时用的版本约束, 与 pyproject.toml 保持一致(pillow 13 未在项目支持范围内)
_DEPENDENCY_SPECS = {
    "playwright": "playwright>=1.49,<2",
    "requests": "requests>=2.31,<3",
    "pillow": "pillow>=10,<13",
    "sv-ttk": "sv-ttk>=2.6,<3",
    "pygetwindow": "pygetwindow>=0.0.9",
}


def _missing_dependencies(only_required=False):
    """返回缺失依赖的 pip 名列表(不 import, 只看 find_spec)。"""
    from importlib import util as importlib_util

    missing = []
    for pip_name, import_name, required in _DEPENDENCIES:
        if only_required and not required:
            continue
        try:
            found = importlib_util.find_spec(import_name) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            missing.append(pip_name)
    return missing


def _write_error_log(detail):
    """把完整堆栈写到 logs/gui_error.log, 方便用户反馈时直接提供。"""
    try:
        err_dir = os.path.join(get_base_dir(), "logs")
        os.makedirs(err_dir, exist_ok=True)
        with open(os.path.join(err_dir, "gui_error.log"), "a", encoding="utf-8") as f:
            f.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n{detail}\n")
    except Exception:
        pass


def _report_missing_dependency(missing):
    """缺第三方库时给出可执行指引, 而不是抛一堆堆栈。"""
    message = TXT.fill(
        TXT.MSG_MISSING_DEP,
        names=", ".join(missing),
        python=sys.executable,
        packages=" ".join(missing),
    )
    log_text.insert(tk.END, message + "\n", "err")
    log_text.see(tk.END)
    messagebox.showerror(TXT.MSG_MISSING_DEP_TITLE, message)
    status_bus.set_state(
        STATE_ERROR, TXT.fill(TXT.STATE_MISSING_DEPS, names=", ".join(missing))
    )


# === 依赖自动安装 ===
# 启动自检发现缺包时, 直接用**当前解释器**的 pip 装回来, 用户不必手敲命令。
# 关闭方式: config.ini 的 [script-option] autoInstallDeps = False
# 注意: 这里只装 pip 包; 滑块答题用的 numpy/opencv 由 modules.installer 自己下 whl。
# 国内直连 pypi.org 经常是"能连上但不给数据", 所以镜像优先, 官方源最后兜底。
_PIP_INDEXES = (
    ("https://pypi.tuna.tsinghua.edu.cn/simple", TXT.PIP_INDEX_TSINGHUA),
    ("https://mirrors.aliyun.com/pypi/simple", TXT.PIP_INDEX_ALIYUN),
    (None, TXT.PIP_INDEX_OFFICIAL),
)

_deps_state = {"busy": False, "ok": True}
_deps_result = queue.Queue()


def _auto_install_enabled():
    """是否允许自动装依赖。默认允许; 离线环境可在配置里关掉。"""
    try:
        raw = config.get("script-option", "autoInstallDeps", fallback="True")
    except Exception:
        return True
    return str(raw).strip().lower() not in ("false", "0", "no", "off")


def _run_python(args, timeout=900):
    """用当前解释器跑子进程, 返回 (returncode, 合并后的输出文本)。

    打包为窗口模式后没有控制台, 输出必须自己收; 也不能弹出黑色命令行窗口。
    """
    cmd = [sys.executable, *args]
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            **kwargs,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, f"{type(exc).__name__}: {exc}"
    output = proc.stdout or b""
    if isinstance(output, bytes):
        output = output.decode("utf-8", "replace")
    return proc.returncode, output


def _pip_install(packages, timeout=240):
    """调 pip 装包, 按镜像优先级逐个尝试, 返回 (是否成功, 最后的失败输出)。

    --timeout/--retries 很关键: 默认 pip 在"连得上但不发数据"的源上会一直等,
    表现为界面卡在"正在安装依赖"十几分钟没有任何输出。
    """
    code, _ = _run_python(["-m", "pip", "--version"], timeout=120)
    if code != 0:
        # 精简版解释器/手工建的 venv 可能没有 pip
        _push_log("WARN", TXT.LOG_DEPS_NO_PIP)
        _run_python(["-m", "ensurepip", "--upgrade"], timeout=300)
    last = ""
    for index, label in _PIP_INDEXES:
        args = [
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "--no-warn-script-location",
            "--progress-bar",
            "off",
            "--timeout",
            "15",
            "--retries",
            "1",
        ]
        if index:
            args += ["-i", index]
        args += list(packages)
        _push_log(
            "INFO",
            TXT.fill(TXT.LOG_DEPS_INDEX, label=label, packages=", ".join(packages)),
        )
        code, output = _run_python(args, timeout)
        if code == 0:
            return True, ""
        last = output or TXT.fill(
            TXT.LOG_DEPS_PIP_FAILED, label=label, code=code
        )
    return False, last


def _push_log(level, text):
    """线程安全地往日志面板投递一行(由 pump_logs 在主线程渲染)。"""
    log_queue.put((level, text))


def _bootstrap_dependencies(missing):
    """后台线程: 装齐缺失依赖, 结果放进 _deps_result 交给主线程收尾。"""
    required = _missing_dependencies(only_required=True)
    optional = [name for name in missing if name not in required]
    ok = True
    left = required
    try:
        if required:
            _push_log("INFO", TXT.fill(TXT.LOG_DEPS_PYTHON, python=sys.executable))
            # 带上版本约束再装, 免得装出项目不支持的大版本
            ok, tail = _pip_install([_DEPENDENCY_SPECS.get(name, name) for name in required])
            if not ok:
                _push_log("ERROR", TXT.LOG_DEPS_FAILED_HEAD)
                _push_log(
                    "ERROR",
                    TXT.fill(
                        TXT.LOG_DEPS_MANUAL_CMD,
                        python=sys.executable,
                        packages=" ".join(required),
                    ),
                )
                for line in [ln for ln in tail.splitlines() if ln.strip()][-6:]:
                    _push_log("ERROR", f"   {line}")
        if ok and optional:
            # 非必需依赖(主题/窗口控制)装不上不影响刷课
            _pip_install(
                [_DEPENDENCY_SPECS.get(name, name) for name in optional], timeout=120
            )
        importlib.invalidate_caches()
        left = _missing_dependencies(only_required=True)
        ok = ok and not left
    except Exception as exc:  # 装依赖失败不能把界面拖死
        ok = False
        left = required or missing
        _push_log(
            "ERROR",
            TXT.fill(TXT.LOG_DEPS_EXC, type=type(exc).__name__, error=exc),
        )
    _deps_result.put((ok, left))


def _start_dependency_bootstrap(missing):
    """在主线程启动后台安装, 期间禁用"开始运行"。"""
    if _deps_state["busy"]:
        return
    _deps_state["busy"] = True
    start_btn.config(state="disabled")
    status_bus.set_state(
        STATE_PREPARING, TXT.fill(TXT.STATE_DEPS_INSTALLING, names=", ".join(missing))
    )
    threading.Thread(
        target=_bootstrap_dependencies, args=(missing,), daemon=True
    ).start()


def _poll_dependency_result():
    """由状态泵在主线程调用: Tk 控件只能在主线程改。"""
    try:
        ok, left = _deps_result.get_nowait()
    except queue.Empty:
        return
    _deps_state["busy"] = False
    _deps_state["ok"] = ok
    if ok:
        _push_log("INFO", TXT.LOG_DEPS_OK)
        if _HAS_SV_TTK is False:
            _push_log("INFO", TXT.LOG_DEPS_THEME)
        status_bus.set_state(STATE_IDLE, TXT.STATE_DEPS_READY)
    else:
        names = ", ".join(left) if left else TXT.STATE_UNKNOWN
        _push_log("ERROR", TXT.fill(TXT.LOG_DEPS_LEFT, names=names))
        status_bus.set_state(
            STATE_ERROR, TXT.fill(TXT.STATE_MISSING_DEPS, names=names)
        )
        messagebox.showerror(
            TXT.MSG_DEPS_FAILED_TITLE,
            TXT.fill(
                TXT.MSG_DEPS_FAILED,
                names=names,
                python=sys.executable,
                packages=names,
            ),
        )
    start_btn.config(state="normal")


# === 界面事件 ===
def show_help():
    notebook.select(help_page)


def open_homepage():
    import webbrowser

    webbrowser.open("https://github.com/CXrunfree/Autovisor")


def read_inputs():
    return {
        "username": username_var.get().strip(),
        "password": password_var.get(),
        "driver": driver_var.get(),
        "exe_path": exe_path_var.get().strip(),
        "limit_time": limit_time_var.get().strip(),
        "speed": speed_var.get().strip(),
        "auto_captcha": verify_var.get(),
        "hide_window": hide_var.get(),
        "mute": mute_var.get(),
        "restart": restart_var.get(),
        "donate": donate_var.get(),
        "skip_done": skip_done_var.get(),
        "daily_limit": daily_limit_var.get().strip(),
    }


def _collect_course_urls():
    """读取课程链接多行文本框: 每行一个链接, 忽略空行。"""
    raw = course_text.get("1.0", tk.END)
    return [line.strip() for line in raw.splitlines() if line.strip()]


def save_config_from_inputs():
    inputs = read_inputs()
    # 重写 [course-url]: 按行写成 URL1..URLn, 只保留非空链接
    if config.has_section("course-url"):
        config.remove_section("course-url")
    config.add_section("course-url")
    for index, url in enumerate(_collect_course_urls(), 1):
        config.set("course-url", f"URL{index}", url)
    config.set("user-account", "username", inputs["username"])
    config.set("user-account", "password", inputs["password"])
    config.set("browser-option", "driver", inputs["driver"])
    config.set("browser-option", "EXE_PATH", inputs["exe_path"])
    config.set("script-option", "enableAutoCaptcha", inputs["auto_captcha"])
    config.set("script-option", "enableHideWindow", inputs["hide_window"])
    config.set("script-option", "showDonateCode", inputs["donate"])
    # 开机自启动 = 左下角小窗 + 自动开始刷课, 界面上只有一个开关。
    # 这两个键仍按 True 写回, 保持与旧配置/命令行参数兼容。
    config.set("script-option", "startupMiniWindow", "True")
    config.set("script-option", "startupAutoRun", "True")
    # 每天累计刷满多少分钟后开机不再自启动(0 = 不限制)
    config.set(
        "script-option",
        "startupDailyLimitMinutes",
        inputs["daily_limit"] or "60",
    )
    config.set("course-option", "limitMaxTime", inputs["limit_time"])
    config.set("course-option", "limitSpeed", inputs["speed"])
    config.set("course-option", "soundOff", inputs["mute"])
    config.set("course-option", "skipCompletedCourse", inputs["skip_done"])
    # 只改开关, 等待阶梯沿用 config.ini [restart].backoffSchedule
    config.set("restart", "enableRestart", inputs["restart"])
    with open(config_file, "w", encoding="utf-8") as f:
        config.write(f)


def _bool_str(section, option, default):
    """把配置里的 True/False 规整成 UI 使用的字符串。"""
    try:
        value = config.get(section, option, raw=True, fallback=str(default)).strip().lower()
    except Exception:
        return "False"
    return "True" if value == "true" else "False"


def _get(section, option, default=""):
    try:
        return config.get(section, option, raw=True, fallback=default).strip()
    except Exception:
        return default


def load_config_into_ui():
    """启动时把 config.ini 里已保存的配置回显到界面。

    全部用 raw=True 读取: 密码/链接里出现 % 或 $ 时不会被插值语法打断。
    任何一项读失败都只跳过该项, 不影响窗口正常打开。
    """
    username_var.set(_get("user-account", "username"))
    password_var.set(_get("user-account", "password"))

    driver = _get("browser-option", "driver", "Edge").lower()
    driver_var.set("Edge" if driver in ("edge", "msedge") else "Chrome")
    exe_path_var.set(_get("browser-option", "EXE_PATH"))

    urls = []
    if config.has_section("course-url"):
        for key in config.options("course-url"):
            value = _get("course-url", key)
            if value:
                urls.append(value)
    course_text.delete("1.0", tk.END)
    if urls:
        course_text.insert("1.0", "\n".join(urls))

    limit_time_var.set(_get("course-option", "limitMaxTime", "30"))
    speed_var.set(_get("course-option", "limitSpeed", "1.0"))

    verify_var.set(_bool_str("script-option", "enableAutoCaptcha", True))
    hide_var.set(_bool_str("script-option", "enableHideWindow", False))
    mute_var.set(_bool_str("course-option", "soundOff", True))
    restart_var.set(_bool_str("restart", "enableRestart", True))
    donate_var.set(_bool_str("script-option", "showDonateCode", False))
    skip_done_var.set(_bool_str("course-option", "skipCompletedCourse", True))
    daily_limit_var.set(_get("script-option", "startupDailyLimitMinutes", "60"))
    # 自启动以注册表为准, 不看 config.ini
    autostart_var.set("True" if autostart.is_enabled() else "False")


def _schedule_values():
    """读取 [restart].backoffSchedule 的原始分段(字符串列表)。"""
    raw = _get("restart", "backoffSchedule", "10,10,10,20,20,30")
    return [part.strip() for part in raw.replace("，", ",").split(",") if part.strip()]


def _schedule_text():
    """把阶梯表显示成 "10/10/10/20/20/30 秒"。"""
    parts = _schedule_values()
    if not parts:
        return TXT.SCHEDULE_NONE
    return TXT.fill(TXT.SCHEDULE_SECONDS, parts="/".join(parts))


def _mask_account(name):
    """运行页只显示脱敏账号, 避免投屏/截图时泄露手机号。"""
    name = (name or "").strip()
    if not name:
        return TXT.SUMMARY_ACCOUNT_EMPTY
    if len(name) <= 3:
        return name[0] + "*" * (len(name) - 1)
    return name[:3] + "*" * (len(name) - 3)


def update_run_summary(*_):
    """运行页那行配置摘要: 只保留账号/课程数/时长/倍速/重试。"""
    try:
        inputs = read_inputs()
        url_count = len(_collect_course_urls())
    except Exception:
        return
    limit = inputs["limit_time"] or "30"
    time_text = (
        TXT.SUMMARY_NO_LIMIT
        if limit.strip() == "0"
        else TXT.fill(TXT.SUMMARY_MINUTES, minutes=limit)
    )
    speed = inputs["speed"] or "1.0"
    parts = [
        _mask_account(inputs["username"]),
        TXT.fill(TXT.SUMMARY_COURSE_COUNT, count=url_count),
        time_text,
        f"{speed}x",
        inputs["driver"],
    ]
    if inputs["restart"] == "True":
        parts.append(TXT.fill(TXT.SUMMARY_RETRY, schedule=_schedule_text()))
    run_summary_var.set(TXT.SUMMARY_SEPARATOR.join(parts))


def gui_log_sink(level, text):
    """Logger 回调: 把日志投递到队列, 由 UI 线程定时取出渲染。

    日志面板显示**全部**级别(含 EVENT 事件流水), 方便排查;
    小窗会过滤 EVENT, 那一行留给 INFO/DEBUG/WARN/ERROR。
    """
    log_queue.put((level, text))


def pump_logs():
    try:
        while True:
            level, text = log_queue.get_nowait()
            tag = None
            if level == "ERROR":
                tag = "err"
            elif level in ("WARN", "EVENT"):
                tag = "warn"
            log_text.insert(tk.END, text + "\n", tag)
            log_text.see(tk.END)
    except queue.Empty:
        pass
    root.after(120, pump_logs)


# === 运行状态面板: 进度条 / 状态灯 / 简要说明 ===
# 状态文字在 ui_text.py 的 STATE_LABEL 里改
_STATE_TEXT = TXT.STATE_LABEL

_STATE_COLOR = {
    STATE_IDLE: "#9aa0a6",
    STATE_PREPARING: "#e8a33d",
    STATE_RUNNING: "#2ecc71",
    STATE_STOPPING: "#e8a33d",
    STATE_FINISHED: "#2e8b57",
    STATE_ERROR: "#e74c3c",
}

_BRIEF_COLOR = {
    "INFO": "#555555",
    "DEBUG": "#7b8089",
    "WARN": "#b9770e",
    "ERROR": "#c0392b",
}

# 小窗只挡 EVENT(事件流水), INFO/DEBUG/WARN/ERROR 都正常显示
_MINI_LEVELS = ("INFO", "DEBUG", "WARN", "ERROR")

_blink_state = {"tick": 0}

# 事件循环完全没动静超过这么久, 认为已经卡死(仅提示, 不擅自杀进程)
_STALL_SECONDS = 600


def _mix_with_white(color, ratio=0.45):
    """把颜色按比例调亮, 用于进度条闪烁。"""
    try:
        color = color.lstrip("#")
        r, g, b = (int(color[i : i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return color
    r = int(r + (255 - r) * ratio)
    g = int(g + (255 - g) * ratio)
    b = int(b + (255 - b) * ratio)
    return f"#{r:02x}{g:02x}{b:02x}"


def _draw_progress_bar(percent, state, active, blink_on):
    width = bar_canvas.winfo_width()
    height = bar_canvas.winfo_height()
    if width <= 1 or height <= 1:
        return
    bar_canvas.delete("all")
    bar_canvas.create_rectangle(0, 0, width, height, fill="#dcdcdc", outline="")
    ratio = max(0.0, min(1.0, float(percent) / 100.0))
    fill_width = int(round(width * ratio))
    color = _STATE_COLOR.get(state, "#0a84ff")
    if active and not blink_on:
        # 闪烁: 运行中每 500ms 在正常/高亮之间切换
        color = _mix_with_white(color, 0.55)
    if fill_width > 0:
        bar_canvas.create_rectangle(0, 0, fill_width, height, fill=color, outline="")


def render_status(snapshot, blink_on=True):
    """把状态快照渲染到进度条 / 状态灯 / 简要说明。"""
    state = (snapshot or {}).get("state") or STATE_IDLE
    percent = float((snapshot or {}).get("percent") or 0.0)
    color = _STATE_COLOR.get(state, "#9aa0a6")
    # 仅"正在刷课"时闪烁; 准备/停止等阶段保持常亮, 避免误解
    blinking = state == STATE_RUNNING

    dot_label.configure(foreground=color if (blink_on or not blinking) else _panel_bg)

    text = _STATE_TEXT.get(state, state)
    title = (snapshot or {}).get("course_title")
    if state == STATE_RUNNING and title:
        text = TXT.fill(TXT.STATUS_WITH_COURSE, state=text, title=title)
    state_var.set(text)

    # 刷课时进度条跟着"当前这一节"走, 和平台页面显示的数字一致;
    # 其余状态显示整门课的总进度。总进度会同时以文字给出, 不再出现
    # "本课 70%、界面只有 7%"这种看着像少了一位数的情况。
    lesson = float((snapshot or {}).get("lesson_percent") or 0.0)
    if state == STATE_RUNNING and lesson > 0:
        shown = lesson
        pct_var.set(
            TXT.fill(
                TXT.PERCENT_BOTH,
                lesson=f"{lesson:.0f}",
                total=f"{percent:.0f}",
            )
        )
    else:
        shown = percent
        pct_var.set(TXT.fill(TXT.PERCENT_TOTAL, total=f"{percent:.0f}"))
    brief_var.set(format_brief(snapshot))
    brief_label.configure(
        foreground=_BRIEF_COLOR.get((snapshot or {}).get("message_level"), "#555555")
    )
    _draw_progress_bar(shown, state, blinking, blink_on)


# ============ 无边框小窗 ============
_MIN_W = 300
# 离屏幕边缘的间距: 贴住左下角, 但不压住任务栏, 也不至于紧贴边框
_MINI_MARGIN = 12
_mini = {"win": None, "pos": None, "ticking": False}


def _mini_default_pos(win, height):
    """默认位置: 工作区(不含任务栏)左下角, 与浏览器窗口隐藏后的方向一致。"""
    left, top, right, bottom = window_guard.work_area()
    screen_w, screen_h = win.winfo_screenwidth(), win.winfo_screenheight()
    # 工作区按物理像素返回, 正常情况下与 Tk 坐标一致;
    # 万一 DPI 缩放让两者对不上, 就退回整屏, 保证小窗不会跑出可见区域。
    if not (0 <= left < right) or not (0 < bottom <= screen_h):
        left, top, right, bottom = 0, 0, screen_w, screen_h
    x = left + _MINI_MARGIN
    y = bottom - height - _MINI_MARGIN
    # 再夹一次: 多屏 / 缩放异常时也不会落到屏幕外
    x = min(max(x, left), max(left, right - _MIN_W - _MINI_MARGIN))
    y = max(y, top + _MINI_MARGIN)
    return x-12, y+12


def _mini_metrics(snapshot):
    """小窗的口径与主界面一致: 刷课时看本课, 其余看总进度。"""
    state = (snapshot or {}).get("state") or STATE_IDLE
    percent = float((snapshot or {}).get("percent") or 0.0)
    lesson = float((snapshot or {}).get("lesson_percent") or 0.0)
    if state == STATE_RUNNING and lesson > 0:
        return (
            state,
            lesson,
            TXT.fill(TXT.MINI_PERCENT_LESSON, lesson=f"{lesson:.0f}"),
        )
    return state, percent, TXT.fill(TXT.MINI_PERCENT_TOTAL, total=f"{percent:.0f}")


def _truncate(text, limit=24):
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _draw_mini_bar(canvas, shown, state):
    width, height = canvas.winfo_width(), canvas.winfo_height()
    if width <= 1:
        # 首帧布局未完成时用估算值, 避免进度条空一帧
        width = max(canvas.winfo_reqwidth(), _MIN_W - 22)
    if height <= 1:
        height = 6
    canvas.delete("all")
    canvas.create_rectangle(0, 0, width, height, fill="#e4e7eb", outline="")
    fill_w = int(round(width * max(0.0, min(1.0, shown / 100.0))))
    if fill_w > 0:
        canvas.create_rectangle(
            0, 0, fill_w, height, fill=_STATE_COLOR.get(state, "#0a84ff"), outline=""
        )


def _build_mini_window():
    win = tk.Toplevel(root)
    win.withdraw()  # 先摆好再显示, 避免在屏幕上闪一下
    win.overrideredirect(True)  # 无边框 / 无标题栏
    win.attributes("-topmost", True)
    try:
        win.attributes("-alpha", 0.97)
    except tk.TclError:
        pass
    win.configure(bg="#ffffff")

    shell = tk.Frame(
        win, bg="#ffffff", highlightthickness=1, highlightbackground="#c3c8d0"
    )
    shell.pack(fill=tk.BOTH, expand=True)

    head = tk.Frame(shell, bg="#ffffff")
    head.pack(fill=tk.X, padx=10, pady=(7, 0))

    dot = tk.Label(head, text="●", bg="#ffffff", fg="#9aa0a6", font=(_FONT, 9))
    dot.pack(side=tk.LEFT)
    title = tk.Label(
        head,
        text=TXT.MINI_TITLE_INIT,
        bg="#ffffff",
        fg="#2b2f36",
        font=(_FONT, 10, "bold"),
    )
    title.pack(side=tk.LEFT, padx=(6, 0))
    pct = tk.Label(head, text="", bg="#ffffff", fg="#6b7078", font=(_FONT, 9))
    pct.pack(side=tk.RIGHT)

    bar = tk.Canvas(shell, height=6, bg="#ffffff", highlightthickness=0, bd=0)
    bar.pack(fill=tk.X, padx=10, pady=(6, 6))

    brief = tk.Label(
        shell, text="", bg="#ffffff", fg="#555555", font=(_FONT, 9), anchor="w"
    )
    brief.pack(fill=tk.X, padx=10, pady=(0, 7))

    win.update_idletasks()
    height = max(70, win.winfo_reqheight())
    pos = _mini.get("pos")
    if pos:
        x, y = pos
    else:
        x, y = _mini_default_pos(win, height)
    win.geometry(f"{_MIN_W}x{height}+{x}+{y}")

    _mini.update(
        {
            "win": win,
            "dot": dot,
            "title": title,
            "pct": pct,
            "bar": bar,
            "brief": brief,
            "height": height,
        }
    )

    # 拖动移动 / 单击还原(位移小于阈值才当作点击, 避免拖完就跳回去)
    drag = {"sx": 0, "sy": 0, "wx": 0, "wy": 0, "moved": False}

    def on_press(event):
        drag.update(
            sx=event.x_root,
            sy=event.y_root,
            wx=win.winfo_x(),
            wy=win.winfo_y(),
            moved=False,
        )

    def on_drag(event):
        dx = event.x_root - drag["sx"]
        dy = event.y_root - drag["sy"]
        if abs(dx) > 3 or abs(dy) > 3:
            drag["moved"] = True
        # 夹在屏幕内, 避免拖出可见区域后找不回来
        nx = min(max(drag["wx"] + dx, 0), win.winfo_screenwidth() - win.winfo_width())
        ny = min(max(drag["wy"] + dy, 0), win.winfo_screenheight() - win.winfo_height())
        win.geometry(f"+{nx}+{ny}")

    def on_release(_event):
        if drag["moved"]:
            # 记住拖到的位置, 下次进小窗还在这里
            _mini["pos"] = (win.winfo_x(), win.winfo_y())
        else:
            exit_mini_mode()

    menu = tk.Menu(win, tearoff=0)
    menu.add_command(label=TXT.MINI_MENU_BACK, command=exit_mini_mode)
    menu.add_separator()
    menu.add_command(label=TXT.MINI_MENU_QUIT, command=lambda: on_close(force=True))

    def on_menu(event):
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    for widget in (win, shell, head, dot, title, pct, bar, brief):
        widget.bind("<ButtonPress-1>", on_press)
        widget.bind("<B1-Motion>", on_drag)
        widget.bind("<ButtonRelease-1>", on_release)
        widget.bind("<Button-3>", on_menu)


def _refresh_mini():
    win = _mini.get("win")
    if win is None:
        _mini["ticking"] = False
        return
    try:
        if not win.winfo_exists():
            _mini["ticking"] = False
            return
        snapshot = status_bus.snapshot()
        state, shown, pct_text = _mini_metrics(snapshot)
        color = _STATE_COLOR.get(state, "#9aa0a6")

        # 刷课中状态灯随主界面一起闪, 表示"正在跑"
        blink = state == STATE_RUNNING and _blink_state["tick"] >= 2
        _mini["dot"].configure(fg="#ffffff" if blink else color)
        _mini["title"].configure(text=_STATE_TEXT.get(state, state), fg="#2b2f36")
        _mini["pct"].configure(text=pct_text, fg=color if state != STATE_IDLE else "#6b7078")

        # 小窗只挡 EVENT: 事件流水只留在日志面板/日志文件里
        snap = snapshot or {}
        message = snap.get("message") or ""
        if snap.get("message_level") not in _MINI_LEVELS:
            message = ""
        brief = _truncate(message or format_brief(snapshot))
        _mini["brief"].configure(
            text=brief,
            fg=_BRIEF_COLOR.get(snap.get("message_level"), "#555555"),
        )
        _draw_mini_bar(_mini["bar"], shown, state)
        win.after(300, _refresh_mini)
    except tk.TclError:
        _mini["ticking"] = False


def enter_mini_mode():
    """隐藏主窗口, 只留一个无边框小窗。"""
    win = _mini.get("win")
    if win is None or not win.winfo_exists():
        _build_mini_window()
        win = _mini["win"]
    root.withdraw()
    win.deiconify()
    win.lift()
    win.attributes("-topmost", True)
    # 先让 Tk 完成布局: 否则首帧进度条宽度还是 1, 会画不出来
    try:
        win.update_idletasks()
    except tk.TclError:
        pass
    if not _mini["ticking"]:
        _mini["ticking"] = True
        _refresh_mini()


def exit_mini_mode():
    """从小窗回到主界面。"""
    win = _mini.get("win")
    if win is not None:
        try:
            _mini["pos"] = (win.winfo_x(), win.winfo_y())
            win.destroy()
        except tk.TclError:
            pass
    _mini["win"] = None
    _mini["ticking"] = False
    root.deiconify()
    root.lift()
    root.focus_force()


def _bring_to_front():
    """被另一个启动请求唤醒: 把窗口从"小窗/隐藏"状态拉回前台。"""
    win = _mini.get("win")
    if win is not None:
        exit_mini_mode()
    try:
        root.deiconify()
        root.lift()
        # 短暂置顶, 保证不被其它窗口盖住; 400ms 后撤掉, 免得一直压着别的程序
        root.attributes("-topmost", True)
        root.after(400, _drop_topmost)
        root.focus_force()
    except tk.TclError:
        pass


def _drop_topmost():
    try:
        root.attributes("-topmost", False)
    except tk.TclError:
        pass


def _poll_activation():
    """有人又启动了程序(双击 exe / 再次开机项)时, 唤出本实例而不是再开一个。"""
    if single_instance.take_notify():
        _bring_to_front()


def pump_status():
    """定时拉取状态快照: 250ms 一次, 每两次为一个亮灭周期。"""
    _blink_state["tick"] = (_blink_state["tick"] + 1) % 4
    snapshot = status_bus.snapshot()
    render_status(snapshot, blink_on=_blink_state["tick"] < 2)
    _poll_dependency_result()
    _poll_activation()
    _check_stall(snapshot)
    root.after(250, pump_status)


def _check_stall(snapshot):
    """长时间没有任何状态更新时提醒一次(只提示, 不擅自杀进程)。"""
    if not (snapshot or {}).get("active"):
        _stall["since"] = time.time()
        _stall["warned"] = False
        return
    updated = float((snapshot or {}).get("updated_at") or 0)
    if time.time() - max(updated, _stall["since"]) < _STALL_SECONDS:
        return
    if _stall["warned"]:
        return
    _stall["warned"] = True
    log_queue.put(
        (
            "WARN",
            TXT.fill(TXT.LOG_WATCHDOG, minutes=_STALL_SECONDS // 60),
        )
    )


def build_config():
    """读取 config.ini 生成 Config 对象, 并把界面上的开关盖上去。"""
    import Autovisor

    ensure_config_file()
    base_dir = get_base_dir()
    cfg = Autovisor.Config(
        os.path.join(base_dir, "config.ini"),
        os.path.join(base_dir, "data", "mirrors.json"),
    )
    # 赞赏码是否弹出由界面决定, 不改配置文件里的原值
    cfg.showDonateCode = donate_var.get() == "True"
    return cfg


def _check_runtime():
    """启动前的环境自检: 返回错误文案, 没问题返回 None。"""
    import platform

    supported = ((3, 10), (3, 11), (3, 12), (3, 13))
    current = (sys.version_info.major, sys.version_info.minor)
    if current not in supported:
        names = ", ".join(f"{major}.{minor}" for major, minor in supported)
        return TXT.fill(
            TXT.MSG_PY_UNSUPPORTED,
            version=platform.python_version(),
            names=names,
            python=sys.executable,
        )
    missing = _missing_dependencies(only_required=True)
    if missing:
        # 走到这里说明自动安装没成功(或用户在配置里关了它)
        return TXT.fill(
            TXT.MSG_MISSING_REQUIRED,
            names=", ".join(missing),
            python=sys.executable,
            packages=" ".join(missing),
        )
    return None


def start_run(interactive=True):
    """唯一的启动入口: 先把界面上的配置写回 config.ini, 再开始刷课。

    interactive=False 用于开机自启动时的自动开始: 那一刻主窗口是藏着的,
    弹 messagebox 会突然抢焦点、挡住桌面, 所以把提示改成写日志与状态灯。
    """
    global running, worker_thread
    if running:
        return
    if _deps_state["busy"]:
        if interactive:
            messagebox.showinfo(TXT.MSG_BUSY_TITLE, TXT.MSG_BUSY)
        else:
            status_bus.set_state(STATE_PREPARING, TXT.STATE_DEPS_WAIT)
        return

    problem = _check_runtime()
    if problem:
        if interactive:
            messagebox.showerror(TXT.MSG_RUNTIME_TITLE, problem)
        else:
            _push_log("ERROR", TXT.LOG_BOOT_PREFIX + problem)
        status_bus.set_state(STATE_ERROR, TXT.STATE_RUNTIME_FAILED)
        return

    try:
        save_config_from_inputs()
    except Exception as exc:
        import traceback

        detail = traceback.format_exc()
        _write_error_log(detail)
        log_text.insert(tk.END, "\n" + detail)
        log_text.see(tk.END)
        if interactive:
            messagebox.showerror(
                TXT.MSG_SAVE_FAILED_TITLE,
                TXT.fill(TXT.MSG_SAVE_FAILED, error=exc, path=config_file),
            )
        else:
            _push_log(
                "ERROR",
                TXT.LOG_BOOT_PREFIX
                + TXT.fill(TXT.LOG_BOOT_SAVE_FAILED, error=exc),
            )
        status_bus.set_state(
            STATE_ERROR, TXT.fill(TXT.STATE_SAVE_FAILED, error=exc)
        )
        return

    try:
        cfg = build_config()
    except Exception as exc:
        import traceback

        detail = traceback.format_exc()
        _write_error_log(detail)
        log_text.insert(tk.END, "\n" + detail)
        log_text.see(tk.END)
        if interactive:
            messagebox.showerror(
                TXT.MSG_CONFIG_FAILED_TITLE,
                TXT.fill(TXT.MSG_CONFIG_FAILED, error=exc, path=config_file),
            )
        else:
            _push_log(
                "ERROR",
                TXT.LOG_BOOT_PREFIX
                + TXT.fill(TXT.LOG_BOOT_CONFIG_FAILED, error=exc),
            )
        status_bus.set_state(
            STATE_ERROR, TXT.fill(TXT.STATE_CONFIG_FAILED, error=exc)
        )
        return

    if not cfg.course_urls:
        # 配置不完整时直接把人送回配置页, 光标落在课程链接框
        if interactive:
            notebook.select(config_page)
            course_text.focus_set()
            messagebox.showwarning(TXT.MSG_NO_COURSE_TITLE, TXT.MSG_NO_COURSE)
        else:
            _push_log("WARN", TXT.LOG_BOOT_NO_COURSE)
        status_bus.set_state(STATE_ERROR, TXT.STATE_NO_COURSE)
        return

    # 从任何入口触发都保证停在运行页, 让用户看到进度
    notebook.select(run_page)

    # 初始化单例日志器并把 GUI 面板注册为消费者
    import Autovisor

    logger_inst = Autovisor.Logger()
    Autovisor.logger = logger_inst
    logger_inst.add_sink(gui_log_sink)
    # 立刻进入"准备中", 避免点击后到 main() 启动之间状态灯仍是空闲
    status_bus.set_state(STATE_PREPARING, TXT.LOG_STARTING)

    running = True
    _stall.update(since=time.time(), warned=False)
    start_btn.config(state="disabled")
    stop_btn.config(state="normal")
    _set_config_tab_locked(True)
    # 清掉上一轮遗留的停止标记。必须放在这里(启动线程之前),
    # 否则"启动后立刻点停止"会被工作线程里的 reset 抹掉, 点了没反应。
    Autovisor.reset_stop()
    log_text.insert(tk.END, "\n" + TXT.LOG_RUN_BEGIN + "\n")
    worker_thread = threading.Thread(target=_worker, args=(cfg,), daemon=True)
    worker_thread.start()


def _cancel_all_tasks(loop):
    """取消循环里残留的任务并等它们结束(参考 asyncio.runners)。

    少了这一步, 后台任务(验证码/静音/播放)会在 loop 关闭后才被 GC,
    于是报 "Task was destroyed but it is pending!" 或
    "Event loop is closed"。
    """
    import asyncio

    try:
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
    except Exception:
        return
    if not pending:
        return
    for task in pending:
        try:
            task.cancel()
        except Exception:
            pass
    try:
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
    except (RuntimeError, asyncio.CancelledError):
        pass


def _loop_torn_down(exc):
    """异常是否只是事件循环被中断/关闭, 而非真正的运行失败。"""
    text = str(exc or "")
    return (
        "Event loop is closed" in text
        or "Event loop stopped before Future completed" in text
    )


def _worker(cfg):
    import asyncio

    import Autovisor

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    Autovisor._running_loop = loop
    exit_code = 1
    task = None
    try:
        # 显式建任务并登记, 让"停止"能取消整个运行而不是硬停事件循环
        task = loop.create_task(Autovisor.supervise(cfg))
        Autovisor._running_task = task
        exit_code = loop.run_until_complete(task)
    except asyncio.CancelledError:
        try:
            Autovisor.logger.info(TXT.LOG_STOP_TASK_CANCELLED, shift=True)
        except Exception:
            pass
        exit_code = 1
    except RuntimeError as exc:
        # 不能不分青红皂白当成"用户点了停止", 否则真正的 RuntimeError
        # (如不支持的解释器版本)会被伪装成正常停止。
        if Autovisor.STOP_REQUESTED.is_set() or _loop_torn_down(exc):
            try:
                Autovisor.logger.info(TXT.LOG_STOP_LOOP, shift=True)
            except Exception:
                pass
        else:
            try:
                Autovisor.logger.log_exception(TXT.LOG_RUN_ERROR, exc, shift=True)
            except Exception:
                pass
            status_bus.set_state(
                STATE_ERROR, TXT.fill(TXT.STATE_RUN_FAILED, error=exc)
            )
        exit_code = 1
    except BaseException as exc:
        # SystemExit / KeyboardInterrupt 等: 若不管就会静默结束工作线程,
        # 界面卡在"运行中"且看不到任何原因。
        try:
            Autovisor.logger.error(
                TXT.fill(
                    TXT.LOG_UNEXPECTED_EXIT,
                    type=type(exc).__name__,
                    error=exc,
                ),
                shift=True,
            )
        except Exception:
            pass
        exit_code = 1
    finally:
        Autovisor._running_loop = None
        Autovisor._running_task = None
        # 与 asyncio.run 的收尾顺序一致: 先取消残留任务, 再关异步生成器/线程池,
        # 最后才 close。顺序反了就会在协程还没跑完时关掉循环。
        _cancel_all_tasks(loop)
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        try:
            loop.run_until_complete(loop.shutdown_default_executor())
        except Exception:
            pass
        try:
            loop.close()
        except Exception:
            pass

        def _done():
            global running
            running = False
            start_btn.config(state="normal")
            stop_btn.config(state="disabled")
            _set_config_tab_locked(False)
            log_text.insert(
                tk.END, "\n" + TXT.fill(TXT.LOG_RUN_END, code=exit_code) + "\n"
            )
            log_text.see(tk.END)
            # 同步收尾状态, 让状态灯/简要说明反映最终结果
            snap = status_bus.snapshot()
            try:
                stopped = Autovisor.STOP_REQUESTED.is_set()
            except Exception:
                stopped = False
            if stopped:
                status_bus.set_state(STATE_IDLE, TXT.LOG_STOP_MANUAL)
            elif exit_code == 0:
                if snap.get("state") != STATE_FINISHED:
                    status_bus.set_state(STATE_FINISHED, TXT.LOG_RUN_DONE)
            else:
                status_bus.set_state(
                    STATE_ERROR,
                    snap.get("error") or snap.get("message") or TXT.LOG_RUN_FAILED,
                )
            if _closing:
                root.destroy()

        root.after(0, _done)


def stop_run():
    import Autovisor

    if not running:
        return
    stop_btn.config(state="disabled")
    Autovisor.request_stop()


def on_close(force=False):
    """关闭窗口: 关之前先把可能还没落盘的设置写完, 再按运行状态决定是否先停课。"""
    global _closing
    try:
        _autosave_flush()  # 关窗 / 退出小窗前, 把最后一笔改动存下来
        _daily_flush()  # 学习时长也补一笔, 别把最后半分钟丢了
    except Exception:
        pass
    if running and not force:
        if not messagebox.askokcancel(TXT.MSG_EXIT_TITLE, TXT.MSG_EXIT_CONFIRM):
            return
        _closing = True
        stop_run()
        root.withdraw()
        return
    if running and force:
        stop_run()
    root.destroy()


def _enable_dpi_awareness():
    """Windows 高分屏(125%/150%)下避免窗口被系统拉伸模糊。

    必须在创建 Tk() 之前调用; 非 Windows 或调用失败时静默跳过,
    最坏情况只是回到系统默认缩放, 不影响功能。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


_enable_dpi_awareness()

# === GUI 构建 ===
root = tk.Tk()
root.title(TXT.APP_TITLE)
root.geometry("820x760+80+40")
root.minsize(700, 620)
root.resizable(True, True)
if _HAS_SV_TTK:
    sv_ttk.set_theme("light")

# 自启动小窗模式: 先把主窗口藏起来, 整个构建过程都不会闪出一个大窗
if _MINI_ON_START:
    root.withdraw()


def _label(parent, text, size=10, bold=False, **kwargs):
    font = (_FONT, size, "bold") if bold else (_FONT, size)
    return ttk.Label(parent, text=text, font=font, **kwargs)


notebook = ttk.Notebook(root)
notebook.pack(fill=tk.BOTH, expand=True, padx=_PAD, pady=(_PAD, _PAD))

run_page = ttk.Frame(notebook, padding=(4, 10, 4, 4))
config_page = ttk.Frame(notebook, padding=(4, 10, 4, 4))
help_page = ttk.Frame(notebook, padding=(4, 10, 4, 4))
notebook.add(run_page, text=TXT.TAB_RUN)
notebook.add(config_page, text=TXT.TAB_CONFIG)
notebook.add(help_page, text=TXT.TAB_HELP)
notebook.select(run_page)


def _set_config_tab_locked(locked):
    """运行期间禁用配置页, 避免改了配置却对当前这次运行无效。"""
    try:
        notebook.tab(config_page, state="disabled" if locked else "normal")
    except Exception:
        pass


# ============ 配置页 ============
# 输入框统一用 StringVar: 键盘、粘贴、拖放、程序回显任何来源的改动都会触发 trace,
# "修改即保存"就不依赖某个具体按键事件(中文输入法、右键粘贴都不会漏)。
username_var = tk.StringVar()
password_var = tk.StringVar()
exe_path_var = tk.StringVar()
limit_time_var = tk.StringVar()
speed_var = tk.StringVar()

account_frame = ttk.LabelFrame(config_page, text=TXT.GROUP_ACCOUNT, padding=(12, 8))
account_frame.pack(fill=tk.X)
account_frame.columnconfigure(1, weight=1)

_label(account_frame, TXT.LABEL_USERNAME).grid(
    row=0, column=0, sticky="e", padx=(0, 10), pady=4
)
username_entry = ttk.Entry(account_frame, textvariable=username_var, font=(_FONT, 10))
username_entry.grid(row=0, column=1, sticky="ew", pady=4)

_label(account_frame, TXT.LABEL_PASSWORD).grid(
    row=1, column=0, sticky="e", padx=(0, 10), pady=4
)
password_entry = ttk.Entry(account_frame, textvariable=password_var, show="*", font=(_FONT, 10))
password_entry.grid(row=1, column=1, sticky="ew", pady=4)

browser_frame = ttk.LabelFrame(config_page, text=TXT.GROUP_BROWSER, padding=(12, 8))
browser_frame.pack(fill=tk.X, pady=(8, 0))
browser_frame.columnconfigure(3, weight=1)

_label(browser_frame, TXT.LABEL_DRIVER).grid(
    row=0, column=0, sticky="e", padx=(0, 10), pady=4
)
driver_var = tk.StringVar(value="Edge")
driver_box = ttk.Combobox(
    browser_frame,
    textvariable=driver_var,
    values=("Edge", "Chrome"),
    state="readonly",
    width=10,
    font=(_FONT, 10),
)
driver_box.grid(row=0, column=1, sticky="w", pady=4)

_label(browser_frame, TXT.LABEL_BROWSER_PATH).grid(
    row=0, column=2, sticky="e", padx=(18, 10), pady=4
)
exe_path_entry = ttk.Entry(browser_frame, textvariable=exe_path_var, font=(_FONT, 10))
exe_path_entry.grid(row=0, column=3, sticky="ew", pady=4)
_label(
    browser_frame, TXT.HINT_BROWSER_PATH, size=9, foreground="#7b8089"
).grid(row=1, column=1, columnspan=3, sticky="w")

course_frame = ttk.LabelFrame(config_page, text=TXT.GROUP_COURSE, padding=(12, 8))
course_frame.pack(fill=tk.X, pady=(8, 0))
course_frame.columnconfigure(0, weight=1)

_label(course_frame, TXT.LABEL_COURSE_URLS).grid(row=0, column=0, sticky="w")
course_text = tk.Text(
    course_frame,
    height=6,
    width=40,
    wrap="none",
    font=("Consolas", 9),
    # 与 ttk.Entry 的细边框保持一致: 平时浅灰, 聚焦时变蓝
    relief="flat",
    borderwidth=0,
    highlightthickness=1,
    highlightbackground="#b9bfc7",
    highlightcolor="#4a90d9",
    padx=8,
    pady=6,
    undo=True,
)
course_scroll = ttk.Scrollbar(course_frame, orient="vertical", command=course_text.yview)
course_text.configure(yscrollcommand=course_scroll.set)
course_text.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
course_scroll.grid(row=1, column=1, sticky="ns", pady=(6, 0))

limit_row = ttk.Frame(course_frame)
limit_row.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))

_label(limit_row, TXT.LABEL_SPEED).pack(side=tk.LEFT)
speed_entry = ttk.Entry(
    limit_row, textvariable=speed_var, width=7, font=(_FONT, 10), justify="center"
)
speed_entry.pack(side=tk.LEFT, padx=(8, 0))

# 可选配置: 直接平铺的方框勾选, 一眼看清当前状态
verify_var = tk.StringVar(value="True")
hide_var = tk.StringVar(value="False")
mute_var = tk.StringVar(value="True")
restart_var = tk.StringVar(value="True")
donate_var = tk.StringVar(value="False")
skip_done_var = tk.StringVar(value="True")
# 开机自启动: 以注册表为准, 不存 config.ini
autostart_var = tk.StringVar(value="False")
# 每天累计刷满多少分钟后不再自启动(分钟, 0 = 不限制)
daily_limit_var = tk.StringVar(value="60")

opt_frame = ttk.LabelFrame(config_page, text=TXT.GROUP_OPTIONS, padding=(12, 8))
opt_frame.pack(fill=tk.X, pady=(8, 0))
opt_frame.columnconfigure(0, weight=1)
opt_frame.columnconfigure(1, weight=1)

# 顺序与 ui_text.OPTION_LABELS 一一对应
_OPTIONS = (
    (TXT.OPTION_LABELS[0], verify_var),
    (TXT.OPTION_LABELS[1], hide_var),
    (TXT.OPTION_LABELS[2], mute_var),
    (TXT.OPTION_LABELS[3], restart_var),
    (TXT.OPTION_LABELS[4], donate_var),
    (TXT.OPTION_LABELS[5], skip_done_var),
)

# ttk.Checkbutton 不支持 font 参数, 用派生样式统一字体
try:
    ttk.Style().configure("Option.TCheckbutton", font=(_FONT, 10))
    _opt_style = "Option.TCheckbutton"
except Exception:
    _opt_style = "TCheckbutton"

_OPTION_WIDGETS = {}
for _i, (_text, _var) in enumerate(_OPTIONS):
    _check = ttk.Checkbutton(
        opt_frame,
        text=_text,
        variable=_var,
        onvalue="True",
        offvalue="False",
        style=_opt_style,
    )
    _check.grid(row=_i // 2, column=_i % 2, sticky="w", padx=(0, 18), pady=3)
    _OPTION_WIDGETS[_text] = _check

for _var in (verify_var, hide_var, mute_var, restart_var, donate_var, skip_done_var):
    _var.trace_add("write", update_run_summary)

# ============ 开机自动启动 ============
# 小窗、开机自动开刷、写注册表其实是同一件事: 开机后在左下角安静地跑起来。
# 拆成三个勾选项只会让人反复确认它们之间的关系, 这里合成一个开关;
# 勾选后才展开"刷多久"的两个数字和今日进度。
boot_frame = ttk.LabelFrame(config_page, text=TXT.GROUP_AUTOSTART, padding=(12, 8))
boot_frame.pack(fill=tk.X, pady=(8, 0))

autostart_check = ttk.Checkbutton(
    boot_frame,
    text=TXT.CHECK_AUTOSTART,
    variable=autostart_var,
    onvalue="True",
    offvalue="False",
    style=_opt_style,
)
autostart_check.pack(anchor="w")

boot_body = ttk.Frame(boot_frame)

daily_row = ttk.Frame(boot_body)
daily_row.pack(fill=tk.X, pady=(8, 0))
ttk.Label(daily_row, text=TXT.LABEL_DAILY_LIMIT_PREFIX, font=(_FONT, 10)).pack(
    side=tk.LEFT
)
daily_limit_entry = ttk.Entry(
    daily_row, textvariable=daily_limit_var, width=5, font=(_FONT, 10), justify="center"
)
daily_limit_entry.pack(side=tk.LEFT, padx=(6, 6))
ttk.Label(daily_row, text=TXT.LABEL_DAILY_LIMIT_SUFFIX, font=(_FONT, 10)).pack(
    side=tk.LEFT
)

run_row = ttk.Frame(boot_body)
run_row.pack(fill=tk.X, pady=(6, 0))
ttk.Label(run_row, text=TXT.LABEL_RUN_LIMIT_PREFIX, font=(_FONT, 10)).pack(side=tk.LEFT)
time_limit_entry = ttk.Entry(
    run_row, textvariable=limit_time_var, width=5, font=(_FONT, 10), justify="center"
)
time_limit_entry.pack(side=tk.LEFT, padx=(6, 6))
ttk.Label(run_row, text=TXT.LABEL_RUN_LIMIT_SUFFIX, font=(_FONT, 10)).pack(side=tk.LEFT)

# 今日进度: 进度条比一句"还差 xx 分钟"直观, 扫一眼就知道离上限还有多远。
# 用 Canvas 自绘而不是 ttk.Progressbar: 后者在浅色主题下 0% 时只剩一条细线,
# 看着像分割线; 自绘能保证槽和填充都看得见, 也便于达标后换色。
class _DailyBar(tk.Canvas):
    def __init__(self, master, width=190, height=13):
        bg = ttk.Style().lookup("TFrame", "background") or "#f9f9f9"
        super().__init__(
            master,
            width=width,
            height=height,
            bg=bg,
            highlightthickness=0,
            bd=0,
        )
        self._percent = 0.0
        self.bind("<Configure>", lambda e: self._draw())

    def set(self, percent):
        self._percent = max(0.0, min(100.0, float(percent)))
        self._draw()

    def _draw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 1:
            return
        self.create_rectangle(0, 0, w, h, outline="#d3d8de", fill="#eceff2")
        filled = int(w * self._percent / 100.0)
        if filled >= 2:
            color = "#4a90d9" if self._percent < 100 else "#8a9bb0"
            self.create_rectangle(0, 0, filled, h, outline="", fill=color)


progress_row = ttk.Frame(boot_body)
progress_row.pack(fill=tk.X, pady=(10, 0))

daily_bar = _DailyBar(progress_row)
daily_bar.pack(side=tk.LEFT)
daily_text = ttk.Label(progress_row, text="", font=(_FONT, 9), foreground="#5f6b7a")
daily_text.pack(side=tk.LEFT, padx=(10, 0))
boot_reset_btn = ttk.Button(progress_row, text=TXT.BTN_RESET_DAILY, width=6)
boot_reset_btn.pack(side=tk.LEFT, padx=(8, 0))

# 开机自启动直接写注册表, 不存 config.ini(以系统里的真实状态为准)。
# 回显配置时也会 set 这个变量, 用 guard 跳过, 避免启动阶段误写注册表。
_autostart_guard = {"quiet": True}


def _set_boot_body_visible(*_):
    """勾选自启动才展开"刷多久"与今日进度, 没勾就收起来。"""
    if autostart_var.get() != "True":
        boot_body.pack_forget()
        return
    boot_body.pack(fill=tk.X)
    # 展开后内容变高: 窗口不够高就补到需要的高度, 否则输入框会被下边缘裁掉
    root.update_idletasks()
    need = root.winfo_reqheight()
    if root.winfo_height() < need:
        root.geometry(f"{root.winfo_width()}x{need}")


def _on_autostart_toggle(*_):
    if _autostart_guard["quiet"]:
        return
    want = autostart_var.get() == "True"
    if not autostart.set_enabled(want):
        _autostart_guard["quiet"] = True
        autostart_var.set("False" if want else "True")
        _autostart_guard["quiet"] = False
        _set_boot_body_visible()
        messagebox.showerror(
            TXT.MSG_AUTOSTART_FAILED_TITLE, TXT.MSG_AUTOSTART_FAILED
        )
        return
    if want:
        log_text.insert(
            tk.END,
            TXT.fill(TXT.LOG_AUTOSTART_ENABLED, target=autostart.launch_target())
            + "\n",
        )
    else:
        log_text.insert(tk.END, TXT.LOG_AUTOSTART_DISABLED + "\n")
    log_text.see(tk.END)


autostart_var.trace_add("write", _set_boot_body_visible)
autostart_var.trace_add("write", _on_autostart_toggle)

if not autostart.available():
    # 非 Windows 平台没有这套注册表机制, 直接禁掉而不是让用户点了没反应
    autostart_check.configure(state="disabled")

restart_hint = ttk.Label(
    config_page,
    text="",
    font=(_FONT, 9),
    foreground="#7b8089",
    wraplength=620,
    justify="left",
)
restart_hint.pack(anchor="w", pady=(6, 0))


def _refresh_restart_hint(*_):
    if restart_var.get() == "True":
        restart_hint.configure(
            text=TXT.fill(
                TXT.RESTART_HINT,
                schedule=_schedule_text(),
                count=len(_schedule_values()),
            )
        )
    else:
        restart_hint.configure(text=TXT.RESTART_HINT_OFF)


restart_var.trace_add("write", _refresh_restart_hint)

# ============ 每日学习时长计时 ============
# 只统计"正在刷课"的墙上时间: 空闲、停在配置页、程序没开都不算。
# 每 5 秒取样一次, 累计满 30 秒落盘一次 —— 断电最多丢 30 秒,
# 既不会白学一整天, 也不会每 5 秒都去写一次文件。
_DAILY_TICK_MS = 5000
_DAILY_FLUSH_SECONDS = 30
_daily = {"pending": 0.0, "tick": time.time()}


def _daily_flush():
    whole = int(_daily["pending"])
    if whole <= 0:
        return
    daily_budget.add(whole)
    _daily["pending"] -= whole
    _refresh_boot_hint()


def _daily_ticker():
    now = time.time()
    delta = now - _daily["tick"]
    _daily["tick"] = now
    # delta 过大说明机器休眠过/时钟跳变, 那段时间不算学习
    if running and 0 < delta < 60:
        _daily["pending"] += delta
        if _daily["pending"] >= _DAILY_FLUSH_SECONDS:
            _daily_flush()
    root.after(_DAILY_TICK_MS, _daily_ticker)


# 今日进度用进度条表示: 已经刷了多少 / 上限多少, 一眼能看完,
# 不用再读一句"还差 xx 分钟到上限"。达到上限后开机就静默跳过,
# 那段是没有界面的, 只能靠这里(和 logs/boot.log)事后确认。
def _daily_text(seconds, limit):
    """今日已刷 / 上限的文字: 分钟级上限就用分钟, 大上限才折成小时。"""
    minutes = int(seconds // 60)
    if limit >= 180 * 60:
        return TXT.fill(
            TXT.DAILY_PROGRESS_HOURS,
            done_h=minutes // 60,
            done_m=f"{minutes % 60:02d}",
            limit_h=int(limit // 3600),
        )
    return TXT.fill(
        TXT.DAILY_PROGRESS_MINUTES,
        done=minutes,
        limit=int(limit // 60),
    )


def _refresh_boot_hint(*_):
    seconds = daily_budget.seconds_today() + int(_daily["pending"])
    limit = daily_budget.limit_seconds(daily_limit_var.get())
    if limit <= 0:
        daily_bar.pack_forget()
        daily_text.configure(
            text=TXT.fill(
                TXT.DAILY_NO_LIMIT,
                done=daily_budget.format_duration(seconds),
            )
        )
        return
    daily_bar.pack(side=tk.LEFT, before=daily_text)
    percent = min(100.0, seconds * 100.0 / max(1, limit))
    daily_bar.set(percent)
    text = _daily_text(seconds, limit)
    if percent >= 100:
        text += TXT.DAILY_FULL_SUFFIX
    daily_text.configure(text=text)


def _reset_boot_today():
    """把今天的累计时长清零: 想今天就再自动刷一轮时用。"""
    _daily_flush()
    _daily["pending"] = 0.0
    if not daily_budget.reset():
        messagebox.showwarning(TXT.MSG_RESET_TITLE, TXT.MSG_RESET_FAILED)
        return
    _refresh_boot_hint()
    log_text.insert(tk.END, TXT.LOG_AUTOSTART_RESET + "\n")
    log_text.see(tk.END)


boot_reset_btn.configure(command=_reset_boot_today)
daily_limit_var.trace_add("write", _refresh_boot_hint)


# ============ 修改即保存 ============
# 配置页不再依赖「点某个按钮才存盘」: 任何一次改动都会在停止输入 600ms 后写回
# config.ini。600ms 是"打字过程中不要反复写文件"与"改完立刻生效"的折中;
# 关窗口、点「返回运行」、点「开始运行」时还会各强制落盘一次。
_AUTOSAVE_DELAY_MS = 600
_autosave = {"guard": True, "pending": None, "saved_at": None}
# 课程链接框的内容基线: 只有内容真的变了才算用户改动, 回显配置不算
_course_baseline = {"text": ""}


def _refresh_save_hint():
    # 平时不占地方, 只在真的存过一次之后显示时间
    if _autosave["saved_at"]:
        save_hint.configure(
            text=TXT.fill(TXT.SAVE_HINT, time=_autosave["saved_at"])
        )
    else:
        save_hint.configure(text="")


def _autosave_flush(*_):
    """立刻写盘; 有排队中的防抖定时器先取消, 保证只写一次。"""
    if _autosave["pending"] is not None:
        try:
            root.after_cancel(_autosave["pending"])
        except Exception:
            pass
        _autosave["pending"] = None
    if _autosave["guard"]:
        return
    try:
        save_config_from_inputs()
    except Exception as exc:
        # 写盘失败不能让输入框卡住: 只在页脚说明原因
        save_hint.configure(text=TXT.fill(TXT.SAVE_FAILED, error=exc))
        return
    _autosave["saved_at"] = time.strftime("%H:%M:%S")
    _refresh_save_hint()


def _autosave_schedule(*_):
    """每次改动都重新计时: 连续输入只会在停下来之后写一次文件。"""
    if _autosave["guard"]:
        return
    if _autosave["pending"] is not None:
        try:
            root.after_cancel(_autosave["pending"])
        except Exception:
            pass
    _autosave["pending"] = root.after(_AUTOSAVE_DELAY_MS, _autosave_flush)


def _on_course_modified(_event=None):
    """课程链接是 Text, 没有 StringVar: 用 <<Modified>> 捕获改动。

    回显配置、重置 modified 标志本身也会让 Tk 发一次这个事件,
    所以再和"上次已知的内容"比一次: 内容没变就不算用户改动, 不必写盘。
    """
    if not course_text.edit_modified():
        return
    course_text.edit_modified(False)
    text = course_text.get("1.0", tk.END)
    if text == _course_baseline["text"]:
        return
    _course_baseline["text"] = text
    update_run_summary()
    _autosave_schedule()


def goto_run_page():
    """立刻落盘并切回运行页(防抖定时器不再等待)。"""
    _autosave_flush()
    update_run_summary()
    notebook.select(run_page)


ttk.Frame(config_page).pack(fill=tk.BOTH, expand=True)

footer_row = ttk.Frame(config_page)
footer_row.pack(fill=tk.X, pady=(10, 0))
save_hint = ttk.Label(footer_row, text="", font=(_FONT, 9), foreground="#5f6b7a")
save_hint.pack(side=tk.LEFT)
ttk.Button(
    footer_row, text=TXT.BTN_BACK_TO_RUN, command=goto_run_page, width=14
).pack(side=tk.RIGHT)

# 输入框改动(键盘/粘贴/拖放/回显)统一由 StringVar 的 trace 捕获
for _var in (username_var, password_var, exe_path_var, limit_time_var, speed_var):
    _var.trace_add("write", _autosave_schedule)

course_text.bind("<<Modified>>", _on_course_modified)
course_text.edit_modified(False)

for _var in (
    driver_var,
    verify_var,
    hide_var,
    mute_var,
    restart_var,
    donate_var,
    skip_done_var,
    daily_limit_var,
):
    _var.trace_add("write", _autosave_schedule)

# ============ 运行页 ============
status_frame = ttk.LabelFrame(run_page, text=TXT.GROUP_STATUS, padding=(12, 8))
status_frame.pack(fill=tk.X)

head_row = ttk.Frame(status_frame)
head_row.pack(fill=tk.X)

dot_label = ttk.Label(head_row, text="●", font=("Segoe UI", 13), foreground="#9aa0a6")
dot_label.pack(side=tk.LEFT)

state_var = tk.StringVar(value=TXT.INIT_STATE)
ttk.Label(head_row, textvariable=state_var, font=(_FONT, 10, "bold")).pack(
    side=tk.LEFT, padx=(6, 0)
)

pct_var = tk.StringVar(value=TXT.INIT_PERCENT)
ttk.Label(head_row, textvariable=pct_var, font=(_FONT, 10), foreground="#3b4252").pack(
    side=tk.RIGHT
)

# 进度条用 Canvas 自绘: 便于闪烁与着色, 不受 ttk 主题样式限制
_panel_bg = ttk.Style().lookup("TFrame", "background") or "#f9f9f9"
bar_canvas = tk.Canvas(status_frame, height=16, highlightthickness=0, bd=0, bg=_panel_bg)
bar_canvas.pack(fill=tk.X, pady=(8, 6))

brief_var = tk.StringVar(value=TXT.INIT_BRIEF)
brief_label = ttk.Label(
    status_frame,
    textvariable=brief_var,
    font=(_FONT, 9),
    foreground="#555555",
    justify="left",
    wraplength=640,
)
brief_label.pack(fill=tk.X)

bar_canvas.bind(
    "<Configure>", lambda e: render_status(status_bus.snapshot(), _blink_state["tick"] < 2)
)
status_frame.bind(
    "<Configure>",
    lambda e: brief_label.configure(wraplength=max(220, e.width - 28)),
)

button_frame = ttk.Frame(run_page)
button_frame.pack(fill=tk.X, pady=(10, 6))

start_btn = ttk.Button(button_frame, text=TXT.BTN_START, command=start_run, width=16)
start_btn.pack(side=tk.LEFT)

stop_btn = ttk.Button(
    button_frame, text=TXT.BTN_STOP, command=stop_run, state="disabled", width=8
)
stop_btn.pack(side=tk.LEFT, padx=8)

ttk.Button(button_frame, text=TXT.BTN_HELP, command=show_help, width=7).pack(side=tk.RIGHT)
ttk.Button(button_frame, text=TXT.BTN_MINI, command=enter_mini_mode, width=7).pack(
    side=tk.RIGHT, padx=8
)
ttk.Button(
    button_frame,
    text=TXT.BTN_CONFIG,
    command=lambda: notebook.select(config_page),
    width=10,
).pack(side=tk.RIGHT)

# 本次将要运行的配置摘要
run_summary_var = tk.StringVar(value="")
ttk.Label(
    run_page, textvariable=run_summary_var, font=(_FONT, 9), foreground="#7b8089"
).pack(anchor="w", pady=(0, 6))

log_frame = ttk.LabelFrame(run_page, text=TXT.GROUP_LOG, padding=(8, 6))
log_frame.pack(fill=tk.BOTH, expand=True)

log_text = scrolledtext.ScrolledText(
    log_frame,
    height=10,
    wrap=tk.WORD,
    font=("Consolas", 9),
    bg="#fafafa",
    relief="flat",
    borderwidth=0,
    highlightthickness=0,
    padx=8,
    pady=6,
)
log_text.pack(fill=tk.BOTH, expand=True)
log_text.tag_config("err", foreground="#c0392b")
log_text.tag_config("warn", foreground="#b9770e")

# ============ 帮助与鸣谢 ============
help_box = scrolledtext.ScrolledText(
    help_page,
    height=8,
    width=40,
    wrap=tk.WORD,
    font=(_FONT, 10),
    bg="#ffffff",
    relief="flat",
    borderwidth=0,
    highlightthickness=0,
    padx=16,
    pady=12,
    cursor="arrow",
    spacing1=1,
    spacing3=1,
)
help_box.pack(fill=tk.BOTH, expand=True)
help_box.tag_config("h1", font=(_FONT, 12, "bold"), spacing1=2, spacing3=8)
help_box.tag_config("h2", font=(_FONT, 10, "bold"), spacing1=10, spacing3=4)
help_box.tag_config("item", lmargin1=18, lmargin2=18, spacing3=2)
help_box.tag_config("dim", foreground="#6b7078", lmargin1=18, lmargin2=18, spacing3=2)
help_box.tag_config(
    "mono",
    font=("Consolas", 9),
    foreground="#3b4252",
    lmargin1=18,
    lmargin2=18,
    spacing3=1,
)


def _help_write(text, tag=None):
    help_box.insert(tk.END, text, tag)


# 赞赏码原图四周留白很多(1229x1229 里只有中间约一半是二维码), 直接缩小到 200px
# 会糊成一片; 所以先按相对比例裁出中间那块再缩放。换了别的二维码图片时,
# 把 _HELP_QR_CROP 设为 None 就退化成整图缩放。
_HELP_QR_CROP = (0.255, 0.14, 0.745, 0.63)  # (左, 上, 右, 下) 均为相对比例
_HELP_QR_SIZE = 220


def _load_help_qrcode():
    """读 resources/QRcode.jpg 并缩放成适合嵌在帮助页里的图; 失败返回 None。"""
    path = os.path.join(get_base_dir(), "resources", "QRcode.jpg")
    if not os.path.exists(path):
        return None
    try:
        from PIL import Image, ImageTk

        image = Image.open(path).convert("RGB")
        if _HELP_QR_CROP:
            width, height = image.size
            left, top, right, bottom = _HELP_QR_CROP
            image = image.crop(
                (
                    int(width * left),
                    int(height * top),
                    int(width * right),
                    int(height * bottom),
                )
            )
        image.thumbnail((_HELP_QR_SIZE, _HELP_QR_SIZE), Image.LANCZOS)
        return ImageTk.PhotoImage(image)
    except Exception:  # 缺 Pillow / 图片损坏都不能让帮助页打不开
        return None


# 帮助页正文全部来自 ui_text.help_sections(), 改文案去 ui_text.py
for _tag, _text in TXT.help_sections(_schedule_text(), len(_schedule_values())):
    _help_write(_text, _tag)

# 文末放赞赏码: 图片对象必须留引用, 否则会被回收成空白
_help_write("\n", None)
_help_write(TXT.HELP_QR_TITLE + "\n", "h2")
help_qr_photo = _load_help_qrcode()
if help_qr_photo is not None:
    help_box.image_create(tk.END, image=help_qr_photo, padx=22, pady=6)
    _help_write("\n", None)
else:
    _help_write(TXT.HELP_QR_MISSING + "\n", "dim")

help_box.configure(state="disabled")

help_bar = ttk.Frame(help_page)
help_bar.pack(fill=tk.X, pady=(8, 0))
ttk.Label(
    help_bar,
    text=TXT.fill(TXT.HELP_VERSION_LINE, version=_VERSION),
    font=(_FONT, 9),
    foreground="#7b8089",
).pack(side=tk.LEFT)
ttk.Button(help_bar, text=TXT.BTN_HOMEPAGE, command=open_homepage, width=14).pack(
    side=tk.RIGHT
)

# 启动时回显已保存配置
load_config_into_ui()
update_run_summary()
_refresh_restart_hint()
_refresh_boot_hint()
_refresh_save_hint()
# 回显阶段所有的 set / insert 都会触发保存回调, 到这里才允许真正写盘
_course_baseline["text"] = course_text.get("1.0", tk.END)
_autosave["guard"] = False

# 程序目录被移动过时, 注册表里的旧路径会让开机启动失效: 这里静默改成当前路径
try:
    autostart.sync(True)
except Exception:
    pass
_autostart_guard["quiet"] = False
# 回显的自启动状态决定"刷多久"那一块要不要展开
_set_boot_body_visible()

# 切换页面时刷新摘要与今日学习时长(时长会随着刷课增长)
notebook.bind(
    "<<NotebookTabChanged>>",
    lambda e: (update_run_summary(), _refresh_boot_hint()),
)

# 每日学习时长取样: 开始时就跑起来, 运行中每 5 秒累加一次
root.after(1000, _daily_ticker)

# 首次运行自动生成/补齐配置时提示一次(属于实际发生的事件, 不是说明文字)
if _config_created:
    log_text.insert(tk.END, TXT.fill(TXT.LOG_CONFIG_CREATED, path=config_file) + "\n")
elif _config_added:
    log_text.insert(
        tk.END,
        TXT.fill(TXT.LOG_CONFIG_PATCHED, keys=", ".join(_config_added)) + "\n",
    )

# 单行输入框回车 → 保存并回到运行页; 课程链接框的回车用于换行
for _entry in (
    username_entry,
    password_entry,
    exe_path_entry,
    time_limit_entry,
    speed_entry,
    daily_limit_entry,
):
    _entry.bind("<Return>", lambda event: goto_run_page())

# 依赖自检: 缺包就地自动安装(装好自动放行); 关掉自动安装才回退成手动命令提示
_pending_deps = _missing_dependencies()
if _pending_deps:
    _pending_required = _missing_dependencies(only_required=True)
    if _auto_install_enabled():
        _push_log(
            "WARN" if _pending_required else "INFO",
            TXT.fill(
                TXT.LOG_DEPS_MISSING_AUTO, names=", ".join(_pending_deps)
            ),
        )
        # 等窗口画出来再装, 避免启动瞬间卡在"白窗 + 无输出"
        root.after(200, lambda: _start_dependency_bootstrap(_pending_deps))
    else:
        log_text.insert(
            tk.END,
            TXT.fill(
                TXT.LOG_DEPS_MISSING_MANUAL, names=", ".join(_pending_deps)
            )
            + "\n",
            "err" if _pending_required else "warn",
        )
        if _pending_required:
            log_text.insert(
                tk.END,
                TXT.fill(
                    TXT.LOG_DEPS_MANUAL_CMD,
                    python=sys.executable,
                    packages=" ".join(_pending_required),
                )
                + "\n",
                "err",
            )

# 选 Playwright 自带 Chromium 时才需要额外下载浏览器内核(Edge/Chrome 用系统安装的)
if str(config.get("browser-option", "driver", fallback="Edge")).strip().lower() == "chromium":
    log_text.insert(
        tk.END,
        TXT.LOG_DEPS_CHROMIUM
        + "\n"
        + TXT.fill(TXT.LOG_DEPS_CHROMIUM_CMD, python=sys.executable)
        + "\n",
        "warn",
    )

# 启动日志泵与状态泵
root.after(120, pump_logs)
render_status(status_bus.snapshot())
root.after(250, pump_status)

root.protocol("WM_DELETE_WINDOW", on_close)

# 窗口尺寸按 DPI 缩放: 启用 DPI 感知后 Tk 的像素随缩放变化,
# 写死的 820x760 在 150% 屏上会小于控件所需尺寸, 内容被挤掉。
root.update_idletasks()
_scale = max(1.0, root.winfo_fpixels("1i") / 96.0)
_win_w = max(int(620 * _scale), root.winfo_reqwidth() + 10)
_win_h = max(int(640 * _scale), root.winfo_reqheight() + 10)
root.geometry(f"{_win_w}x{_win_h}+80+30")
root.minsize(int(560 * _scale), int(520 * _scale))

# 开机自启动(--mini): 主窗口全程不出现, 直接给一个工作区左下角的小窗
if _MINI_ON_START:
    root.after(80, enter_mini_mode)

# 开机自启动(--boot): 界面就绪后直接开跑, 不用等人来点「开始运行」。
# 依赖还在自动安装时先等着, 每 2 秒看一次, 最多等 10 分钟(首次装 playwright 会比较久)。
def _boot_auto_start(attempt=0):
    # 开机自启动 = 小窗 + 自动开刷, 这里不再单独判断开关
    if not _BOOT_ON_START:
        return
    if _deps_state["busy"]:
        status_bus.set_state(STATE_PREPARING, TXT.STATE_DEPS_WAIT)
        if attempt < 300:
            root.after(2000, lambda: _boot_auto_start(attempt + 1))
        return
    log_text.insert(tk.END, TXT.LOG_BOOT_AUTORUN + "\n")
    log_text.see(tk.END)
    start_run(interactive=False)


if _BOOT_ON_START:
    root.after(1200, _boot_auto_start)

root.mainloop()
