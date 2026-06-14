"""CDP direct control - navigate & read content"""
import json, time, subprocess, sys, base64
from websocket import create_connection

def run(url):
    r = subprocess.run(['curl', '-s', 'http://localhost:9322/json/version'], capture_output=True, text=True)
    browser_ws = json.loads(r.stdout)['webSocketDebuggerUrl']
    
    r = subprocess.run(['curl', '-s', '-X', 'PUT', f'http://localhost:9322/json/new?{url}'], 
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
    
    r = cmd('Runtime.evaluate', {'expression': 'location.href'})
    print(f"URL: {r['result']['value']}")
    
    r = cmd('Runtime.evaluate', {'expression': 'document.body?.innerText?.substring(0,3000) || "null"'})
    print(f"BODY:\n{r['result']['value']}")
    
    r = cmd('Page.captureScreenshot', {'format': 'png'})
    with open('/tmp/boss_check.png', 'wb') as f:
        f.write(base64.b64decode(r['data']))
    print(f"\nScreenshot saved")
    
    ws.close()

if __name__ == '__main__':
    run('https://www.zhipin.com/web/geek/resume?ca=1')
