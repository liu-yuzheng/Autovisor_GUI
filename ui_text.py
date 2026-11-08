# encoding=utf-8
"""Autovisor 图形界面显示的全部文字。

想改界面上看到的字，只需要改这个文件，保存后重启程序即可生效，
不用去动 GUI.py 或 modules/ 里的代码。

两点注意:
1. 带 {花括号} 的是占位符，运行时会替换成具体内容(账号、分钟数、错误原因等)。
   可以移动它的位置、改它前后的文字，但花括号里的名字别改、别删。
   万一写错了，程序会把这条文字原样显示出来，不会崩。
2. 只改引号里面的内容。变量名、引号、逗号、括号的结构不要动。

文件按界面分区: 窗口与页签 → 运行页 → 配置页 → 小窗 → 弹窗 →
日志与状态提示 → 帮助页。
"""

# ============ 窗口与页签 ============
APP_TITLE = "智慧树刷课助手"
TAB_RUN = "  运行  "
TAB_CONFIG = "  配置  "
TAB_HELP = "  帮助  "

# ============ 运行页 ============
GROUP_STATUS = "运行状态"
GROUP_LOG = "日志"
BTN_START = "开始运行"
BTN_STOP = "停止"
BTN_HELP = "帮助"
BTN_MINI = "小窗"
BTN_CONFIG = "修改配置"

# 状态灯旁边那两个字
STATE_LABEL = {
    "idle": "空闲",
    "preparing": "准备中",
    "running": "正在刷课",
    "stopping": "正在停止",
    "finished": "已完成",
    "error": "出错",
}

# 刚开始运行、还没有任何状态时显示的占位文字
INIT_STATE = "空闲"
INIT_PERCENT = "总进度 0%"
INIT_BRIEF = "空闲，等待开始"

# 刷课中在状态后面挂上课程名
STATUS_WITH_COURSE = "{state} · {title}"
# 进度数字: 刷课时同时给出本课和总进度(中间是全角空格)
PERCENT_BOTH = "本课 {lesson}%　总进度 {total}%"
PERCENT_TOTAL = "总进度 {total}%"

# ============ 配置页 ============
GROUP_ACCOUNT = "账号"
LABEL_USERNAME = "手机号"
LABEL_PASSWORD = "密码"

GROUP_BROWSER = "浏览器"
LABEL_DRIVER = "驱动"
LABEL_BROWSER_PATH = "浏览器路径"
HINT_BROWSER_PATH = "留空使用默认安装位置"

GROUP_COURSE = "课程"
LABEL_COURSE_URLS = "课程链接（每行一个）"
LABEL_SPEED = "倍速"

GROUP_OPTIONS = "可选"
# 顺序与 GUI.py 里的开关一一对应，可以改文字，不能增删条目
OPTION_LABELS = (
    "自动跳过验证码",
    "运行时隐藏浏览器",
    "静音播放",
    "运行出错自动重试",
    "完成后显示赞赏码",
    "已学完的课程直接跳过",
)

GROUP_AUTOSTART = "开机自动启动"
CHECK_AUTOSTART = "开机后在左下角小窗自动开始刷课"
LABEL_DAILY_LIMIT_PREFIX = "每天刷满"
LABEL_DAILY_LIMIT_SUFFIX = "分钟后不再自启动（0 = 不限）"
LABEL_RUN_LIMIT_PREFIX = "每次运行"
LABEL_RUN_LIMIT_SUFFIX = "分钟（0 = 不限）"
BTN_RESET_DAILY = "清零"
BTN_BACK_TO_RUN = "返回运行 →"

# 出错重试那一行提示
RESTART_HINT = ""
RESTART_HINT_OFF = "已关闭自动重试"
SCHEDULE_SECONDS = "{parts} 秒"
SCHEDULE_NONE = "失败即停（不重试）"

# 今日学习时长: 分钟级上限用分钟, 大上限折成小时
DAILY_PROGRESS_MINUTES = "今日 {done} / {limit} 分钟"
DAILY_PROGRESS_HOURS = "今日 {done_h} 小时 {done_m} 分 / {limit_h} 小时"
DAILY_FULL_SUFFIX = "（已满）"
DAILY_NO_LIMIT = "今日已刷 {done}（未设上限）"

# 自动保存提示(配置页左下角)
SAVE_HINT = "已保存 {time}"
SAVE_FAILED = "自动保存失败: {error}"

# 运行页那行配置摘要
SUMMARY_ACCOUNT_EMPTY = "未填写账号"
SUMMARY_COURSE_COUNT = "{count} 个课程"
SUMMARY_NO_LIMIT = "不限时"
SUMMARY_MINUTES = "{minutes} 分钟"
SUMMARY_RETRY = "出错重试 {schedule}"
SUMMARY_SEPARATOR = " · "

