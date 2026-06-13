"""从CDP Chrome提取BOSS cookie，然后通过API直接修改简历"""
import json, time, subprocess, requests
from websocket import create_connection

# 1. 创建新标签页到BOSS简历页（利用已登录状态）
r = subprocess.run(['curl', '-s', '-X', 'PUT', 'http://localhost:9322/json/new?https://www.zhipin.com/web/geek/resume?ca=1'], 
                  capture_output=True, text=True)
tab = json.loads(r.stdout)
page_ws = tab['webSocketDebuggerUrl']

ws = create_connection(page_ws, timeout=15, suppress_origin=True)

def cmd(method, params=None, id=1):
    msg = {'id': id, 'method': method}
    if params: msg['params'] = params
    ws.send(json.dumps(msg))
    while True:
        r = json.loads(ws.recv())
        if r.get('id') == id:
            return r.get('result')

time.sleep(5)

# 2. 获取页面信息
r = cmd('Runtime.evaluate', {'expression': 'location.href'})
current_url = r['result']['value']
print(f"URL: {current_url}")

r = cmd('Runtime.evaluate', {'expression': 'document.title'})
print(f"Title: {r['result']['value']}")

# 3. 提取cookies
r = cmd('Network.getAllCookies')
cookies = r.get('cookies', [])
boss_cookies = {c['name']: c['value'] for c in cookies if 'zhipin' in c.get('domain', '') or 'boss' in c.get('domain', '') or 'zhipin' in c.get('url', '')}
print(f"\nBOSS cookies found: {len(boss_cookies)}")
for k, v in boss_cookies.items():
    print(f"  {k}: {v[:50]}...")

# 4. 获取完整的cookies字符串
all_cookies = {}
for c in cookies:
    domain = c.get('domain', '')
    name = c['name']
    value = c['value']
    if name not in all_cookies:
        all_cookies[name] = value
    
cookies_str = '; '.join([f'{k}={v}' for k, v in all_cookies.items()])
print(f"\nAll cookies: {cookies_str[:500]}")

# 5. 用cookies请求BOSS简历API
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36',
    'Cookie': cookies_str,
    'Accept': 'application/json, text/plain, */*',
    'Referer': 'https://www.zhipin.com/web/geek/resume',
    'Origin': 'https://www.zhipin.com',
}

# 尝试获取简历信息API
urls = [
    'https://www.zhipin.com/wapi/zprelation/geek/resume',
    'https://www.zhipin.com/wapi/zpgeek/resume',
    'https://www.zhipin.com/wapi/zpgeek/resume/list',
    'https://www.zhipin.com/wapi/zpuser/geek/info',
]

for url in urls:
    try:
        r = requests.get(url, headers=headers, timeout=10)
        print(f"\n{url}")
        print(f"  Status: {r.status_code}")
        print(f"  Body: {r.text[:300]}")
    except Exception as e:
        print(f"  Error: {e}")

ws.close()
