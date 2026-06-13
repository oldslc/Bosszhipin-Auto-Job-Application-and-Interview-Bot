"""测试通过代理访问BOSS直聘"""
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            proxy={"server": "http://127.0.0.1:7890"},
            args=[
                "--no-first-run",
                "--no-default-browser-check",
            ]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        # 先查IP
        await page.goto("https://httpbin.org/ip")
        content = await page.content()
        print(f"[IP Check] {content}")
        
        # 再试BOSS
        await page.goto("https://www.zhipin.com/web/geek/resume?ca=1")
        await page.wait_for_timeout(3000)
        title = await page.title()
        url = page.url
        print(f"[BOSS] Title: {title}")
        print(f"[BOSS] URL: {url}")
        
        input("按Enter退出...")
        await browser.close()

asyncio.run(main())
