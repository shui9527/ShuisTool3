# -*- coding: utf-8 -*-
"""ai_chat.py 新增逻辑自测（不发网络请求，mock sender）"""
import sys, os, json, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from function.ai_chat import AiChatEngine, _safe_slice, SENSITIVE_WORDS

ok = 0; fail = 0
def check(name, cond, detail=''):
    global ok, fail
    if cond: ok += 1; print(f'  PASS {name}')
    else: fail += 1; print(f'  FAIL {name}  {detail}')

cfg_path = os.path.join(tempfile.mkdtemp(), 'ai_config.json')
eng = AiChatEngine(cfg_path, sender_mgr=None)
cfg = eng._snapshot()

# 1) 新配置字段归一化
check('call 组存在', cfg['call']['concurrency'] == 1 and cfg['call']['timeout'] == 90 and cfg['call']['retry'] == 1)
check('context.budget/maxInputLen', cfg['context']['budget'] == 1200 and cfg['context']['maxInputLen'] == 100)
check('max_tokens 默认 80', cfg['max_tokens'] == 80)

# 2) _safe_slice：emoji 不切断
s = '你好👍😀' + 'x' * 30
cut = _safe_slice(s, 20)
check('safe_slice 长度', len(cut) <= 20, f'len={len(cut)}')
# 尾部不应是变体选择符或孤立代理（😀 是 2 个 code point，切中时应回退）
last = cut[-1]
check('safe_slice 无孤立代理', not ('\uD800' <= last <= '\uDFFF') and not ('\uFE00' <= last <= '\uFE0F'))

# 3) _process_reply：敏感过滤命中
r, note = eng._process_reply(cfg, '扫码加群领福利', '弹幕')
check('内置敏感词拦截', r is None and '敏感词' in note, note)
r, note = eng._process_reply(cfg, '打电话给我13800138000', '弹幕')
check('手机号正则拦截', r is None and '敏感格式' in note, note)
# 未命中正常裁剪
r, note = eng._process_reply(cfg, '主播唱得真好听，继续加油继续努力呀', '弹幕')
check('正常回复裁剪', r is not None and len(r) <= 20, repr(r))

# 4) B 模式会话记忆：_build_messages 含 assistant 后形成多轮
cfg['context']['mode'] = 'B'
cfg['context']['window'] = 4
eng._user_sessions[123] = __import__('collections').deque(maxlen=4)
eng._user_sessions[123].append((time.time(), 'user', '小明', '你好'))
eng._user_sessions[123].append((time.time(), 'assistant', '', '我是AI助手'))
msgs = eng._build_messages(cfg, 123, '小明', '你叫什么', '你叫什么')
roles = [m['role'] for m in msgs]
check('B 模式含 assistant 轮次', roles == ['system', 'user', 'assistant', 'user'], str(roles))
# 会话里当前弹幕已追加
check('B 模式追加当前弹幕', eng._user_sessions[123][-1][3] == '你叫什么')

# 5) 预算截断：塞满后丢最早的
cfg['context']['budget'] = 100
eng._user_sessions[124] = __import__('collections').deque(maxlen=8)
for i in range(6):
    eng._user_sessions[124].append((time.time(), 'user', 'u', '这是一条比较长的弹幕内容用来测试预算' * 3))
msgs2 = eng._build_messages(cfg, 124, 'u', '最后这条', '最后这条')
total = sum(len(str(m.get('content') or '')) for m in msgs2)
check('预算截断总量受限', total <= 100 + 200, f'total={total}')
check('预算截断保留最后一条', msgs2[-1]['content'].endswith('最后这条') or '最后这条' in msgs2[-1]['content'])
check('预算截断保留 system', msgs2[0]['role'] == 'system')

# 6) C 模式指向明确：最新弹幕单独标注
cfg['context']['mode'] = 'C'
eng._global_recent.clear()
eng._global_recent.append((time.time(), '路人甲', '主播唱首歌'))
msgs3 = eng._build_messages(cfg, 999, '主播', '来了', '来了')
check('C 模式标注最新弹幕', '只回复最新这条弹幕' in msgs3[-1]['content'], msgs3[-1]['content'])
check('C 模式背景含路人甲', '路人甲说：主播唱首歌' in msgs3[-1]['content'])

