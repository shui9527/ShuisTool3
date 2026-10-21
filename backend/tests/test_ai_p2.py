# -*- coding: utf-8 -*-
"""P2 特性自测：流式输出（_call_ai_stream / stream_test）+ 同用户重复弹幕去重（mock requests，不联网）"""
import sys, os, json, time, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import function.ai_chat as mod
from function.ai_chat import AiChatEngine

ok = 0; fail = 0
def check(name, cond, detail=''):
    global ok, fail
    if cond: ok += 1; print(f'  PASS {name}')
    else: fail += 1; print(f'  FAIL {name}  {detail}')

BASE_CFG = {
    'enabled': True, 'provider': 'llamacpp', 'base_url': 'http://127.0.0.1:8080/v1',
    'api_key': '', 'model': 'test', 'temperature': 0.7, 'max_tokens': 80,
    'persona': 'host', 'system_prompt': '', 'target_words': 20, 'max_reply_len': 20,
    'forbidden_check': True,
    'trigger': {'prefixEnabled': True, 'prefixes': ['@AI'], 'keywordEnabled': False,
                'keywords': [], 'allEnabled': False, 'probability': 100},
    'throttle': {'globalCool': 0, 'userCool': 0, 'perMinute': 100},
    'context': {'mode': 'A', 'window': 4, 'globalWindow': 15, 'expireMinutes': 5,
                'withUsername': True, 'budget': 1200, 'maxInputLen': 100, 'dedupeSeconds': 60},
    'call': {'concurrency': 1, 'timeout': 30, 'retry': 0, 'keep_alive': 0},
    'filter': {'triggerBlacklist': [], 'replyBlacklist': []},
}

# ---------- 1) 去重逻辑 ----------
eng = AiChatEngine(os.path.join(tempfile.mkdtemp(), 'ai_config.json'), sender_mgr=None)
check('首次放行', eng._dedupe_check(1, '一样的话', 60) is True)
check('窗口内重复拦截', eng._dedupe_check(1, '一样的话', 60) is False)
check('不同内容放行', eng._dedupe_check(1, '别的话', 60) is True)
check('不同用户放行', eng._dedupe_check(2, '一样的话', 60) is True)
check('窗口 0 关闭去重', eng._dedupe_check(1, '一样的话', 0) is True)
eng._dedupe_map[1] = ('一样的话', time.time() - 100)
check('窗口过期后放行', eng._dedupe_check(1, '一样的话', 60) is True)

# ---------- 2) handle_danmaku 集成：重复弹幕不触发、有日志 ----------
eng2 = AiChatEngine(os.path.join(tempfile.mkdtemp(), 'ai_config.json'), sender_mgr=None)
def fake_call(self, cfg, messages):
    return '主播好呀', {}
eng2._call_ai = fake_call.__get__(eng2, AiChatEngine)
eng2.save_config(dict(BASE_CFG))
r1 = eng2.handle_danmaku({'danmu': '@AI 你好', 'username': '小明', 'uid': 11})
time.sleep(0.1)
after_first = dict(eng2._user_last_map)
minute_after_first = len(eng2._minute_ts)
r2 = eng2.handle_danmaku({'danmu': '@AI 你好', 'username': '小明', 'uid': 11})
check('首次触发 True', r1 is True, str(r1))
check('重复弹幕不触发', r2 is False, str(r2))
check('重复有去重日志', any('重复弹幕去重' in (l.get('note') or '') for l in eng2.get_logs()), '')
check('重复不新增限流占用', eng2._user_last_map == after_first and len(eng2._minute_ts) == minute_after_first,
      f'map={eng2._user_last_map} minute={len(eng2._minute_ts)}')

# ---------- 3) _call_ai_stream：SSE 解析 + usage 捕获 ----------
class FakeStreamResp:
    def __init__(self, lines, status=200, text='', encoding='iso-8859-1'):
        # encoding 默认模拟 requests 在响应头无 charset 时的行为（ISO-8859-1）
        self._lines = lines; self.status_code = status; self.text = text
        self.encoding = encoding
    def iter_lines(self, decode_unicode=False):
        for l in self._lines:
            if decode_unicode and isinstance(l, bytes):
                yield l.decode(self.encoding)
            else:
                yield l
    def json(self):
        return {}

