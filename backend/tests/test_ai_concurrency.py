# -*- coding: utf-8 -*-
"""并发槽位 + ticket 回退 多线程压力自测（mock _call_ai，不联网）"""
import sys, os, time, threading, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from function.ai_chat import AiChatEngine

eng = AiChatEngine(os.path.join(tempfile.mkdtemp(), 'ai_config.json'), sender_mgr=None)
cfg = eng._snapshot()
cfg['enabled'] = True
cfg['call'] = {'concurrency': 1, 'timeout': 30, 'retry': 0, 'keep_alive': 0}
cfg['throttle'] = {'globalCool': 0, 'userCool': 0, 'perMinute': 1000}
cfg['context'] = {'mode': 'B', 'window': 4, 'globalWindow': 15, 'expireMinutes': 5,
                  'withUsername': True, 'budget': 1200, 'maxInputLen': 100}

# mock AI：耗时 0.15s，返回固定内容
_calls = []
_lock = threading.Lock()
def fake_call(self, cfg, messages):
    with _lock:
        _calls.append(time.time())
        cur = len(_calls)
    time.sleep(0.15)
    with _lock:
        cur2 = len([t for t in _calls])  # 简化：并发数用运行窗口估算
    return '主播说得对呀', {'prompt_tokens': 10, 'completion_tokens': 5}
eng._call_ai = fake_call.__get__(eng, AiChatEngine)

results = []
barrier = threading.Barrier(8)
def worker(i):
    barrier.wait()
    r = eng._run_ai_reply(cfg, 1000 + i, f'用户{i}', f'弹幕{i}', f'弹幕{i}', 'manual', ticket=None, skip_throttle=True)
    results.append((i, r))

threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
t0 = time.time()
for t in threads: t.start()
for t in threads: t.join()
elapsed = time.time() - t0

ok = 0; fail = 0
def check(name, cond, detail=''):
    global ok, fail
    if cond: ok += 1; print(f'  PASS {name}')
    else: fail += 1; print(f'  FAIL {name}  {detail}')

# 1) 全部线程返回（无异常/无死锁）
check('8 线程全部返回', len(results) == 8, str(len(results)))
# 2) 槽位全部释放
check('推理槽位已释放', eng._ai_busy == 0 and eng._ai_waiting == 0, f'busy={eng._ai_busy} waiting={eng._ai_waiting}')
# 3) 并发=1 下总耗时不应是 8*0.15 串行 1.2s 的极端，也不应 < 0.2s（说明无并发保护）
check('耗时合理（串行主导）', elapsed >= 0.15 * 4, f'elapsed={elapsed:.2f}s')
# 4) 排队上限丢弃：8 请求，concurrency=1 排队上限 3，应丢弃 8-1-3=4 个
dropped = [r for _, r in results if not r.get('ok') and '并发繁忙' in str(r.get('note', ''))]
check('超排队上限被丢弃', len(dropped) == 4, f'dropped={len(dropped)}')
# 5) 成功 4 个（1 运行 + 3 排队），且都形成会话记忆
succ = [r for _, r in results if r.get('ok')]
check('成功 4 个', len(succ) == 4, f'succ={len(succ)}')
with eng._ctx_lock:
    sess_count = sum(1 for v in eng._user_sessions.values() if v)
check('B 模式会话已写入', sess_count == 4, f'sessions={sess_count}')
check('会话含 assistant 记忆', all(any(item[1] == 'assistant' for item in v) for v in eng._user_sessions.values()))

# 6) 限流 ticket：失败线程回退自己的占用（throttle perMinute 上限压力）
eng2 = AiChatEngine(os.path.join(tempfile.mkdtemp(), 'ai_config.json'), sender_mgr=None)
cfg2 = eng2._snapshot()
cfg2['call'] = {'concurrency': 1, 'timeout': 30, 'retry': 0, 'keep_alive': 0}
cfg2['throttle'] = {'globalCool': 0, 'userCool': 0, 'perMinute': 1000}
cfg2['context'] = {'mode': 'A', 'budget': 1200, 'maxInputLen': 100}
def fail_call(self, cfg, messages):
    raise RuntimeError('模拟 AI 失败')
eng2._call_ai = fail_call.__get__(eng2, AiChatEngine)
res = []
def w2(i):
    ok2, reason, ticket = eng2._throttle_ok(cfg2, 2000 + i)
    if not ok2:
        res.append(('blocked', i)); return
    r = eng2._run_ai_reply(cfg2, 2000 + i, f'u{i}', f'd{i}', f'd{i}', 'auto', ticket=ticket, skip_throttle=False)
    res.append(('done', i, r))
ts = [threading.Thread(target=w2, args=(i,)) for i in range(4)]
for t in ts: t.start()
for t in ts: t.join()
check('失败线程回退后限流不残留', len(eng2._user_last_map) == 0, str(eng2._user_last_map))
check('minute 队列回退为空', len(eng2._minute_ts) == 0, str(list(eng2._minute_ts)))

print(f'\n并发自测结果: {ok} 通过, {fail} 失败')
sys.exit(1 if fail else 0)
