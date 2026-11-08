# encoding=utf-8
import argparse
import asyncio
import ctypes
import os
import platform
import sys
import threading
import time

from playwright.async_api import BrowserContext, Page, Playwright, TimeoutError, async_playwright
from playwright._impl._errors import TargetClosedError

from modules import installer, updater
from modules.banner import print_banner
from modules.configs import Config, ConfigError
from modules.course_runner import (
    CourseOutcome,
    detect_catalog_after_verification,
    run_course,
)
from modules.diagnostics import check_browser, check_course
from modules.login import (
    LOGIN_PASSWORD_SELECTOR,
    LOGIN_SUBMIT_SELECTOR,
    LOGIN_USERNAME_SELECTOR,
    accept_login_terms,
    is_login_page,
    wait_for_login_complete,
)
from modules import anti_idle, window_guard
from modules.logger import Logger
from modules.slider import slider_verify
from modules.support import show_donate
from modules.tasks import (
    play_video,
    skip_questions,
    task_monitor,
    video_optimize,
    wait_for_verify,
)
from modules.utils import (
    clear_cookies,
    get_runtime_path,
    hide_window,
    load_cookies,
    optimize_page,
    register_window,
    save_cookies,
)
from modules.status import (
    STATE_ERROR,
    STATE_FINISHED,
    STATE_PREPARING,
    STATE_RUNNING,
    STATE_STOPPING,
    status_bus,
)
from modules.version import __version__

# 界面/小窗上显示的状态播报文案都在 ui_text.py 里改, 日志文案不在此列
import ui_text as TXT

# 模块级日志器: CLI 在 cli() 里创建, 界面在启动前赋值。
logger = None


def _ensure_logger():
    """返回可用的日志器: 界面忘记注入时也能自己兜住。"""
    global logger
    if logger is None:
        logger = Logger()
    return logger


# === 协作式停止 ===
# 界面"停止"按钮使用: 置位 STOP_REQUESTED 并取消顶层任务。
# supervise() 会在每次尝试前/退避后检查该标志, 保证尽快退出而不是强杀进程。
STOP_REQUESTED = threading.Event()
_running_loop = None
_running_task = None
_stop_lock = threading.Lock()
_stop_stage = 0


def _is_loop_teardown(exc) -> bool:
    """判断异常是否只是事件循环收尾/被中断, 而不是真正的运行失败。"""
    text = str(exc or "")
    return (
        "Event loop is closed" in text
        or "Event loop stopped before Future completed" in text
    )


def request_stop():
    """请求停止当前运行。

    第一次点"停止"走协作式取消: 取消顶层的 supervise 任务, 让 main() 的
    finally 在**仍然存活**的事件循环上正常收尾(取消后台任务、关闭浏览器)。
    直接 loop.stop() 会让 run_until_complete 带着半空中的协程栈返回,
    紧接着 loop.close(), 挂起的 main() 直到被 GC 才执行 finally,
    于是抛出一串 "Event loop is closed"(还夹着 GeneratorExit)。
    再点一次则作为兜底, 直接停掉事件循环。
    """
    global _stop_stage
    with _stop_lock:
        _stop_stage += 1
        stage = _stop_stage
    STOP_REQUESTED.set()
    try:
        status_bus.set_state(STATE_STOPPING, TXT.PHASE_STOPPING)
    except Exception:
        pass
    loop = _running_loop
    task = _running_task
    if loop is None or loop.is_closed():
        return
    try:
        if stage == 1 and task is not None:
            loop.call_soon_threadsafe(task.cancel)
        elif stage > 1:
            loop.call_soon_threadsafe(loop.stop)
    except Exception:
        pass


def reset_stop():
    """清除停止请求, 供下一次运行前调用。"""
    global _stop_stage
    STOP_REQUESTED.clear()
    with _stop_lock:
        _stop_stage = 0


