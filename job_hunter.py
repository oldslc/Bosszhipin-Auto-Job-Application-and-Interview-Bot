"""
职位搜索 & 自动投递模块
在 BOSS 搜索页自动扫职位，匹配条件后点"立即沟通"
"""
import json, logging, random, re, time
from typing import List, Dict

import config
from browser import BrowserController

logger = logging.getLogger(__name__)

# BOSS 特殊数字字符映射
NUM_MAP = {
    '': '0', '': '1', '': '2', '': '3', '': '4',
    '': '5', '': '6', '': '7', '': '8', '': '9', '': '10'
}

CITY_URLS = {
    "南通": "https://www.zhipin.com/web/geek/jobs?city=101190500",
    "南京": "https://www.zhipin.com/web/geek/jobs?city=100010000",
    "上海": "https://www.zhipin.com/web/geek/jobs?city=101020100",
}

# 轻松型岗位关键词
EASY_KEYWORDS = [
    '测试', '售后', '运维', '客服', '助理', '调试', 'Ai', 'ai', '智能',
    '技术支持', '售后维修', '售后调试', '技术员', '维护', '自动化',
    '检测', '质检', '品控', '数据'
]


class JobHunter:
    """职位搜索 & 自动投递"""

    def __init__(self, browser: BrowserController):
        self.browser = browser
        self.delivered_today = 0
        self.max_daily = 30  # 每天最多投 30 个

    def decode_salary(self, text: str) -> str:
        for k, v in NUM_MAP.items():
            text = text.replace(k, v)
        return text

    def is_good_job(self, title: str, salary_text: str, tags: str) -> tuple:
        """
        判断是否值得投递
        返回: (ok: bool, reason: str)
        """
        full = title + ' ' + tags
        clean = self.decode_salary(full)

        # 轻松型判断
        is_easy = any(kw in clean for kw in EASY_KEYWORDS)
        if not is_easy and '开发' not in clean and '工程' not in clean:
            return False, "非目标类型"

        # 薪资判断（取最低值 >= 8K）
        matches = re.findall(r'(\d+)[-~至](\d+)', clean)
        if matches:
            low = int(matches[0][0])
            if low < 8:
                return False, f"薪资{low}K<8K"

        return True, "符合条件"

    def scan_and_deliver(self, city: str):
        """扫描一个城市的职位并投递"""
        url = CITY_URLS.get(city)
        if not url:
            logger.warning(f"未知城市: {city}")
            return

        logger.info(f"📍 开始扫描 {city}: {url}")
        self.browser.navigate(url)
        time.sleep(5)

        page = self.browser.get_page()

        # 等待职位列表加载
        for wait in range(10):
            has_list = page.evaluate("document.querySelector('.rec-job-list')?.children?.length || 0")
            if has_list and int(has_list) > 0:
                logger.info(f"  职位列表已加载: {has_list} 个")
                break
            time.sleep(2)

        for page_num in range(1, 4):  # 最多扫3页
            if self.delivered_today >= self.max_daily:
                logger.info(f"今日已投满 {self.max_daily} 个")
                break

            # 获取左侧职位列表
            jobs_raw = page.evaluate('''(() => {
                const list = document.querySelector('.rec-job-list');
                if (!list) return '[]';
                const items = list.querySelectorAll('li');
                return JSON.stringify(Array.from(items).slice(0, 20).map((el, i) => {
                    const text = el.innerText || '';
                    return { index: i, text: text.slice(0, 300) };
                }));
            })()''')

            try:
                jobs = json.loads(jobs_raw) if jobs_raw else []
            except:
                jobs = []

            if not jobs:
                logger.info(f"  {city} 第{page_num}页: 无职位")
                break

            logger.info(f"  {city} 第{page_num}页: {len(jobs)} 个职位")

            for job in jobs:
                if self.delivered_today >= self.max_daily:
                    break

                text = job['text']
                lines = text.split('\n')
                title = lines[0] if lines else '?'
                salary = lines[1] if len(lines) > 1 else ''

                # 判断
                tags = ' '.join(lines[2:6])
                ok, reason = self.is_good_job(title, salary, tags)
                if not ok:
                    continue

                # 提取公司
                company = ''
                for line in lines[3:8]:
                    if line and len(line) > 2 and '经验' not in line and '大专' not in line \
                       and '本科' not in line and '学历' not in line and 'K' not in line:
                        company = line
                        break

                logger.info(f"  ✅ {title} | {salary} | {company[:20]}")

                # 点击该职位 → 再点"立即沟通"
                idx = job['index']
                result = self._click_and_chat(idx)

                if result:
                    self.delivered_today += 1
                    logger.info(f"    已沟通 ({self.delivered_today}/{self.max_daily})")
                    # 记录到对话文件
                    self._record_delivery(city, title, salary, company)
                else:
                    logger.info(f"    沟通失败")

                time.sleep(random.uniform(2, 4))

            # 滚动加载更多
            page.evaluate('''(() => {
                const container = document.querySelector('.rec-job-list');
                if (container) {
                    container.scrollTop = container.scrollHeight;
                    window.scrollBy(0, 600);
                }
            })()''')
            time.sleep(3)

            # 检查是否加载了新职位
            new_count = page.evaluate('''(() => {
                const list = document.querySelector('.rec-job-list');
                return list ? list.children.length : 0;
            })()''')

            if new_count and new_count <= len(jobs):
                break

    def _click_and_chat(self, index: int) -> bool:
        """点击职位 → 点立即沟通"""
        page = self.browser.get_page()

        # 1. 点击职位
        clicked = page.evaluate(f'''
            (() => {{
                const items = document.querySelectorAll('.rec-job-list li');
                const item = items[{index}];
                if (!item) return false;
                item.scrollIntoView({{behavior:"instant", block:"center"}});
                const rect = item.getBoundingClientRect();
                const evt = new MouseEvent('click', {{
                    bubbles:true, cancelable:true, view:window,
                    clientX: rect.x + rect.width/2, clientY: rect.y + rect.height/2
                }});
                item.dispatchEvent(evt);
                return true;
            }})()
        ''')
        if not clicked:
            return False
        time.sleep(2)

        # 2. 点"立即沟通"
        chatted = page.evaluate('''(() => {
            const btn = document.querySelector('.op-btn-chat, a.op-btn');
            if (!btn) {
                // 备用：找任何含"沟通"的按钮
                const all = document.querySelectorAll('a, button');
                for (let b of all) {
                    if (b.innerText?.includes('沟通') && b.offsetParent !== null) {
                        b.scrollIntoView({behavior:"instant", block:"center"});
                        const evt = new MouseEvent('click', {bubbles:true, cancelable:true, view:window});
                        b.dispatchEvent(evt);
                        return true;
                    }
                }
                return false;
            }
            btn.scrollIntoView({behavior:"instant", block:"center"});
            const evt = new MouseEvent('click', {bubbles:true, cancelable:true, view:window});
            btn.dispatchEvent(evt);
            return true;
        })()''')
        time.sleep(2)

        if chatted:
            # 处理弹窗
            page.evaluate('''(() => {
                const btns = document.querySelectorAll('.btn-primary.btn-sure, .popup .btn-primary, [class*="dialog"] .btn-primary');
                for (let b of btns) {
                    if (b.offsetParent !== null) {
                        b.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, view:window}));
                        return true;
                    }
                }
                return false;
            })()''')
            time.sleep(1.5)

        return chatted

    def _record_delivery(self, city: str, title: str, salary: str, company: str):
        """记录投递"""
        import os
        record_path = os.path.join(config.DATA_DIR, "delivered.json")
        records = []
        if os.path.exists(record_path):
            try:
                with open(record_path, encoding='utf-8') as f:
                    records = json.load(f)
            except:
                pass

        records.append({
            'time': time.strftime('%Y-%m-%d %H:%M'),
            'city': city,
            'title': title,
            'salary': salary,
            'company': company,
        })

        # 最多保留 200 条
        records = records[-200:]
        os.makedirs(os.path.dirname(record_path), exist_ok=True)
        with open(record_path, 'w', encoding='utf-8') as f:
            json.dump(records, f, ensure_ascii=False, indent=2)

    def run_once(self):
        """执行一次投递任务（每个城市扫一页）"""
        logger.info(f"🚀 开始自动投递 (今日已投: {self.delivered_today})")

        for city in config.CITIES:
            if self.delivered_today >= self.max_daily:
                break
            self.scan_and_deliver(city)

        logger.info(f"📊 本轮投递完成: 共 {self.delivered_today} 个")