sss = [
    'data: {"choices":[{"delta":{"role":"assistant"}}]}',
    'data: {"choices":[{"delta":{"content":"你"}}]}',
    'data: {"choices":[{"delta":{"content":"好"}}]}',
    'data: {"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":4}}',
    'data: [DONE]',
]
sent_json = {}
def fake_post_stream(url, headers=None, json=None, stream=False, timeout=None):
    sent_json.clear(); sent_json.update(json or {})
    return FakeStreamResp(sss)
mod.requests.post = fake_post_stream

eng3 = AiChatEngine(os.path.join(tempfile.mkdtemp(), 'ai_config.json'), sender_mgr=None)
cfg3 = eng3._snapshot()
cfg3['base_url'] = 'http://127.0.0.1:8080/v1'; cfg3['provider'] = 'llamacpp'
usage_box = {}
pieces = list(eng3._call_ai_stream(cfg3, [{'role': 'user', 'content': 'hi'}], usage_box))
check('流式拼出完整文本', ''.join(pieces) == '你好', repr(''.join(pieces)))
check('stream=True 已发送', sent_json.get('stream') is True)
check('usage_box 捕获', usage_box.get('prompt_tokens') == 10 and usage_box.get('completion_tokens') == 4, str(usage_box))

# ---------- 3.5) 编码回归：SSE 无 charset 时（bytes 流）中文必须不乱码 ----------
def fake_post_bytes(url, headers=None, json=None, stream=False, timeout=None):
    # 构造真实 UTF-8 字节的 SSE 行（llama.cpp 无 charset 场景）
    line = 'data: {"choices":[{"delta":{"content":"中文测试"}}]}'.encode('utf-8')
    return FakeStreamResp([line, b'data: [DONE]'], encoding='iso-8859-1')
mod.requests.post = fake_post_bytes

eng31 = AiChatEngine(os.path.join(tempfile.mkdtemp(), 'ai_config.json'), sender_mgr=None)
cfg31 = eng31._snapshot()
cfg31['base_url'] = 'http://127.0.0.1:8080/v1'; cfg31['provider'] = 'llamacpp'
pieces31 = list(eng31._call_ai_stream(cfg31, [{'role': 'user', 'content': 'hi'}]))
check('无 charset 字节流中文不乱码', ''.join(pieces31) == '中文测试', repr(''.join(pieces31)))

# ---------- 4) stream_test：流式事件序列 + 非流式回退 ----------
class FakeNonStreamResp:
    def __init__(self, status=200, payload=None, text=''):
        self.status_code = status; self._payload = payload or {}; self.text = text
    def iter_lines(self, decode_unicode=False):
        return iter([])
    def json(self):
        return self._payload

def fake_post_mixed(url, headers=None, json=None, stream=False, timeout=None):
    if json.get('stream'):
        return FakeStreamResp([], status=400, text='stream not supported')
    return FakeNonStreamResp(200, {'choices': [{'message': {'content': '回退你好'}}],
                                   'usage': {'prompt_tokens': 5, 'completion_tokens': 3}})
mod.requests.post = fake_post_mixed

eng4 = AiChatEngine(os.path.join(tempfile.mkdtemp(), 'ai_config.json'), sender_mgr=None)
eng4.save_config(dict(BASE_CFG))
events = list(eng4.stream_test('测试消息', username='测试用户', uid=999))
deltas = [e['text'] for e in events if e['event'] == 'delta']
done = [e for e in events if e['event'] == 'done']
check('回退非流式整段输出', ''.join(deltas) == '回退你好', repr(''.join(deltas)))
check('done 事件存在', len(done) == 1, str(events))
check('done ok 且回复已处理', bool(done) and done[0].get('ok') is True and done[0].get('reply') == '回退你好', str(done))
check('done 带 sent 状态', bool(done) and done[0].get('sent_ok') is False, str(done))
check('流式槽位已释放', eng4._ai_busy == 0 and eng4._ai_waiting == 0, f'busy={eng4._ai_busy}')