# 获取全局事件循环
event_loop_verify = asyncio.Event()
event_loop_answer = asyncio.Event()
COOKIE_PATH = get_runtime_path("data", "cookies.json")
ZHS_COOKIE_URLS = [
    "https://www.zhihuishu.com",
    "https://passport.zhihuishu.com",
    "https://onlineweb.zhihuishu.com",
    "https://studyvideoh5.zhihuishu.com",
    "https://studywisdomh5.zhihuishu.com",
    "https://fusioncourseh5.zhihuishu.com",
    "https://hike.zhihuishu.com",
]


_saved_cookies = None


def _cookies_signature(cookies):
    if not cookies:
        return None
    return frozenset(
        (
            cookie.get("name"),
            cookie.get("domain"),
            cookie.get("path"),
            cookie.get("value"),
        )
        for cookie in cookies
    )


def remember_login_cookies(cookies) -> None:
    """记录已经落盘的凭证, 用于跳过重复写入。"""
    global _saved_cookies
    _saved_cookies = _cookies_signature(cookies)


async def persist_login_cookies(context: BrowserContext) -> None:
    """凭证有变化时才写盘: 登录完成、Cookie 续期后立即保存, 中断也不丢。"""
    global _saved_cookies
    cookies = await context.cookies(ZHS_COOKIE_URLS)
    signature = _cookies_signature(cookies)
    if signature is None or signature == _saved_cookies:
        return
    try:
        save_cookies(cookies, COOKIE_PATH)
    except Exception as exc:
        logger.log_exception("保存登录 Cookies 失败.", exc)
        return
    _saved_cookies = signature
    logger.event("保存登录凭证", 条数=len(cookies), 文件=COOKIE_PATH)


def get_screen_size():
    if os.name == "nt":
        user32 = ctypes.windll.user32
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    return 1920, 1080


async def init_page(p: Playwright, config, cookies) -> tuple[Page, BrowserContext]:
    driver = "msedge" if config.driver == "edge" else config.driver
    logger.info(f"正在启动{config.driver}浏览器...")
    screen_width, screen_height = get_screen_size()
    logger.event(
        "启动浏览器",
        驱动=config.driver,
        通道=driver,
        可执行文件=config.exe_path or "默认",
        窗口大小=f"{screen_width}x{screen_height}",
    )
    launch_args = {
        "channel": driver,
        "headless": False,
        "executable_path": config.exe_path if config.exe_path else None,
        "args": [
            "--start-maximized",
            f"--window-size={screen_width},{screen_height}",
            "--window-position=0,0",
            # 窗口不可见(最小化/移出屏幕)时, 不要让 Chromium 把它当后台窗口
            # 降频: 否则定时器被节流、渲染暂停, 播放进度会卡住。
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-features=CalculateNativeWinOcclusion,IntensiveWakeUpThrottling",
            "--autoplay-policy=no-user-gesture-required",
        ],
    }
    try:
        browser = await p.chromium.launch(**launch_args)
    except TargetClosedError as exc:
        logger.log_exception("首次启动浏览器失败,准备重试.", exc)
        logger.info("检测到浏览器首次启动失败,正在重试...")
        await asyncio.sleep(1)
        browser = await p.chromium.launch(**launch_args)
    logger.event("浏览器已启动", 版本=getattr(browser, "version", "未知"))
    # 使用真实窗口尺寸，避免 Playwright 默认 viewport 覆盖最大化窗口。
    context = await browser.new_context(viewport=None)
    if cookies:
        await context.add_cookies(cookies)
        logger.info("已加载 Cookies!")
    else:
        logger.info("未找到 Cookies,将跳转至登录页.")
    page = await context.new_page()
    logger.debug(f"{config.driver}浏览器启动完成.")
    # 抹去特征
    with open(get_runtime_path("resources", "stealth.min.js"), 'r') as f:
        js = f.read()
    await page.add_init_script(js)
    logger.debug("stealth.js执行完成.")
    # 防挂机: 钉住 visibilityState / hasFocus, 屏蔽 blur 等失活事件
    await anti_idle.install(page, logger)
    logger.debug("防挂机脚本注入完成.")
    page.set_default_timeout(24 * 3600 * 1000)

    return page, context