# 7) 限流 ticket 精确回退
cfg2 = eng._snapshot()
cfg2['throttle'] = {'globalCool': 0, 'userCool': 0, 'perMinute': 3}
ok1, r1, t1 = eng._throttle_ok(cfg2, 777)
ok2, r2, t2 = eng._throttle_ok(cfg2, 888)
eng._throttle_undo(t1)  # 回退 t1（uid=777），不应影响 t2 占用
check('ticket 回退不误伤他人', eng._user_last_map.get(888) == t2['user_ts'], str(eng._user_last_map))
check('ticket 回退自身', 777 not in eng._user_last_map)
check('minute 记录剩 t2', len(eng._minute_ts) == 1, str(eng._minute_ts))
eng._throttle_undo(t2)
check('双回退后 minute 队列空', len(eng._minute_ts) == 0, str(eng._minute_ts))

# 8) _openai_base 归一化（ollama 补 /v1）
from function.ai_chat import AiChatEngine as E
check('ollama 补 /v1', E._openai_base('http://127.0.0.1:11434', 'ollama') == 'http://127.0.0.1:11434/v1')
check('已有 /v1 不重复', E._openai_base('http://127.0.0.1:11434/v1', 'ollama') == 'http://127.0.0.1:11434/v1')
check('llamacpp 原样', E._openai_base('http://127.0.0.1:8080/v1', 'llamacpp') == 'http://127.0.0.1:8080/v1')

# 9) 日期注入：小模型无日期知识，system prompt 自动带当前日期
sp1 = eng._inject_date('你是助手。')
check('日期注入前缀', sp1.startswith('今天是 ') and '星期' in sp1[:20], sp1)
check('已含日期不重复注入', eng._inject_date('今天是 2026年1月1日，星期四。助手。') == '今天是 2026年1月1日，星期四。助手。', '')
check('空 prompt 不注入', eng._inject_date('') == '', '')

# 10) persona_prompts：按人设保存自定义 System Prompt，切换不丢失
cfg10 = {'persona': 'gentle', 'target_words': 20, 'system_prompt': '',
         'persona_prompts': {'gentle': 'gentle 自定义', 'geek': 'geek 自定义'}}
sp = eng._build_system_prompt(cfg10)
check('persona_prompts 自定义优先', sp.startswith('gentle 自定义') or 'gentle 自定义' in sp, sp)
cfg11 = {'persona': 'funny', 'target_words': 20, 'system_prompt': '',
         'persona_prompts': {'gentle': 'gentle 自定义'}}
sp = eng._build_system_prompt(cfg11)
check('无自定义回落模板', '沙雕' in sp, sp)
cfg12 = {'persona': 'custom', 'target_words': 20, 'system_prompt': '',
         'persona_prompts': {}}
check('custom 无自定义为空', eng._build_system_prompt(cfg12) == '', eng._build_system_prompt(cfg12))
# 旧配置迁移：全局 system_prompt 归入当前人设
engM = AiChatEngine(os.path.join(tempfile.mkdtemp(), 'ai_config.json'), sender_mgr=None)
engM.config = {'persona': 'gentle', 'system_prompt': '旧全局', 'persona_prompts': {}}
engM._normalize_config()
check('旧 system_prompt 迁移到当前人设', engM.config['persona_prompts'].get('gentle') == '旧全局', str(engM.config['persona_prompts']))

# 11) 用户黑名单（昵称/UID 命中不触发）
cfg_ub = {'trigger': {'userBlacklist': ['坏人甲', '12345']}, 'filter': {}}
check('黑名单昵称命中', eng._hit_user_blacklist(cfg_ub, '坏人甲', '888') == '坏人甲')
check('黑名单 uid 命中', eng._hit_user_blacklist(cfg_ub, '好人', '12345') == '12345')
check('非黑名单用户放行', eng._hit_user_blacklist(cfg_ub, '好人', '888') is None)
check('空黑名单放行', eng._hit_user_blacklist({'trigger': {'userBlacklist': []}}, '任何人', '9') is None)
# 全链路：黑名单用户 @AI 弹幕不进入链路
cfg_ub2 = eng._snapshot()
cfg_ub2['enabled'] = True
cfg_ub2['trigger']['prefixEnabled'] = True
cfg_ub2['trigger']['prefixes'] = ['@AI']
cfg_ub2['trigger']['userBlacklist'] = ['坏人甲']
eng_ub = AiChatEngine(os.path.join(tempfile.mkdtemp(), 'ai_config.json'), sender_mgr=None)
eng_ub.config = cfg_ub2
check('黑名单用户弹幕不触发', eng_ub.handle_danmaku({'danmu': '@AI 你好', 'username': '坏人甲', 'uid': '888'}) is False)
check('普通用户弹幕进入链路', eng_ub.handle_danmaku({'danmu': '@AI 你好', 'username': '路人', 'uid': '999'}) is True)

print(f'\n结果: {ok} 通过, {fail} 失败')
sys.exit(1 if fail else 0)