# ---------- 5) stream_test：正常流式事件序列 ----------
def fake_post_ok(url, headers=None, json=None, stream=False, timeout=None):
    if json.get('stream'):
        return FakeStreamResp([
            'data: {"choices":[{"delta":{"content":"流"}}]}',
            'data: {"choices":[{"delta":{"content":"式好"}}]}',
            'data: {"choices":[],"usage":{"prompt_tokens":7,"completion_tokens":2}}',
            'data: [DONE]',
        ])
    return FakeNonStreamResp(200, {'choices': [{'message': {'content': 'x'}}]})
mod.requests.post = fake_post_ok

eng5 = AiChatEngine(os.path.join(tempfile.mkdtemp(), 'ai_config.json'), sender_mgr=None)
eng5.save_config(dict(BASE_CFG))
events5 = list(eng5.stream_test('测试', username='测试用户', uid=1001))
deltas5 = ''.join(e['text'] for e in events5 if e['event'] == 'delta')
done5 = [e for e in events5 if e['event'] == 'done']
check('正常流式逐段 delta', deltas5 == '流式好', repr(deltas5))
check('done usage 统计', bool(done5) and done5[0].get('usage') == '↑7 ↓2', str(done5))

# ---------- 6) 思考模式参数（call.thinking: auto/on/off × 本地/云端）----------
def mk_thinking_cfg(provider, thinking):
    c = dict(BASE_CFG)
    c['provider'] = provider
    c['call'] = {'concurrency': 1, 'timeout': 30, 'retry': 0, 'keep_alive': 0, 'thinking': thinking}
    return c

eng6 = AiChatEngine(os.path.join(tempfile.mkdtemp(), 'ai_config.json'), sender_mgr=None)
a = eng6._thinking_args(mk_thinking_cfg('llamacpp', 'auto'))
check('本地 auto 自动关闭思考', a == {'chat_template_kwargs': {'enable_thinking': False}, 'reasoning_effort': 'none'}, str(a))
a = eng6._thinking_args(mk_thinking_cfg('llamacpp', 'off'))
check('本地 off 关闭思考', a == {'chat_template_kwargs': {'enable_thinking': False}, 'reasoning_effort': 'none'}, str(a))
a = eng6._thinking_args(mk_thinking_cfg('lmstudio', 'on'))
check('本地 on 开启思考', a == {'chat_template_kwargs': {'enable_thinking': True}}, str(a))
a = eng6._thinking_args(mk_thinking_cfg('openai', 'on'))
check('云端 openai on 附加 reasoning_effort', a == {'reasoning_effort': 'medium'}, str(a))
a = eng6._thinking_args(mk_thinking_cfg('qwen', 'on'))
check('云端 qwen on 附加 enable_thinking', a == {'enable_thinking': True}, str(a))
a = eng6._thinking_args(mk_thinking_cfg('qwen', 'off'))
check('云端 qwen off 关闭', a == {'enable_thinking': False}, str(a))
a = eng6._thinking_args(mk_thinking_cfg('openai', 'auto'))
check('云端 auto 不附加', a == {}, str(a))

# 走真实 _call_ai 路径：payload 带上 thinking 参数
sent_thinking = {}
def fake_post_thinking(url, headers=None, json=None, stream=False, timeout=None):
    sent_thinking.clear(); sent_thinking.update(json or {})
    return FakeNonStreamResp(200, {'choices': [{'message': {'content': '正常回复'}}],
                                   'usage': {'prompt_tokens': 5, 'completion_tokens': 3}})
mod.requests.post = fake_post_thinking
eng62 = AiChatEngine(os.path.join(tempfile.mkdtemp(), 'ai_config.json'), sender_mgr=None)
raw62, _ = eng62._call_ai(mk_thinking_cfg('llamacpp', 'on'), [{'role': 'user', 'content': 'hi'}])
check('真实请求 payload 带思考开关', sent_thinking.get('chat_template_kwargs', {}).get('enable_thinking') is True, str(sent_thinking))
check('真实请求正常返回', raw62 == '正常回复', repr(raw62))

print(f'\nP2 自测结果: {ok} 通过, {fail} 失败')
sys.exit(1 if fail else 0)
