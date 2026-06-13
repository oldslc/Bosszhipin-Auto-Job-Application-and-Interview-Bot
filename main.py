"""Boss直聘求职者自动对话Agent - 主入口"""
import logging
import os
import signal
import sys
import time
from datetime import datetime
import config
from browser import BrowserController
from llm_client import LLMClient
from chat_handler import ChatHandler
from monitor import ChatMonitor
from job_hunter import JobHunter

def setup_logging():
    os.makedirs(config.LOGS_DIR, exist_ok=True)
    log_filename = datetime.now().strftime("geek-agent_%Y%m%d_%H%M%S.log")
    log_path = os.path.join(config.LOGS_DIR, log_filename)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(log_path, encoding="utf-8")]
    )
    return logging.getLogger(__name__)

def print_banner():
    banner = """\
+----------------------------------------------------+
|  Boss直聘求职者自动对话 Agent                        |
|  反检测: CDP(真实Chrome) / Playwright(Stealth JS)   |
+----------------------------------------------------+\
"""
    print(banner)
    print("当前配置:")
    print(f"  求职者: 丁雨阳 (AI应用开发)")
    print(f"  薪资底线: {config.SALARY_MIN} 元/月")
    print(f"  城市: {', '.join(config.CITIES)}")
    print(f"  回复风格: {config.REPLY_STYLE}")
    print(f"  LLM: {config.LLM_MODEL}")
    print(f"  轮询间隔: {config.POLL_INTERVAL}s")
    if config.CHROME_CDP_PORT:
        print(f"  浏览器模式: CDP(真实Chrome) → 端口 {config.CHROME_CDP_PORT}")
        print(f"  提示: 确保 Chrome 已以 --remote-debugging-port={config.CHROME_CDP_PORT} 启动")
    else:
        print(f"  浏览器模式: Playwright + 增强 Stealth JS")
    if config.PROXY:
        print(f"  代理: {config.PROXY}")
    print("-" * 40)

def main():
    logger = setup_logging()
    print_banner()
    browser = BrowserController()
    llm_client = LLMClient()
    chat_handler = ChatHandler(browser, llm_client)
    monitor = ChatMonitor(browser, chat_handler)
    should_exit = False

    def handle_signal(sig, frame):
        nonlocal should_exit
        logger.info("收到退出信号，正在停止...")
        print("\n正在停止 Agent...")
        should_exit = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        print("正在启动浏览器...")
        browser.connect()
        print("浏览器启动成功")
        print(f"  用户数据目录: {browser.get_user_data_dir()}")
        print(f"  提示: 首次启动需手动登录 Boss直聘\n")

        # ============================================================
        # 阶段1: 自动投递简历
        # ============================================================
        print("🎯 开始自动搜索职位并投递简历...")
        hunter = JobHunter(browser)
        hunter.run_once()
        print(f"✅ 投递完成，共沟通 {hunter.delivered_today} 个职位\n")

        # ============================================================
        # 阶段2: 监听消息 & 自动回复
        # ============================================================
        print("正在打开 Boss 直聘求职者聊天页面...")
        success = browser.navigate_and_handle_verification()
        if not success:
            print("导航失败或验证超时，将继续尝试轮询...")

        if browser.is_blocked:
            print("\n[警告] 当前处于 Boss 直聘安全验证页面")
            print("  方案1: 在打开浏览器中手动完成验证码")
            print("  方案2: 在config.py中配置代理后重启\n")

        print(f"\n开始监听消息 (每 {config.POLL_INTERVAL}s 轮询)")
        print("  按 Ctrl+C 停止\n")

        while not should_exit:
            try:
                if browser.is_blocked:
                    logger.warning("当前页面处于安全验证状态，等待手动处理...")
                    time.sleep(10)
                    continue
                monitor.poll_once()
                if browser.is_blocked:
                    logger.warning("检测到被重定向到安全验证页！")
            except Exception as e:
                logger.error(f"轮询异常: {e}")
            time.sleep(config.POLL_INTERVAL)

    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.error(f"Agent 运行出错: {e}")
        print(f"\n运行出错: {e}")
    finally:
        print("正在清理资源...")
        browser.disconnect()
        print("Agent 已停止。再见！")

if __name__ == "__main__":
    main()