async def auto_login(context: BrowserContext, page: Page, config, modules=None) -> None:
    wait_start = time.time()
    await page.goto(config.login_url, wait_until="commit")
    if not is_login_page(page.url):
        logger.info("检测到已登录,跳过登录步骤.")
        return

    if config.username and config.password:
        try:
            username = await page.wait_for_selector(
                LOGIN_USERNAME_SELECTOR, state="visible", timeout=30000
            )
            password = await page.wait_for_selector(
                LOGIN_PASSWORD_SELECTOR, state="visible", timeout=30000
            )
            logger.event("自动登录", 方式="账号密码", 账号="已填写")
            await username.fill(config.username)
            await password.fill(config.password)
            await accept_login_terms(page)
            submit = await page.wait_for_selector(
                LOGIN_SUBMIT_SELECTOR, state="visible", timeout=30000
            )
            await page.wait_for_timeout(500)
            await submit.click()
        except TimeoutError:
            if is_login_page(page.url):
                logger.warn("未找到自动登录控件,请在浏览器中手动完成登录.", shift=True)
                logger.event("自动登录", 方式="手动", 原因="未找到登录控件")

    captcha_task = None
    if config.enableAutoCaptcha and modules:
        logger.event("滑块任务", 状态="启动")
        captcha_task = asyncio.create_task(slider_verify(page, modules))

    try:
        await wait_for_login_complete(page)
    finally:
        if captcha_task:
            if not captcha_task.done():
                captcha_task.cancel()
            await asyncio.gather(captcha_task, return_exceptions=True)

    logger.event("登录完成", 耗时=f"{time.time() - wait_start:.1f}s", 地址=page.url)
    await persist_login_cookies(context)
    logger.info(f"已保存登录凭证到: {COOKIE_PATH},下次可免密登录.")


async def ensure_login(
    context: BrowserContext, page: Page, cookies, config, modules=None
) -> bool:
    if cookies:
        logger.info("正在校验 Cookies 登录状态...")
        await page.goto(config.login_url, wait_until="domcontentloaded")
        try:
            await wait_for_login_complete(page, timeout=10000)
        except TimeoutError:
            pass
        if not is_login_page(page.url):
            logger.info("使用Cookies登录成功!")
            logger.event("登录状态", 结果="Cookies 有效", 地址=page.url)
            await persist_login_cookies(context)
            return True
        logger.warn("检测到 Cookies 已失效, 将重新登录.", shift=True)
        logger.event("登录状态", 结果="Cookies 失效", 地址=page.url)
        clear_cookies(COOKIE_PATH)
        remember_login_cookies(None)
        cookies = None

    if not config.username or not config.password:
        logger.info("请手动填写账号密码...")
    logger.info("正在等待登录完成...")
    await auto_login(context, page, config, modules)
    logger.info("登录成功!")
    return False