# ============ 小窗 ============
MINI_TITLE_INIT = "空闲"
MINI_PERCENT_LESSON = "本课 {lesson}%"
MINI_PERCENT_TOTAL = "总进度 {total}%"
MINI_MENU_BACK = "返回主界面"
MINI_MENU_QUIT = "退出程序"

# ============ 弹窗 ============
MSG_BUSY_TITLE = "请稍候"
MSG_BUSY = "正在自动安装依赖, 装好后即可开始运行。"

MSG_RUNTIME_TITLE = "环境检查未通过"

MSG_SAVE_FAILED_TITLE = "保存失败"
MSG_SAVE_FAILED = "无法写入配置文件:\n{error}\n\n配置文件: {path}"

MSG_CONFIG_FAILED_TITLE = "配置错误"
MSG_CONFIG_FAILED = (
    "无法加载配置:\n{error}\n\n"
    "完整信息已写入 logs/gui_error.log\n"
    "配置文件: {path}"
)

MSG_NO_COURSE_TITLE = "缺少课程"
MSG_NO_COURSE = "请先在「课程链接」中填写至少一条课程地址。"

MSG_EXIT_TITLE = "退出"
MSG_EXIT_CONFIRM = "正在刷课, 确定要停止并退出吗?"

MSG_AUTOSTART_FAILED_TITLE = "开机自启动"
MSG_AUTOSTART_FAILED = (
    "修改开机自启动失败。\n\n"
    "请确认当前用户对注册表\n"
    "HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run\n"
    "有写入权限后重试。"
)

MSG_RESET_TITLE = "清零失败"
MSG_RESET_FAILED = "无法删除今天的学习记录，请检查用户目录（%LOCALAPPDATA%）的权限。"

MSG_MISSING_DEP_TITLE = "缺少依赖"
MSG_MISSING_DEP = (
    "当前 Python 环境缺少依赖: {names}\n\n"
    "正在使用的解释器:\n{python}\n\n"
    "请先安装后再启动:\n"
    '    "{python}" -m pip install {packages}\n'
    'playwright 还需要执行一次: "{python}" -m playwright install'
)

MSG_DEPS_FAILED_TITLE = "依赖安装失败"
MSG_DEPS_FAILED = (
    "自动安装未能补齐: {names}\n\n"
    "解释器: {python}\n\n"
    "请检查网络后重新打开本程序, 或手动执行:\n"
    '    "{python}" -m pip install {packages}'
)

MSG_PY_UNSUPPORTED = (
    "当前 Python {version} 不受支持, 仅支持 {names}。\n\n"
    "解释器: {python}\n"
    "请换用受支持的 Python 启动本程序。"
)
MSG_MISSING_REQUIRED = (
    "缺少必要依赖: {names}\n\n"
    "解释器: {python}\n\n"
    "请先安装后重试:\n"
    '    "{python}" -m pip install {packages}'
)

# ============ 日志与状态提示 ============
# 日志面板里那一行行的提示(前面带 [依赖]/[自启动] 之类的前缀)
LOG_STARTING = "正在启动..."
LOG_RUN_BEGIN = "=== 开始运行 ==="
LOG_RUN_END = "=== 运行结束 (exit={code}) ==="
LOG_STOP_MANUAL = "已手动停止"
LOG_RUN_DONE = "运行结束"
LOG_RUN_FAILED = "运行失败,请查看日志"
LOG_STOP_TASK_CANCELLED = "已停止运行（任务已取消）。"
LOG_STOP_LOOP = "已停止运行（事件循环被中断）。"
LOG_RUN_ERROR = "运行失败"
LOG_UNEXPECTED_EXIT = "运行被意外终止: {type}: {error}"
LOG_WATCHDOG = (
    "[看门狗] 已 {minutes} 分钟没有新的运行状态, 可能卡住了; 可点「停止」后重试"
)

LOG_CONFIG_CREATED = "[配置] 已生成 {path}"
LOG_CONFIG_PATCHED = "[配置] 已补齐: {keys}"

# 开机自启动那次被跳过时, 写进 logs/boot.log 的一行
LOG_BOOT_SKIPPED = "跳过自启动: 今天已累计刷 {done}, 达到上限 {limit} 分钟, 不再自动拉起"

# 开机自启动(无人值守)时不能弹窗, 这两类失败只写进日志
LOG_BOOT_SAVE_FAILED = "无法写入配置文件: {error}"
LOG_BOOT_CONFIG_FAILED = "无法加载配置: {error}"

