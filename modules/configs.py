# encoding=utf-8
import configparser
import json
import os
import re
import sys


class ConfigError(Exception):
    pass


class Config:
    def __init__(self, config_path=None, mirrors_path=None):
        self.config_path = config_path
        if config_path:
            self._config = configparser.ConfigParser()
            # 用户常量
            self._read_config()
            self.driver = self.get_driver()
            # 账号密码允许缺失(留空即手动登录), 统一用 fallback 读取,
            # 避免 config.ini 由界面首次生成时缺项直接抛 NoOptionError。
            self.username = self._config.get('user-account', 'username', raw=True, fallback='')
            self.password = self._config.get('user-account', 'password', raw=True, fallback='')
            # 浏览器选项
            self.exe_path = self._config.get('browser-option', 'EXE_PATH', raw=True, fallback='')
            self.attach_existing_chrome = self.get_bool_field('browser-option', 'attachExistingChrome', fallback=False)
            self.cdp_url = self._config.get('browser-option', 'cdpUrl', raw=True, fallback='http://127.0.0.1:9222').strip()
            # 脚本选项
            self.enableAutoCaptcha = self.get_bool_field('script-option', 'enableAutoCaptcha')
            self.enableHideWindow = self.get_bool_field('script-option', 'enableHideWindow')
            self.showDonateCode = self.get_bool_field("script-option", "showDonateCode")
            # 课程选项
            self.soundOff = self.get_bool_field('course-option', 'soundOff')
            self.course_match_rule = re.compile("https://[-A-Za-z0-9+&@#/%?=~_|!:,.;]+[-A-Za-z0-9+&@#/%=~_|]")
            self.course_urls = self.get_course_urls()
        # 登录
        self.login_url = "https://passport.zhihuishu.com/login"
        self.block_js = '''return document.getElementsByClassName("yidun_jigsaw")[0].src'''
        self.bg_js = '''return document.getElementsByClassName("yidun_bg-img")[0].src'''
        # 弹窗
        self.pop_js = '''document.getElementsByClassName("iconfont iconguanbi")[0].click();'''

        self.mirrors = self._read_mirrors(mirrors_path)

    def _read_config(self) -> None:
        if not os.path.isfile(self.config_path):
            raise ConfigError(f"未找到配置文件: {self.config_path}")
        try:
            self._config.read(self.config_path, encoding='utf-8-sig')
        except UnicodeDecodeError:
            self._config.read(self.config_path, encoding='gbk')
        required_sections = {
            "user-account", "browser-option", "script-option", "course-option", "course-url"
        }
        missing_sections = required_sections - set(self._config.sections())
        if missing_sections:
            raise ConfigError(
                f"配置文件缺少必要配置段: {', '.join(sorted(missing_sections))}"
            )

    def _read_mirrors(self, mirrors_path=None) -> dict:
        if not mirrors_path:
            if self.config_path:
                base_dir = os.path.dirname(self.config_path)
            elif getattr(sys, "frozen", False):
                base_dir = os.path.dirname(sys.executable)
            else:
                base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            mirrors_path = os.path.join(base_dir, "data", "mirrors.json")
        if not os.path.isfile(mirrors_path):
            raise ConfigError(f"未找到镜像配置文件: {mirrors_path}")
        try:
            with open(mirrors_path, "r", encoding="utf-8-sig") as file:
                mirrors = json.load(file)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ConfigError(f"镜像配置文件无效: {mirrors_path}") from error
        if not isinstance(mirrors, dict):
            raise ConfigError("镜像配置文件必须是 JSON 对象")
        mirrors = {str(name): str(url).strip() for name, url in mirrors.items() if str(url).strip()}
        if not mirrors:
            raise ConfigError("镜像配置文件至少需要配置一个镜像源")
        return mirrors

    def get_driver(self) -> str:
        driver = self._config.get('browser-option', 'driver', raw=True, fallback='')
        if not driver:
            driver = "edge"
        return driver.lower()

    def get_bool_field(self, section: str, option: str, fallback: bool = False) -> bool:
        field = self._config.get(section, option, raw=True, fallback=str(fallback)).strip().lower()
        if field == "true":
            return True
        else:
            return False

    def get_course_urls(self) -> list:
        course_urls = []
        _options = self._config.options("course-url")
        for _option in _options:
            course_url = self._config.get("course-url", _option, raw=True)
            matched = re.findall(self.course_match_rule, course_url)
            if not matched:
                print(f"\"{course_url.strip()}\"\n不是一个有效网址,将忽略该网址.")
                continue
            course_urls.append(course_url)
        return course_urls

    # @property修饰器可设置属性
    # 这样写可实时响应配置变化
    def _safe_get_float(self, section: str, option: str, default: float = 0.0) -> float:
        try:
            value = self._config.get(section, option, raw=True, fallback='').strip()
            if not value:
                return default
            return float(value)
        except (ValueError, configparser.Error):
            return default

    @property
    def limitMaxTime(self) -> float:
        self._read_config()
        return self._safe_get_float('course-option', 'limitMaxTime', 0.0)

    @property
    def limitSpeed(self) -> float:
        self._read_config()
        speed = self._safe_get_float('course-option', 'limitSpeed', 1.0)
        return min(max(speed, 0.5), 1.8)

    @property
    def skipCompletedCourse(self) -> bool:
        """整门课都学完时, 是否直接跳到下一个课程链接(默认 True)。

        关闭后维持原有行为: 进入"复习"模式把这门课从头重播一遍,
        直到 limitMaxTime 到点才切换 —— 多链接时会被误当成"卡住不动"。
        """
        self._read_config()
        raw = self._safe_get_str('course-option', 'skipCompletedCourse', 'True')
        return raw.strip().lower() not in ('false', '0', 'no', 'off')

    # ---------- 失败重启([restart] 段, 全部可选) ----------
    # 每次重试前的等待秒数: 表长度 = 重试次数, 总尝试次数 = len + 1。
    DEFAULT_BACKOFF_SCHEDULE = (10, 10, 10, 20, 20, 30)

    # 单次等待的上下限, 防止手写配置把程序卡在几小时的等待里
    MIN_BACKOFF_SECONDS = 0
    MAX_BACKOFF_SECONDS = 3600

    def _has_option(self, section: str, option: str) -> bool:
        """区段不存在时返回 False(部分 Python 版本 has_option 会抛 NoSectionError)。"""
        try:
            return self._config.has_option(section, option)
        except configparser.Error:
            return False

    def _safe_get_str(self, section: str, option: str, default: str = "") -> str:
        """读取可选配置项; 区段或选项缺失时返回 default, 不抛异常。"""
        if not self._config.has_section(section):
            return default
        if not self._config.has_option(section, option):
            return default
        try:
            return self._config.get(section, option, raw=True).strip()
        except configparser.Error:
            return default

    def _parse_schedule(self, raw: str) -> tuple:
        """把 "10,10,10,20,20,30" 解析成元组; 中文逗号/分号/空格都当分隔符。"""
        values = []
        for token in re.split(r"[,，;；\s]+", raw or ""):
            if not token:
                continue
            try:
                seconds = int(float(token))
            except (TypeError, ValueError):
                continue
            values.append(
                min(max(seconds, self.MIN_BACKOFF_SECONDS), self.MAX_BACKOFF_SECONDS)
            )
        return tuple(values)

    @property
    def enableRestart(self) -> bool:
        """是否启用失败自动重启(默认开启, 显式写 False 才关闭)。"""
        return self._safe_get_str('restart', 'enableRestart', 'True').lower() == 'true'

    @property
    def backoffSchedule(self) -> tuple:
        """重试前的等待序列(秒)。

        - 未配置 [restart] / backoffSchedule: 用默认阶梯 10,10,10,20,20,30
        - 显式留空("backoffSchedule =") : 返回空元组, 表示失败即停不重试
        - 只写一个值: 该值作固定间隔
        - 兼容旧写法 maxRetries + backoffSec: 按固定间隔展开
        """
        if self._has_option('restart', 'backoffSchedule'):
            return self._parse_schedule(
                self._safe_get_str('restart', 'backoffSchedule', '')
            )
        legacy_interval = self._safe_get_str('restart', 'backoffSec', '')
        if legacy_interval:
            parsed = self._parse_schedule(legacy_interval)
            if not parsed:
                return ()
            try:
                retries = max(0, int(float(self._safe_get_str('restart', 'maxRetries', '5'))))
            except (TypeError, ValueError):
                retries = 5
            return parsed * retries if retries else ()
        return self.DEFAULT_BACKOFF_SCHEDULE

    @property
    def maxAttempts(self) -> int:
        """总尝试次数 = 首轮 + 每次重试。"""
        return len(self.backoffSchedule) + 1