async def main(config) -> bool:
    modules, tasks = [], []
    playback_enabled = asyncio.Event()
    all_courses_complete = True
    run_ok = True
    # 状态总线: 把日志摘要(依赖安装/出错原因等)与进度同步给界面/小窗
    status_bus.attach_logger(logger)
    status_bus.begin(course_total=len(config.course_urls), message=TXT.PHASE_STARTING)
    if config.enableAutoCaptcha:
        logger.section("依赖安装")
        status_bus.set_state(STATE_PREPARING, TXT.PHASE_CHECK_DEPS)
        logger.info("正在检查依赖库...")
        modules = installer.start(config)
        logger.info("所有依赖库安装完成!")

    logger.section("登录")
    status_bus.set_state(STATE_PREPARING, TXT.PHASE_LOGIN)
    async with async_playwright() as p:
        cookies = load_cookies(COOKIE_PATH)
        remember_login_cookies(cookies)
        logger.event("本地凭证", 数量=len(cookies) if cookies else 0, 文件=COOKIE_PATH)
        page, context = await init_page(p, config, cookies)
        monitor_task = None
        window_task = None
        try:
            login_by_cookie = await ensure_login(context, page, cookies, config, modules)

            logger.context(登录方式="Cookie" if login_by_cookie else "账号")
            tasks.extend(
                [
                    asyncio.create_task(
                        wait_for_verify(page, config, event_loop_verify)
                    ),
                    asyncio.create_task(video_optimize(page, config)),
                    asyncio.create_task(skip_questions(page, event_loop_answer)),
                    asyncio.create_task(play_video(page, playback_enabled)),
                ]
            )
            logger.event(
                "后台任务启动",
                任务=", ".join(task.get_coro().__name__ for task in tasks),
            )
            if config.enableHideWindow:
                await hide_window(page)
            else:
                # 未开启隐藏也登记窗口: 用户手动最小化时看门狗可以救回来
                await register_window(page)
            monitor_task = asyncio.create_task(task_monitor(tasks))
            # 窗口保活: 被最小化/隐藏时自动恢复, 避免平台判定"用户离开"而暂停
            window_task = asyncio.create_task(window_guard.watchdog(logger, lambda: True))

            course_total = len(config.course_urls)
            status_bus.set_course(total=course_total)
            for index, course_url in enumerate(config.course_urls, 1):
                logger.section(f"课程 {index}/{course_total}")
                status_bus.set_state(STATE_RUNNING)
                status_bus.set_course(index=index, total=course_total)
                logger.context(课程序号=f"{index}/{course_total}", 课程地址=course_url)
                logger.info("正在加载播放页...")
                await page.goto(course_url, wait_until="commit")
                await page.wait_for_timeout(1500)
                if "login" in page.url:
                    logger.warn(
                        "播放页跳转到登录页, 当前登录状态已失效, 正在重新登录.",
                        shift=True,
                    )
                    logger.event("登录失效", 地址=page.url)
                    clear_cookies(COOKIE_PATH)
                    remember_login_cookies(None)
                    await ensure_login(context, page, None, config, modules)
                    logger.info("重新进入播放页...")
                    await page.goto(course_url, wait_until="commit")
                    await page.wait_for_timeout(1500)

                catalog = await detect_catalog_after_verification(page, page.url)
                logger.info(f"检测到 {catalog.name} 课程目录.")
                logger.context(目录类型=catalog.name)
                logger.event("课程目录", 类型=catalog.name, 地址=page.url)
                await persist_login_cookies(context)
                await optimize_page(page, config, catalog)
                logger.info("页面优化完成!")
                if catalog.course_title:
                    title_element = page.locator(catalog.course_title).first
                    if await title_element.count():
                        title = " ".join(
                            (await title_element.text_content() or "").split()
                        )
                        if title:
                            logger.info(f"当前课程:<<{title}>>")
                            status_bus.set_course(title=title)
                            logger.context(课程名称=title)
                            logger.event("当前课程", 名称=title)

                playback_enabled.clear()
                outcome = await run_course(
                    page, catalog, config, logger, playback_enabled
                )
                playback_enabled.clear()
                logger.event("课程结果", 结果=outcome.value, 目录类型=catalog.name)
                if outcome is CourseOutcome.FAILED:
                    logger.warn("课程未确认完成,已停止本轮运行.", shift=True)
                    run_ok = False
                    break
                if outcome is CourseOutcome.TIME_LIMIT:
                    all_courses_complete = False
                if outcome is CourseOutcome.COMPLETED and index < course_total:
                    logger.info(
                        f"课程 {index}/{course_total} 已处理完毕, 即将进入下一门!", shift=True
                    )
                    logger.event("课程切换", 下一门=f"{index + 1}/{course_total}")
                    status_bus.set_state(
                        STATE_PREPARING,
                        TXT.fill(
                            TXT.PHASE_NEXT_COURSE,
                            index=index + 1,
                            total=course_total,
                        ),
                    )
        finally:
            for task in tasks:
                task.cancel()
            if monitor_task:
                monitor_task.cancel()
            if window_task:
                window_task.cancel()
            # 收尾阶段任何一步失败都不能盖掉真正的结束原因(尤其是用户停止)
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
                if monitor_task:
                    await asyncio.gather(monitor_task, return_exceptions=True)
                if window_task:
                    await asyncio.gather(window_task, return_exceptions=True)
            except (asyncio.CancelledError, RuntimeError) as exc:
                logger.warn(f"后台任务收尾未完成: {exc}")
            try:
                await persist_login_cookies(context)
            except Exception as exc:
                logger.log_exception("刷新登录 Cookies 失败.", exc)
            try:
                await context.browser.close()
            except TargetClosedError:
                pass
            except Exception as exc:
                logger.log_exception("关闭浏览器失败.", exc)

    logger.section("任务结束")
    logger.event(
        "运行结果",
        正常退出=run_ok,
        全部完成=all_courses_complete,
    )
    logger.clear_context()
    if not run_ok:
        logger.warn("本轮因课时进度未确认而停止.", shift=True)
        snapshot = status_bus.snapshot()
        status_bus.set_state(
            STATE_ERROR, snapshot.get("error") or TXT.PHASE_LESSON_FAILED
        )
        return False
    if all_courses_complete:
        logger.info("所有课程已学习完毕!")
        status_bus.set_percent(100)
        status_bus.set_state(STATE_FINISHED, TXT.PHASE_ALL_DONE)
    else:
        logger.info("本轮已按每门课程时限结束,仍有课程未完成.", shift=True)
        status_bus.set_state(STATE_FINISHED, TXT.PHASE_TIME_LIMIT)
    show_donate(get_runtime_path("resources", "QRcode.jpg"), show=config.showDonateCode)
    return True