LOG_AUTOSTART_ENABLED = "[自启动] 已加入开机启动: {target}"
LOG_AUTOSTART_DISABLED = "[自启动] 已取消开机启动"
LOG_AUTOSTART_RESET = "[自启动] 已清零今日累计时长，今天还可以再自启动"
LOG_BOOT_AUTORUN = "[自启动] 已按设置自动开始刷课"
LOG_BOOT_PREFIX = "[自动开始] "
LOG_BOOT_NO_COURSE = "[自动开始] 还没有课程链接, 已停在空闲状态"

LOG_DEPS_NO_PIP = "[依赖] 当前解释器没有可用的 pip, 尝试 ensurepip 补齐…"
LOG_DEPS_INDEX = "[依赖] 使用{label}安装: {packages}"
# 装依赖时依次尝试的源(日志里显示的名字)
PIP_INDEX_TSINGHUA = "清华镜像"
PIP_INDEX_ALIYUN = "阿里云镜像"
PIP_INDEX_OFFICIAL = "官方源"
LOG_DEPS_PIP_FAILED = "{label}安装失败(返回码 {code})"
LOG_DEPS_PYTHON = "[依赖] 解释器: {python}"
LOG_DEPS_FAILED_HEAD = "[依赖] 自动安装失败, 请检查网络后重试, 或手动执行:"
LOG_DEPS_MANUAL_CMD = '   "{python}" -m pip install {packages}'
LOG_DEPS_CHROMIUM_CMD = '   "{python}" -m playwright install chromium'
LOG_DEPS_EXC = "[依赖] 自动安装异常: {type}: {error}"
LOG_DEPS_OK = "[依赖] 安装完成, 可以开始运行"
LOG_DEPS_THEME = "[依赖] 主题库已装好, 重启界面后生效"
LOG_DEPS_LEFT = "[依赖] 仍缺少: {names}"
LOG_DEPS_MISSING_AUTO = "[依赖] 缺少 {names}, 正在自动安装…"
LOG_DEPS_MISSING_MANUAL = "[依赖] 缺少 {names}, 自动安装已关闭, 请手动执行"
LOG_DEPS_CHROMIUM = (
    "[依赖] 当前浏览器为 Chromium, 若启动报浏览器缺失, 请执行:"
)

# 状态栏/进度条上的一句话说明
STATE_DEPS_INSTALLING = "正在安装依赖: {names}"
STATE_DEPS_WAIT = "正在安装依赖, 装好后自动开始"
STATE_DEPS_READY = "依赖已就绪"
STATE_MISSING_DEPS = "缺少依赖: {names}"
STATE_RUNTIME_FAILED = "环境检查未通过"
STATE_NO_COURSE = "未填写有效的课程链接"
STATE_SAVE_FAILED = "无法写入配置: {error}"
STATE_CONFIG_FAILED = "无法加载配置: {error}"
STATE_RUN_FAILED = "运行失败: {error}"
STATE_UNKNOWN = "未知"

# ============ 运行过程中的状态播报 ============
# 这些字显示在运行页状态灯下面那一行, 以及开机自启时的小窗上。
PHASE_STOPPING = "正在停止,等待当前课时收尾..."
PHASE_STARTING = "正在启动..."
PHASE_CHECK_DEPS = "正在检查并安装依赖库..."
PHASE_LOGIN = "正在登录智慧树..."
PHASE_NEXT_COURSE = "准备进入课程 {index}/{total}"
PHASE_LESSON_FAILED = "课时进度未确认,本轮已停止"
PHASE_ALL_DONE = "所有课程已学习完毕"
PHASE_TIME_LIMIT = "已按课程时限结束,仍有课程未完成"
PHASE_RETRY_WAIT = "{seconds}s 后重试（第 {round_no}/{total} 次尝试）"
PHASE_ATTEMPT = "第 {attempt}/{total} 次尝试"
PHASE_DEPS_FAILED = "依赖安装失败(退出码 {code})"
PHASE_RUN_ERROR = "第 {attempt} 次运行出错: {error}"
PHASE_RETRY_EXHAUSTED = "已达最大尝试次数 {total},运行失败"

# 运行页/小窗那行简要说明的默认句(状态机内部用, 与上面的 STATE_LABEL 是两套)
STATE_BRIEF = {
    "idle": "空闲，等待开始",
    "preparing": "正在准备（检查依赖 / 登录 / 打开课程）",
    "running": "正在刷课",
    "stopping": "正在停止…",
    "finished": "已完成",
    "error": "运行出错",
}
BRIEF_COURSE = "课程 {index}/{total}"
BRIEF_LESSON = "课时 {index}/{total}"
BRIEF_PART_SEPARATOR = " · "
BRIEF_SEPARATOR = "｜"
BRIEF_ERROR = "出错：{reason}"
BRIEF_UNKNOWN = "未知错误"

# ============ 帮助页 ============
# 帮助页底部那行版本号与主页按钮
HELP_VERSION_LINE = "Autovisor {version} · MIT License"
BTN_HOMEPAGE = "打开项目主页"

