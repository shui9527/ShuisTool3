# -*- coding: utf-8 -*-
"""接口级联测：导入 main 的 Flask app，用 test_client 验证 AI 路由（不写配置、不起服务）"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from main import app, AI_CONFIG_FILE

client = app.test_client()
ok = 0; fail = 0
def check(name, cond, detail=''):
    global ok, fail
    if cond: ok += 1; print(f'  PASS {name}')
    else: fail += 1; print(f'  FAIL {name}  {detail}')

# 1) GET /ai/config：新字段随配置返回（旧配置合并默认值）
r = client.get('/ai/config')
d = r.get_json()
check('/ai/config 200', r.status_code == 200, str(r.status_code))
check('配置含 call 组', isinstance(d.get('call'), dict) and d['call']['concurrency'] >= 1 and d['call']['timeout'] >= 10)
check('配置含 context.budget', isinstance(d.get('context', {}).get('budget'), int) and d['context']['budget'] >= 200)
check('配置含 maxInputLen', d.get('context', {}).get('maxInputLen') == 100)
check('max_tokens >= 16', d.get('max_tokens', 0) >= 16, str(d.get('max_tokens')))
check('api_key 脱敏', '***' in str(d.get('api_key') or '') or d.get('api_key') == '')
check('provider_name 识别', 'provider_name' in d)
print(f'  info: provider={d.get("provider")} model={d.get("model")}')

# 2) GET /ai/status
r = client.get('/ai/status')
s = r.get_json()
check('/ai/status 200', r.status_code == 200)
check('status 有 enabled 字段', 'enabled' in s, str(s.keys()))

# 3) GET /ai/models：路由存在且返回 JSON（llama.cpp 未启动时 ok=False + error）
r = client.get('/ai/models')
m = r.get_json()
check('/ai/models 200', r.status_code == 200, str(r.status_code))
check('models 返回结构', isinstance(m.get('models'), list), str(m)[:120])
if m.get('ok'):
    print(f'  info: 本地运行时已连，模型列表={m["models"]}')
else:
    print(f'  info: 本地运行时未连接（预期）: {m.get("error")}')

# 4) POST /ai/test ping（不写配置）
r = client.post('/ai/test', json={'mode': 'ping'})
t = r.get_json()
check('/ai/test ping 200', r.status_code == 200)
check('ping 返回 ok 字段', 'ok' in t, str(t)[:120])
print(f'  info: ping 结果 ok={t.get("ok")} provider={t.get("provider")}')

print(f'\n接口自测结果: {ok} 通过, {fail} 失败')
sys.exit(1 if fail else 0)