def _is_fatal(exc) -> bool:
    """重试也不会好转的错误: 直接停下, 不消耗重试次数。"""
    if isinstance(exc, ConfigError):
        return True
    return "不支持 Python" in str(exc or "")


async def _wait_backoff(seconds, next_attempt, total):
    """退避等待: 按 1 秒切片轮询停止标志, 点"停止"不必等满整个间隔。

    返回 False 表示等待期间收到了停止请求。
    """
    if seconds <= 0:
        return not STOP_REQUESTED.is_set()
    status_bus.set_state(
        STATE_PREPARING,
        TXT.fill(
            TXT.PHASE_RETRY_WAIT,
            seconds=seconds,
            round_no=next_attempt,
            total=total,
        ),
    )
    remaining = seconds
    while remaining > 0:
        if STOP_REQUESTED.is_set():
            return False
        step = 1 if remaining >= 1 else remaining
        try:
            await asyncio.sleep(step)
        except KeyboardInterrupt:
            return False
        remaining -= step
        if remaining > 0 and remaining % 5 == 0:
            status_bus.set_message(
                TXT.fill(
                    TXT.PHASE_RETRY_WAIT,
                    seconds=f"{remaining:.0f}",
                    round_no=next_attempt,
                    total=total,
                )
            )
    return True