# 帮助页最下方的赞赏码(图片在 resources/QRcode.jpg)
HELP_QR_TITLE = "赞赏支持"
HELP_QR_MISSING = "(赞赏码图片缺失或无法读取：resources/QRcode.jpg)"

# 许可全文(显示在帮助页最末, 等宽字体)
LICENSE_TEXT = (
    "Copyright (c) 2024 PengChengxu\n\n"
    "Permission is hereby granted, free of charge, to any person obtaining a copy\n"
    'of this software and associated documentation files (the "Software"), to deal\n'
    "in the Software without restriction, including without limitation the rights\n"
    "to use, copy, modify, merge, publish, distribute, sublicense, and/or sell\n"
    "copies of the Software, and to permit persons to whom the Software is\n"
    "furnished to do so, subject to the following conditions:\n\n"
    "The above copyright notice and this permission notice shall be included in all\n"
    "copies or substantial portions of the Software.\n\n"
    'THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR\n'
    "IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,\n"
    "FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE\n"
    "AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER\n"
    "LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,\n"
    "OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE\n"
    "SOFTWARE.\n"
)


def _bullets(lines):
    return [("item", "· " + line + "\n") for line in lines]


def help_sections(schedule_text, retry_count):
    """拼出帮助页: 返回 [(样式, 文本), ...]。

    样式只有 h1(大标题) / h2(小标题) / item(正文, 带圆点) / dim / mono(等宽)。
    schedule_text 是重试间隔(如 "10/10/10/20/20/30 秒"), retry_count 是重试次数。
    想加许可全文: 末尾追加 ("mono", LICENSE_TEXT)。
    """
    blocks = [
        ("h1", "使用说明\n"),
        (
            "dim",
            "仅供学习交流，请于 24 小时内删除；请勿用于不当刷课，账号异常概不负责。\n",
        ),
        ("h2", "配置\n"),
    ]
    blocks += _bullets(
        (
            "账号密码可留空，留空则启动后手动登录。",
            "课程链接从课程页复制，每行一条，可填多条；倍速上限 1.8。",
            "改动即时保存，没有「保存」按钮。",
            "隐藏浏览器是把窗口移到屏幕外，不是最小化，播放不会中断。",
            "开机自启 = 写启动项 + 开机显示左下角小窗 + 就绪后自动开始。",
            "「每天刷满 N 分钟」只算真正在刷课的时间，满额后开机静默跳过。",
            "「每次运行」是单次最长刷课时间。",
        )
    )

    blocks.append(("h2", "运行\n"))
    blocks += _bullets(
        (
            "「本课」是当前这一节的进度，「总进度」是整门课。",
            "运行中配置页锁定，结束或停止后自动解锁。",
            "停止会先取消当前课时再关浏览器，不留残留进程。",
            "重复启动只会唤出已有窗口，不会另开一份。",
        )
    )

    blocks.append(("h2", "小窗\n"))
    blocks += _bullets(
        (
            "显示进度与一行状态；单击返回主界面，右键可退出。",
            "默认停在工作区左下角，可拖动，位置本次运行内有效。",
        )
    )

    blocks.append(("h2", "失败自动重试\n"))
    if retry_count:
        blocks.append(
            (
                "item",
                "· 出错或课时未被平台确认时自动重试，间隔 "
                + str(schedule_text)
                + "，共 "
                + str(retry_count)
                + " 次。\n",
            )
        )
    else:
        blocks.append(("item", "· 当前未开启自动重试，出错即停止。\n"))
    blocks += _bullets(
        (
            "间隔可在 config.ini 的 [restart].backoffSchedule 调整，例如 10,20,60。",
            "重试只补没完成的课时，点「停止」可立即中断倒计时。",
            "缺依赖、配置无效、课程链接为空不会重试，直接给出原因。",
        )
    )

    blocks.append(("h2", "鸣谢\n"))
    blocks += _bullets(
        (
            "本程序的图形界面、失败自动重启、进度状态与小窗模块，是在 Autovisor 源码基础上做的本地改动。",
            "原项目：Autovisor（智慧树刷课脚本）",
            "作者：PengChengxu",
            "主页：https://github.com/CXrunfree/Autovisor",
            "许可：MIT License（以下为许可全文）",
        )
    )

    blocks.append(("h2", "\nMIT License\n"))
    blocks.append(("mono", LICENSE_TEXT))

    return blocks


# ============ 工具函数(不用改) ============
def fill(template, **values):
    """把 {占位符} 换成实际值; 万一占位符写错就原样返回, 不让界面崩掉。"""
    try:
        return template.format(**values)
    except (KeyError, IndexError, ValueError):
        return template
