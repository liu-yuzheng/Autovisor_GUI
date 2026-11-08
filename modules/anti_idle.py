"""页面级防挂机: 让播放器始终认为自己在前台。

窗口已被移到屏幕外(见 window_guard), 但平台仍可能通过
document.visibilityState / hasFocus() / blur 事件判断"用户离开了",
进而暂停视频或停止上报进度。这里在页面初始化阶段注入一段脚本,
统一把这些信号钉死成"可见 + 有焦点"。

注意: 只在浏览器层面伪装, 不模拟鼠标键盘, 避免 isTrusted=false 的
合成事件被平台识破。
"""

from playwright.async_api import Page

ANTI_IDLE_JS = """
(() => {
  if (window.__avAntiIdle) return;
  window.__avAntiIdle = true;

  const spoof = (proto, prop, value) => {
    try {
      Object.defineProperty(proto, prop, {
        configurable: true,
        get: () => value,
      });
    } catch (e) {}
  };

  // 1) 可见性: 永远 visible
  spoof(Document.prototype, 'hidden', false);
  spoof(Document.prototype, 'webkitHidden', false);
  spoof(Document.prototype, 'visibilityState', 'visible');
  spoof(Document.prototype, 'webkitVisibilityState', 'visible');

  // 2) 焦点: 永远有焦点
  try {
    Document.prototype.hasFocus = function () { return true; };
    Object.defineProperty(document, 'hasFocus', {
      configurable: true,
      value: () => true,
    });
  } catch (e) {}

  // 3) 拦截"用户离开"类事件, 阻止平台据此暂停
  //    捕获阶段 + stopImmediatePropagation 可以拦在平台监听器之前
  //    (本脚本由 add_init_script 注入, 注册时机早于页面自身脚本)
  const silent = [
    'visibilitychange',
    'webkitvisibilitychange',
    'blur',
    'pagehide',
    'freeze',
  ];
  const block = (event) => {
    try { event.stopImmediatePropagation(); } catch (e) {}
  };
  for (const type of silent) {
    window.addEventListener(type, block, true);
    document.addEventListener(type, block, true);
  }
})();
"""


async def install(page: Page, logger=None) -> bool:
    """对当前页面立即生效, 并对后续导航持续生效。

    add_init_script 只作用于之后的文档, 所以这里额外 evaluate 一次,
    保证已经在播放的页面也立刻戴上"面具"。
    """
    if page is None:
        return False
    try:
        await page.add_init_script(ANTI_IDLE_JS)
    except Exception as exc:
        if logger:
            logger.debug(f"防挂机脚本注册失败(导航后可能不生效): {exc}")
    try:
        await page.evaluate(ANTI_IDLE_JS)
        return True
    except Exception as exc:
        if logger:
            logger.debug(f"防挂机脚本注入当前页面失败: {exc}")
        return False
