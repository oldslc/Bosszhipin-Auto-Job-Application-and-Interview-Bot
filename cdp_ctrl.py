"""CDP direct control for Chrome - navigate & screenshot"""
import json, time, subprocess, sys, os
from websocket import create_connection

def main():
    # 获取最新浏览器WS
    r = subprocess.run(['curl', '-s', 'http://localhost:9322/json/version'], capture_output=True, text=True)
    browser_ws = json.loads(r.stdout)['webSocketDebuggerUrl']
    
    # 创建新标签页
    r = subprocess.run(['curl', '-s', '-X', 'PUT', 'http://localhost:9322/json/new?' + sys.argv[1]], 
                      capture_output=True, text=True)
    tab = json.loads(r.stdout)
    page_ws = tab['webSocketDebuggerUrl']
    
    # 连到页面
    ws = create_connection(page_ws, timeout=15, suppress_origin=True)
    
    def cmd(method, params=None, id=1):
        msg = {'id': id, 'method': method}
        if params: msg['params'] = params
        ws.send(json.dumps(msg))
        while True:
            r = json.loads(ws.recv())
            if r.get('id') == id:
                return r.get('result')
    
    # 等页面加载
    time.sleep(4)
    
    # 获取状态
    r = cmd('Runtime.evaluate', {'expression': 'JSON.stringify({url:location.href, title:document.title})'})
    data = json.loads(r['result']['value'])
    print(json.dumps(data, ensure_ascii=False))
    
    # 截图
    r = cmd('Page.captureScreenshot', {'format': 'png'})
    screenshot_b64 = r['data']
    
    # 保存截图
    out_path = sys.argv[2] if len(sys.argv) > 2 else '/mnt/c/Users/27871/OneDrive/Desktop/boss_shot.png'
    import base64
    with open(out_path, 'wb') as f:
        f.write(base64.b64decode(screenshot_b64))
    print(f"Screenshot: {out_path}")
    
    ws.close()

if __name__ == '__main__':
    main()