async def supervise(config) -> int:
    """失败自动重启: 按 [restart].backoffSchedule 的阶梯表做有限次重试。

    返回码: 0 成功 / 1 失败(重试用尽或用户停止) / 2 不可重试的致命错误。

    每次尝试都会重新走 main(): 登录态 Cookie 已落盘, 重启可免密续上;
    已完成的课时由平台记录, run_course 只挑未完成的课时, 不会重头刷。
    任何阶段都会检查 STOP_REQUESTED(界面"停止"按钮), 触发后尽快退出。
    """
    _ensure_logger()
    if STOP_REQUESTED.is_set():
        logger.info("已请求停止,不启动运行.")
        return 1

    schedule = tuple(config.backoffSchedule) if config.enableRestart else ()
    total = len(schedule) + 1
    if total > 1:
        logger.section("失败重启已启用")
        logger.event(
            "重启策略",
            总尝试次数=total,
            等待序列=",".join(str(second) for second in schedule),
        )
    else:
        logger.event("重启策略", 自动重试="关闭")

    for attempt in range(1, total + 1):
        if STOP_REQUESTED.is_set():
            logger.info("已请求停止,结束重试.", shift=True)
            return 1
        if total > 1:
            logger.section(f"运行尝试 {attempt}/{total}")
            status_bus.set_state(
                STATE_PREPARING,
                TXT.fill(TXT.PHASE_ATTEMPT, attempt=attempt, total=total),
            )
        try:
            ok = await main(config)
        except asyncio.CancelledError:
            # 界面点了"停止": 交给工作线程收尾, 不当作失败
            raise
        except KeyboardInterrupt:
            logger.info("用户中断,停止运行.")
            return 1
        except SystemExit as exc:
            # installer.start() 装不上依赖时会 sys.exit(-1), 重试没有意义
            logger.error(f"依赖安装未完成(退出码 {exc.code}),不再重试.", shift=True)
            status_bus.set_state(
                STATE_ERROR, TXT.fill(TXT.PHASE_DEPS_FAILED, code=exc.code)
            )
            return 2
        except Exception as exc:
            # 点了"停止"或事件循环正在收尾时抛出的异常, 不是运行失败:
            # 既不该记 traceback(看起来像崩溃), 也不该触发下一次重试。
            if STOP_REQUESTED.is_set() or _is_loop_teardown(exc):
                logger.info("已停止运行(用户中断)。", shift=True)
                return 1
            if _is_fatal(exc):
                logger.log_exception("遇到不可重试的错误,停止运行.", exc, shift=True)
                status_bus.set_state(STATE_ERROR, f"{exc}")
                return 2
            logger.log_exception(
                f"第 {attempt} 次运行抛出异常,准备重试.", exc, shift=True
            )
            # 用 WARN 而不是 ERROR: 这次失败还会重试, 状态灯不该直接变红
            status_bus.set_message(
                TXT.fill(TXT.PHASE_RUN_ERROR, attempt=attempt, error=exc),
                level="WARN",
            )
            ok = False
        else:
            if ok:
                logger.event("运行结果", 结果="成功", 尝试次数=attempt)
                return 0
            logger.warn(
                f"第 {attempt} 次运行未确认完成(可能课时失败),准备重试.",
                shift=True,
            )

        if attempt >= total:
            logger.error(f"已达最大尝试次数 {total},停止运行.", shift=True)
            snapshot = status_bus.snapshot()
            status_bus.set_state(
                STATE_ERROR,
                snapshot.get("error")
                or snapshot.get("message")
                or TXT.fill(TXT.PHASE_RETRY_EXHAUSTED, total=total),
            )
            return 1

        delay = schedule[attempt - 1]
        logger.info(
            f"将在 {delay}s 后重试(第 {attempt + 1}/{total} 次尝试)...", shift=True
        )
        logger.event("重试退避", 等待秒=delay, 下一次=attempt + 1)
        if not await _wait_backoff(delay, attempt + 1, total):
            logger.info("已请求停止,结束重试.", shift=True)
            return 1

    return 1


def parse_args():
    parser = argparse.ArgumentParser(description="Autovisor")
    parser.add_argument("--config", default=None, help="配置文件路径(默认使用程序目录下的 config.ini)")
    parser.add_argument(
        "--check-browser",
        action="store_true",
        help="只检查 Chrome 启动和智慧树登录状态",
    )
    parser.add_argument(
        "--check-course",
        metavar="URL",
        help="阻止进度上报和自动播放,只检查课程目录选择器",
    )
    parser.add_argument(
        "--import-cookies",
        metavar="PATH",
        help="从 Requests CookieJar JSON 安全导入未过期的智慧树 Cookie",
    )
    return parser.parse_args()


