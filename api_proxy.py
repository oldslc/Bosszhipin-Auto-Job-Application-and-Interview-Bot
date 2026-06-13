"""通过代理调BOSS API"""
import json, subprocess, requests

# 1. 先创建标签页拿cookie
r = subprocess.run(['curl', '-s', '-X', 'PUT', 'http://localhost:9322/json/new?https://www.zhipin.com/web/geek/resume?ca=1'], 
                  capture_output=True, text=True)
tab = json.loads(r.stdout)
page_ws = tab['webSocketDebuggerUrl']

from websocket import create_connection
ws = create_connection(page_ws, timeout=15, suppress_origin=True)

def cmd(method, params=None, id=1):
    msg = {'id': id, 'method': method}
    if params: msg['params'] = params
    ws.send(json.dumps(msg))
    while True:
        r = json.loads(ws.recv())
        if r.get('id') == id:
            return r.get('result')

import time; time.sleep(4)

# 拿cookie
r = cmd('Network.getAllCookies')
cookies_raw = r.get('cookies', [])
cookies_str = '; '.join([f'{c["name"]}={c["value"]}' for c in cookies_raw])
ws.close()

print(f"Cookies ({len(cookies_raw)}): {cookies_str[:300]}...")

# 2. 通过代理调API
proxy = 'http://127.0.0.1:7890'
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Cookie': cookies_str,
    'Accept': 'application/json, text/plain, */*',
    'Referer': 'https://www.zhipin.com/web/geek/resume',
}

urls = [
    'https://www.zhipin.com/wapi/zprelation/geek/resume',
    'https://www.zhipin.com/wapi/zpgeek/resume',
    'https://www.zhipin.com/wapi/zpuser/geek/info',
    'https://www.zhipin.com/wapi/zpgeek/resume/expect',
]

for url in urls:
    try:
        r = requests.get(url, headers=headers, proxies={'http': proxy, 'https': proxy}, timeout=15)
        data = r.json()
        print(f"\n{url.split('/')[-1]}")
        print(f"  code={data.get('code')}, msg={data.get('message','')[:80]}")
        if data.get('code') == 0 and data.get('zpData'):
            print(f"  data keys: {list(data['zpData'].keys())[:10]}")
            print(f"  data: {json.dumps(data['zpData'], ensure_ascii=False, indent=2)[:500]}")
    except Exception as e:
        print(f"\n{url.split('/')[-1]} Error: {e}")