def cli() -> int:
    global logger
    args = parse_args()
    print_banner()
    logger = Logger()
    exit_code = 0
    try:
        logger.section("初始化")
        logger.info("程序启动中...")
        logger.event(
            "运行环境",
            版本=__version__,
            Python=platform.python_version(),
            运行方式="打包" if getattr(sys, "frozen", False) else "源码",
            系统=platform.system(),
            系统版本=platform.release(),
            系统架构=platform.machine(),
            启动参数=(f"[{' '.join(sys.argv[1:])}]" if sys.argv[1:] else "无"),
            日志文件=logger.filename,
        )
        updater.check_for_update(logger)
        installer.validate_python_version()
        base_dir = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
        config_path = args.config or os.path.join(base_dir, "config.ini")
        mirrors_path = os.path.join(base_dir, "data", "mirrors.json")
        config = Config(config_path, mirrors_path)
        logger.context(配置文件=config_path)
        logger.event(
            "运行配置",
            驱动=config.driver,
            浏览器路径=config.exe_path or "默认",
            连接现有浏览器=config.attach_existing_chrome,
            账号="已填写" if config.username else "未填写",
            密码="已填写" if config.password else "未填写",
            自动答题=config.enableAutoCaptcha,
            隐藏窗口=config.enableHideWindow,
            静音=config.soundOff,
            倍速=config.limitSpeed,
            时限分钟=config.limitMaxTime,
            课程数=len(config.course_urls),
            镜像源=len(config.mirrors),
        )
        for index, course_url in enumerate(config.course_urls, 1):
            logger.event("课程地址", 序号=f"{index}/{len(config.course_urls)}", 地址=course_url)
        if args.import_cookies:
            from modules.utils import import_zhihuishu_cookies

            count = import_zhihuishu_cookies(args.import_cookies, COOKIE_PATH)
            logger.info(f"已安全导入 {count} 条智慧树 Cookie.", shift=True)
            return 0
        if args.check_browser:
            return asyncio.run(check_browser(config, logger, COOKIE_PATH))
        if args.check_course:
            return asyncio.run(
                check_course(args.check_course, config, logger, COOKIE_PATH)
            )
        if not config.course_urls:
            logger.error("未检测到有效网址或不支持此类网页,请检查配置文件!")
            return 2
        exit_code = asyncio.run(supervise(config))
    except TargetClosedError as exc:
        if "BrowserType.launch" in repr(exc):
            logger.log_exception("浏览器相关流程异常结束.", exc)
            logger.error("浏览器启动失败,请检查 Chrome 或 CDP 配置!")
        else:
            logger.debug(f"浏览器关闭结束运行: {logger.summarize_exception(exc)}")
        exit_code = 1
    except ConfigError as exc:
        logger.error(f"配置文件无效: {exc}", shift=True)
        logger.info("请完整解压发行包，并确保 config.ini 与 Autovisor.exe 位于同一目录。")
        exit_code = 1
    except Exception as exc:
        logger.log_exception("程序运行时出现未处理异常.", exc, shift=True)
        if isinstance(exc, KeyError):
            logger.error(f"配置文件错误!")
        elif isinstance(exc, FileNotFoundError):
            logger.error(f"依赖文件缺失: {exc.filename},请重新安装程序!")
        elif isinstance(exc, UnicodeDecodeError):
            logger.error("配置文件编码错误,保存时请选择UTF-8或GBK编码!")
        else:
            logger.error("系统出错,请检查后重新启动!")
        exit_code = 1
    finally:
        logger.save()
        if getattr(sys, "frozen", False) and sys.stdin.isatty():
            try:
                input("程序已结束,按Enter退出...")
            except EOFError:
                # 非交互式运行(重定向/自动化)时 stdin 可能已关闭, 不应视作异常
                pass
    return exit_code


if __name__ == "__main__":
    sys.exit(cli())