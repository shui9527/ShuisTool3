# -*- coding: utf-8 -*-
"""
AiChatEngine：AI 弹幕聊天（后端核心模块）

依据定稿方案 ai_chat_feature_design_20261002.md：
  弹幕回调 → AI 总开关 → 黑名单 → 触发判定(@前缀/关键词/全量，或关系)
  → 限流(全局冷却/同用户冷却/每分钟上限，随触发方式推荐档位)
  → 概率 → 上下文(A单条/B同用户小对话/C全局浅上下文±15秒，字符预算截断)
  → 调AI(独立线程，requests 直调 OpenAI 兼容 /chat/completions，不引 openai 库；
    并发槽位限流、可配超时/重试、Ollama keep_alive、多运行时兼容)
  → 回复处理(裁剪/内置敏感过滤/黑名单) → 发送(messagesender 单发) → 日志

多运行时兼容（OpenAI 兼容 /chat/completions）：
  - ollama   ：http://127.0.0.1:11434/v1（模型列表走 /api/tags，支持 keep_alive）
  - llamacpp ：llama.cpp server，http://127.0.0.1:8080/v1（模型列表走 /v1/models）
  - lmstudio ：LM Studio，http://127.0.0.1:1234/v1（模型列表走 /v1/models）
  - openai/deepseek/qwen/kimi 等云端 OpenAI 兼容服务

线程模型：弹幕回调运行于 blivedm 的 asyncio 循环线程。本模块在回调线程内
只做轻量判定（开关/黑名单/触发/概率/限流/上下文组装），AI 网络调用与发送
放入独立 daemon 线程执行（发送复用 sender_mgr.send_once，其内部已按
_run_async_via_thread 模式包 bilibili_api.sync()，可在任意线程调用），
保证不阻塞弹幕循环。并发槽位（_ai_gate）限制同时推理的请求数，避免本地
小模型被弹幕洪峰打爆。
"""
import json
import os
import random
import re
import threading
import time
from collections import deque
from datetime import datetime

import requests

# ---------------------------------------------------------------------------
# 配置常量
# ---------------------------------------------------------------------------
DEFAULT_AI_CONFIG = {
    # 总开关
    "enabled": False,
    # 模型配置
    "provider": "openai",          # 服务商预设键名（openai/deepseek/qwen/kimi/ollama/llamacpp/lmstudio/custom）
    "base_url": "https://api.openai.com/v1",
    "api_key": "",
    "model": "gpt-4o-mini",
    "temperature": 0.7,
    "max_tokens": 80,              # 输出 token 上限（20 字中文回复约 30~40 token，80 留足余量）
    # AI 行为
    "persona": "host",             # gentle/geek/funny/host/custom
    "system_prompt": "",
    "target_words": 20,            # 期望字数（写入 system prompt）
    "max_reply_len": 20,           # 硬裁剪上限（B站弹幕约 20 字）
    "forbidden_check": True,       # 内置敏感词过滤（回复黑名单同时生效）
    # 触发规则 + 限流（合并卡）
    "trigger": {
        "prefixEnabled": False,
        "prefixes": [],
        "keywordEnabled": False,
        "keywords": [],
        "allEnabled": False,
        "probability": 100,        # 触发概率 %
        "userBlacklist": [],       # 用户黑名单（昵称或 UID，命中则不触发 AI，不占限流）
    },
    "throttle": {
        "globalCool": 30,          # 全局冷却 s
        "userCool": 60,            # 同用户冷却 s
        "perMinute": 10,           # 每分钟上限
    },
    # 上下文
    "context": {
        "mode": "B",               # A 单条 / B 同用户小对话 / C 全局浅上下文
        "window": 4,               # B 模式会话条数（含 AI 回复，一条 user 或 assistant 各算一条）
        "globalWindow": 15,        # C 模式全局时间窗 s
        "expireMinutes": 5,        # B 模式会话过期时间
        "withUsername": True,      # 携带用户名
        "budget": 1200,            # 上下文总字符预算（含用户名；超出从最早的条目丢弃，最后一条保留）
        "maxInputLen": 100,        # 单条弹幕/消息输入截断上限（防御超长输入）
        "dedupeSeconds": 60,       # 同用户相同弹幕去重窗口 s（0 = 关闭；命中不触发不计数）
    },
    # 调用与并发（本地小模型适配）
    "call": {
        "concurrency": 1,          # 同时推理请求数（本地 CPU 模型建议 1）
        "timeout": 90,             # 请求读超时 s（连接超时取 min(10, timeout)）
        "retry": 1,                # 失败重试次数（仅网络错误/429/5xx；4xx 配置错误不重试）
        "keep_alive": 30,          # Ollama keep_alive 分钟（0 = 不发送该字段；仅 ollama 生效）
        "thinking": "auto",        # 思考模式：auto=本地自动关闭/云端跟随默认；on=强制开启；off=强制关闭
    },
    # 内容过滤
    "filter": {
        "triggerBlacklist": [],    # 命中则不触发不回复
        "replyBlacklist": [],      # AI 回复命中则不发送
    },
    # 按人设保存的自定义 System Prompt（persona key -> 文本；空值回落模板）
    "persona_prompts": {},
}

# 服务商预设（base_url 前缀匹配用于识别显示名；need_key=False 表示可不填 Key；
# local=True 表示本地运行时，前端显示"刷新模型列表"）
PROVIDER_PRESETS = {
    "openai": {"name": "OpenAI", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini", "need_key": True},
    "deepseek": {"name": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat", "need_key": True},
    "qwen": {"name": "通义千问", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus", "need_key": True},
    "kimi": {"name": "Kimi", "base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k", "need_key": True},
    "ollama": {"name": "Ollama 本地", "base_url": "http://127.0.0.1:11434/v1", "model": "qwen2.5:1.5b", "need_key": False, "local": True},
    "llamacpp": {"name": "llama.cpp", "base_url": "http://127.0.0.1:8080/v1", "model": "", "need_key": False, "local": True},
    "lmstudio": {"name": "LM Studio", "base_url": "http://127.0.0.1:1234/v1", "model": "", "need_key": False, "local": True},
}

# 人设模板：{target_words} 为期望字数占位
PERSONA_TEMPLATES = {
    "gentle": "你是直播间温柔陪伴型 AI 助手，语气温和体贴，像朋友一样关心观众。回复控制在 {n} 字以内，简洁口语化，不要长篇大论。",
    "geek": "你是直播间高冷技术宅 AI 助手，话不多但专业，偶尔冒出技术梗。回复控制在 {n} 字以内，简洁口语化，不要长篇大论。",
    "funny": "你是直播间沙雕搞笑 AI 助手，说话幽默爱玩梗，能自然接住弹幕的梗。回复控制在 {n} 字以内，简洁口语化，不要长篇大论。",
    "host": "你是直播间专业主持人 AI 助手，热情引导互动，回复观众并活跃气氛。回复控制在 {n} 字以内，简洁口语化，不要长篇大论。",
    "custom": "",  # 自定义：使用 system_prompt 原文
}

# 内置敏感词库（forbidden_check=True 时生效；弹幕场景通用违规内容，
# 与用户自定义 replyBlacklist 叠加；命中则回复不发送）
SENSITIVE_WORDS = [
    '加微信', '微信号', '微信聊', '加V', '加v', 'VX', 'vx', 'QQ群', 'qq群', 'Q群',
    '私聊', '私我', '加群', '扫码',
    '代练', '刷单', '兼职', '日结', '返利', '稳赚', '包赔',
    '博彩', '赌博', '下注', '开盘', '投注',
    '色情', '裸聊', '黄片', '办证', '发票', '洗钱', '诈骗', '贷款',
]
# 联系方式/敏感格式正则（命中即拦截）
SENSITIVE_PATTERNS = [
    re.compile(r'1[3-9]\d{9}'),                      # 手机号
    re.compile(r'[Qq][Qq][:：]?\s*\d{5,10}'),        # QQ 号
    re.compile(r'(?:微信|[Vv][Xx]|[Ww][Xx])[:：]?\s*[A-Za-z0-9_-]{4,20}'),  # 微信号
]


def _mask_key(value):
    """密钥脱敏：保留前3后3，中间 ***；短于6 返回 ******（与 main._mask_credential 一致）"""
    if not value:
        return ''
    s = str(value)
    if len(s) < 6:
        return '******'
    return s[:3] + '***' + s[-3:]


def _safe_slice(text, n):
    """按字符硬裁剪，避免切断在 emoji/组合字符中间（变体选择符/组合符/孤立代理回退）"""
    if len(text) <= n:
        return text
    s = text[:n]
    while s:
        last = s[-1]
        if ('\uFE00' <= last <= '\uFE0F') or ('\u20D0' <= last <= '\u20FF') or ('\uD800' <= last <= '\uDBFF'):
            s = s[:-1]
        else:
            break
    return s


class AiChatEngine:
    def __init__(self, config_file, sender_mgr=None, log_file=None):
        self.config_file = config_file
        self.sender_mgr = sender_mgr
        self.log_file = log_file
        self._lock = threading.RLock()
        self.config = dict(DEFAULT_AI_CONFIG)
        self._load_config()

        # 限流状态
        self._global_last_ts = 0.0
        self._user_last_map = {}          # uid -> ts
        self._minute_ts = deque()         # [(ts, ticket_id)]（每分钟上限，ticket 支持精确回退）
        self._ticket_seq = 0              # 限流占用自增序号
        self._throttle_lock = threading.Lock()

        # 上下文状态
        self._user_sessions = {}          # uid -> deque[(ts, role, username, text)]
        self._global_recent = deque(maxlen=200)
        self._ctx_lock = threading.Lock()

        # 同用户重复弹幕去重状态：uid -> (danmu, ts)
        self._dedupe_map = {}
        self._dedupe_lock = threading.Lock()

        # 并发槽位（本地模型保护）：_ai_busy 推理中、_ai_waiting 排队中
        self._ai_busy = 0
        self._ai_waiting = 0
        self._ai_cond = threading.Condition()

        # 交互日志（内存 ring buffer，最多 60 条；同时落盘 JSONL）
        self._logs = deque(maxlen=60)
        self._log_lock = threading.Lock()

        # 服务商状态缓存（/ai/status 60s 缓存）
        self._status_cache = {
            'ok': False,
            'checked': False,
            'last_check': 0.0,
            'latency_ms': 0,
        }
        self._status_lock = threading.Lock()

    # ------------------------------------------------------------------
    # 配置读写
    # ------------------------------------------------------------------
    def _load_config(self):
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    saved = json.load(f)
                if isinstance(saved, dict):
                    self._merge_config(self.config, saved)
        except Exception as e:
            print(f"[AI聊天] 读取配置失败: {e}")

    @staticmethod
    def _merge_config(target, src):
        """递归合并：只覆盖 src 中出现的键（合并写入语义，与 /savecookies 一致）"""
        for k, v in src.items():
            if isinstance(v, dict) and isinstance(target.get(k), dict):
                AiChatEngine._merge_config(target[k], v)
            else:
                target[k] = v

    def save_config(self, patch):
        """合并写入配置并落盘；api_key 含掩码 *** 时跳过该键"""
        with self._lock:
            patch = dict(patch or {})
            ak = patch.get('api_key')
            if isinstance(ak, str) and ('***' in ak):
                patch.pop('api_key', None)
            self._merge_config(self.config, patch)
            self._normalize_config()
            self._write_config()
        # 配置变更后令状态缓存失效（下次 /ai/status 即时探测）
        with self._status_lock:
            self._status_cache['checked'] = False
            self._status_cache['last_check'] = 0.0
        return True

    def _normalize_config(self):
        """字段类型/结构兜底，防止脏配置导致链路崩溃"""
        cfg = self.config
        cfg.setdefault('trigger', {})
        cfg.setdefault('throttle', {})
        cfg.setdefault('context', {})
        cfg.setdefault('filter', {})
        cfg.setdefault('call', {})
        # persona_prompts：按人设保存的自定义 System Prompt（旧配置的全局 system_prompt 迁移到当前人设）
        if not isinstance(cfg.get('persona_prompts'), dict):
            cfg['persona_prompts'] = {}
        if not cfg['persona_prompts'] and cfg.get('system_prompt'):
            cfg['persona_prompts'][str(cfg.get('persona') or 'host')] = str(cfg['system_prompt'])
        for key in ('prefixes', 'keywords', 'userBlacklist'):
            if not isinstance(cfg['trigger'].get(key), list):
                cfg['trigger'][key] = []
        for key in ('triggerBlacklist', 'replyBlacklist'):
            if not isinstance(cfg['filter'].get(key), list):
                cfg['filter'][key] = []
        try:
            cfg['throttle']['globalCool'] = max(0, int(cfg['throttle'].get('globalCool') or 0))
            cfg['throttle']['userCool'] = max(0, int(cfg['throttle'].get('userCool') or 0))
            cfg['throttle']['perMinute'] = max(1, int(cfg['throttle'].get('perMinute') or 1))
            cfg['trigger']['probability'] = min(100, max(0, int(cfg['trigger'].get('probability') or 100)))
            cfg['max_reply_len'] = max(1, int(cfg.get('max_reply_len') or 20))
            cfg['max_tokens'] = max(16, int(cfg.get('max_tokens') or 80))
            cfg['target_words'] = max(1, int(cfg.get('target_words') or 20))
            ctx = cfg['context']
            ctx['window'] = max(1, int(ctx.get('window') or 4))
            ctx['globalWindow'] = max(5, int(ctx.get('globalWindow') or 15))
            ctx['expireMinutes'] = max(1, int(ctx.get('expireMinutes') or 5))
            ctx['budget'] = max(200, int(ctx.get('budget') or 1200))
            ctx['maxInputLen'] = max(20, int(ctx.get('maxInputLen') or 100))
            ctx['dedupeSeconds'] = max(0, int(ctx.get('dedupeSeconds') or 0))
            call = cfg['call']
            call['concurrency'] = max(1, min(8, int(call.get('concurrency') or 1)))
            call['timeout'] = max(10, int(call.get('timeout') or 90))
            call['retry'] = max(0, min(3, int(call.get('retry') or 0)))
            call['keep_alive'] = max(0, int(call.get('keep_alive') or 0))
            t = str(call.get('thinking') or 'auto').lower()
            call['thinking'] = t if t in ('on', 'off') else 'auto'
        except (TypeError, ValueError):
            pass

    def _write_config(self):
        tmp = self.config_file + '.tmp'
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=4)
            os.replace(tmp, self.config_file)
        except Exception as e:
            print(f"[AI聊天] 保存配置失败: {e}")

    def get_config(self):
        """返回配置副本（api_key 脱敏）"""
        with self._lock:
            cfg = json.loads(json.dumps(self.config, ensure_ascii=False))
        cfg['api_key'] = _mask_key(cfg.get('api_key', ''))
        cfg['provider_name'] = self._provider_name(cfg.get('base_url', ''), cfg.get('provider', ''))
        return cfg

    def _snapshot(self):
        """返回完整配置深拷贝（供触发链路使用，避免热更新读一半）"""
        with self._lock:
            return json.loads(json.dumps(self.config, ensure_ascii=False))

    @staticmethod
    def _provider_name(base_url, provider_key):
        if provider_key in PROVIDER_PRESETS:
            return PROVIDER_PRESETS[provider_key]['name']
        base = (base_url or '').strip().rstrip('/').lower()
        for p in PROVIDER_PRESETS.values():
            if base == p['base_url'].rstrip('/').lower():
                return p['name']
        return '自定义'

    @staticmethod
    def _openai_base(base_url, provider):
        """归一化为 OpenAI 兼容端点：本地运行时（ollama/llamacpp/lmstudio）
        常填不带 /v1 的地址，自动补全"""
        b = str(base_url or '').strip().rstrip('/')
        if not b:
            return ''
        if AiChatEngine._is_local_provider(provider) and not b.endswith('/v1'):
            b = b + '/v1'
        return b

    @staticmethod
    def _is_local_provider(provider):
        """本地运行时：llama.cpp / LM Studio / Ollama（OpenAI 兼容端点）"""
        return str(provider or '').lower() in ('llamacpp', 'lmstudio', 'ollama')

    @staticmethod
    def _thinking_args(cfg):
        """思考模式请求参数：按 provider 与用户选择（call.thinking）附加。

        - auto（默认）：本地运行时（llamacpp/lmstudio/ollama）自动关闭思考——
          Qwen3 等模型思考会占满 max_tokens 导致 content 为空（弹幕场景推荐）；
          云端不附加（跟随服务商默认）。
        - on：强制开启思考。本地 llama.cpp 发 enable_thinking=true（实测有效）；
          云端按服务商附加已知字段，不支持的端点会忽略未知字段。
        - off：强制关闭。本地发 enable_thinking=false + reasoning_effort=none；
          云端仅对已知支持的服务商发送。
        """
        mode = str((cfg.get('call') or {}).get('thinking') or 'auto').lower()
        if mode not in ('on', 'off'):
            mode = 'auto'
        provider = str(cfg.get('provider') or '').lower()
        if provider in ('llamacpp', 'lmstudio', 'ollama'):
            # 本地运行时（llama.cpp 实测；Ollama/LM Studio 忽略未知字段，无害）
            if mode == 'on':
                return {'chat_template_kwargs': {'enable_thinking': True}}
            return {'chat_template_kwargs': {'enable_thinking': False}, 'reasoning_effort': 'none'}
        # 云端：按服务商附加已知字段（未知字段多数端点忽略；不支持则建议回 auto）
        if mode == 'on':
            if provider in ('openai', 'deepseek'):
                return {'reasoning_effort': 'medium'}
            if provider == 'qwen':
                return {'enable_thinking': True}
            if provider == 'kimi':
                return {'thinking': {'type': 'enabled'}}
            return {}
        if mode == 'off':
            if provider == 'qwen':
                return {'enable_thinking': False}
            return {}
        return {}

    def _build_system_prompt(self, cfg):
        persona = str(cfg.get('persona') or 'host')
        n = cfg.get('target_words', 20)
        pp = cfg.get('persona_prompts') or {}
        # 当前人设的自定义 SP 优先；无则兼容旧全局字段；再无则回落人设模板
        sp = str(pp.get(persona) or '').strip()
        if not sp:
            sp = str(cfg.get('system_prompt') or '').strip()
        if sp:
            return self._inject_date(sp)
        if persona == 'custom':
            return ''
        tpl = PERSONA_TEMPLATES.get(persona)
        if tpl is None:
            tpl = PERSONA_TEMPLATES['host']
        return self._inject_date(tpl.format(n=n))

    @staticmethod
    def _inject_date(sp):
        """小模型没有日期知识（否则"今天星期几"只会回显/乱猜）：
        在 system prompt 开头注入当前日期与星期；已含"今天是"则跳过。"""
        sp = (sp or '').strip()
        if not sp or '今天是' in sp[:30]:
            return sp
        weeks = ('一', '二', '三', '四', '五', '六', '日')
        prefix = '今天是 %d年%d月%d日，星期%s。' % (
            datetime.now().year, datetime.now().month, datetime.now().day,
            weeks[datetime.now().weekday()])
        return prefix + sp

    # ------------------------------------------------------------------
    # AI 调用（OpenAI 兼容 /chat/completions，requests 直调）
    # 返回 (content, usage)；支持可配超时/重试；ollama 附加 keep_alive
    # ------------------------------------------------------------------
    def _call_ai(self, cfg, messages):
        base_url = self._openai_base(cfg.get('base_url'), cfg.get('provider'))
        if not base_url:
            raise RuntimeError('未配置 API Base URL')
        url = base_url + '/chat/completions'
        headers = {'Content-Type': 'application/json'}
        api_key = str(cfg.get('api_key') or '').strip()
        if api_key:
            headers['Authorization'] = 'Bearer ' + api_key
        call = cfg.get('call') or {}
        timeout = max(10, int(call.get('timeout') or 90))
        payload = {
            'model': str(cfg.get('model') or '').strip() or 'gpt-4o-mini',
            'messages': messages,
            'temperature': float(cfg.get('temperature') or 0.7),
            'max_tokens': int(cfg.get('max_tokens') or 80),
        }
        payload.update(self._thinking_args(cfg))
        # Ollama 支持请求体 keep_alive（分钟字符串）；llama.cpp/LM Studio 会忽略未知字段，
        # 但为稳妥仅对 ollama 发送
        if str(cfg.get('provider') or '') == 'ollama':
            ka = int(call.get('keep_alive') or 0)
            if ka > 0:
                payload['keep_alive'] = f'{ka}m'
        retry = max(0, int(call.get('retry') or 0))
        last_err = None
        for attempt in range(retry + 1):
            try:
                resp = requests.post(url, headers=headers, json=payload,
                                     timeout=(min(10, timeout), timeout))
            except requests.RequestException as e:
                last_err = RuntimeError(f'请求失败: {e}')
                continue  # 网络错误可重试
            if resp.status_code >= 400:
                body = resp.text[:200]
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_err = RuntimeError(f'API 返回 {resp.status_code}: {body}')
                    continue  # 限流/服务端错误可重试
                raise RuntimeError(f'API 返回 {resp.status_code}: {body}')  # 4xx 配置错误不重试
            data = resp.json()
            try:
                content = data['choices'][0]['message']['content']
            except (KeyError, IndexError, TypeError):
                raise RuntimeError('API 响应缺少 choices/message/content 字段')
            usage = data.get('usage') or {}
            return str(content or '').strip(), usage
        raise last_err or RuntimeError('AI 调用失败')

    def _call_ai_stream(self, cfg, messages, usage_box=None):
        """OpenAI 兼容流式调用（stream=True）：yield 内容增量（逐段 text）。

        - 调用方自行累积 full_text（每个 yield 的 piece 累加即可）
        - usage_box（可选 dict）：流结束前写入 usage 统计（部分服务端在末尾 chunk 携带）
        - 仅对"首次请求失败"重试（网络错误/429/5xx）；流中途断开不重试（已有部分输出）
        - 服务端不支持流式（400）时抛出 RuntimeError，调用方可回退非流式
        """
        base_url = self._openai_base(cfg.get('base_url'), cfg.get('provider'))
        if not base_url:
            raise RuntimeError('未配置 API Base URL')
        url = base_url + '/chat/completions'
        headers = {'Content-Type': 'application/json'}
        api_key = str(cfg.get('api_key') or '').strip()
        if api_key:
            headers['Authorization'] = 'Bearer ' + api_key
        call = cfg.get('call') or {}
        timeout = max(10, int(call.get('timeout') or 90))
        payload = {
            'model': str(cfg.get('model') or '').strip() or 'gpt-4o-mini',
            'messages': messages,
            'temperature': float(cfg.get('temperature') or 0.7),
            'max_tokens': int(cfg.get('max_tokens') or 80),
            'stream': True,
        }
        payload.update(self._thinking_args(cfg))
        if str(cfg.get('provider') or '') == 'ollama':
            ka = int(call.get('keep_alive') or 0)
            if ka > 0:
                payload['keep_alive'] = f'{ka}m'
        retry = max(0, int(call.get('retry') or 0))
        last_err = None
        for attempt in range(retry + 1):
            try:
                resp = requests.post(url, headers=headers, json=payload, stream=True,
                                     timeout=(min(10, timeout), timeout))
            except requests.RequestException as e:
                last_err = RuntimeError(f'请求失败: {e}')
                continue  # 网络错误可重试
            if resp.status_code >= 400:
                body = resp.text[:200]
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_err = RuntimeError(f'API 返回 {resp.status_code}: {body}')
                    continue
                raise RuntimeError(f'API 返回 {resp.status_code}: {body}')  # 4xx 不重试（含不支持流式）
            # 流式解析 SSE：data: {json} ... data: [DONE]
            # 关键：llama.cpp 等 SSE 响应头常不带 charset，requests 默认按 ISO-8859-1
            # 解码会把 UTF-8 中文变成乱码（æ_Žä¹...），必须显式指定 UTF-8。
            resp.encoding = 'utf-8'
            for line in resp.iter_lines(decode_unicode=True):
                if not line:
                    continue
                line = line.strip()
                if not line.startswith('data:'):
                    continue
                data = line[len('data:'):].strip()
                if data == '[DONE]':
                    return
                try:
                    obj = json.loads(data)
                except Exception:
                    continue
                try:
                    choice = obj['choices'][0]
                    # 兼容两种字段：delta（流式）/ message（个别实现整段下发）
                    delta = choice.get('delta') or choice.get('message') or {}
                    piece = delta.get('content')
                except (KeyError, IndexError, TypeError):
                    piece = None
                if piece:
                    yield str(piece)
                u = obj.get('usage')
                if isinstance(u, dict) and u and usage_box is not None:
                    usage_box.update(u)
            return
        raise last_err or RuntimeError('AI 调用失败')

    # ------------------------------------------------------------------
    # 服务商状态（60s 缓存；配置变更后失效）
    # ------------------------------------------------------------------
    def check_status(self, force=False):
        now = time.time()
        with self._status_lock:
            cached = dict(self._status_cache)
        if not force and cached['checked'] and now - cached['last_check'] < 60:
            return cached

        cfg = self._snapshot()
        result = {'ok': False, 'checked': True, 'last_check': now, 'latency_ms': 0}
        base_url = self._openai_base(cfg.get('base_url'), cfg.get('provider'))
        if not cfg.get('enabled') or not base_url:
            with self._status_lock:
                self._status_cache = result
            return result
        try:
            headers = {}
            api_key = str(cfg.get('api_key') or '').strip()
            if api_key:
                headers['Authorization'] = 'Bearer ' + api_key
            t0 = time.time()
            r = requests.get(base_url.rstrip('/') + '/models', headers=headers, timeout=5)
            result['latency_ms'] = int((time.time() - t0) * 1000)
            result['ok'] = r.status_code < 400
        except Exception:
            result['ok'] = False
        with self._status_lock:
            self._status_cache = result
        return result

    # ------------------------------------------------------------------
    # 模型列表（多运行时）：Ollama /api/tags；llama.cpp / LM Studio / 云端 /models
    # ------------------------------------------------------------------
    def list_models(self):
        cfg = self._snapshot()
        provider = str(cfg.get('provider') or '')
        base_url = str(cfg.get('base_url') or '').strip().rstrip('/')
        if not base_url:
            return {'ok': False, 'error': '未配置 API Base URL', 'models': []}
        headers = {}
        api_key = str(cfg.get('api_key') or '').strip()
        if api_key:
            headers['Authorization'] = 'Bearer ' + api_key
        try:
            if provider == 'ollama':
                # Ollama 原生接口 /api/tags（base_url 可能带 /v1，需还原到 host 根）
                host = re.sub(r'/v1$', '', base_url)
                r = requests.get(host.rstrip('/') + '/api/tags', headers=headers, timeout=5)
                if r.status_code >= 400:
                    return {'ok': False, 'error': f'HTTP {r.status_code}: {r.text[:120]}', 'models': []}
                data = r.json()
                models = [str(m.get('name') or '') for m in (data.get('models') or []) if m.get('name')]
                return {'ok': True, 'models': models}
            # 其余运行时统一走 OpenAI 兼容 /models
            api_base = self._openai_base(base_url, provider)
            r = requests.get(api_base.rstrip('/') + '/models', headers=headers, timeout=5)
            if r.status_code >= 400:
                return {'ok': False, 'error': f'HTTP {r.status_code}: {r.text[:120]}', 'models': []}
            data = r.json()
            items = data.get('data') or []
            models = [str(m.get('id') or '') for m in items if m.get('id')]
            if not models and isinstance(data.get('models'), list):
                models = [str(m.get('name') or '') for m in data['models'] if m.get('name')]
            return {'ok': True, 'models': models}
        except Exception as e:
            return {'ok': False, 'error': str(e), 'models': []}

    # ------------------------------------------------------------------
    # 并发槽位（本地模型保护：同时推理数 + 排队上限，超出丢弃）
    # ------------------------------------------------------------------
    def _acquire_slot(self, concurrency):
        """获取推理槽位。返回 True 表示获得；False 表示排队超限/等待超时被丢弃。"""
        limit = max(1, int(concurrency or 1))
        queue_cap = limit * 3
        with self._ai_cond:
            if self._ai_busy >= limit:
                if self._ai_waiting >= queue_cap:
                    return False
                self._ai_waiting += 1
                try:
                    end = time.time() + 30.0  # 排队等待上限 30s
                    while self._ai_busy >= limit:
                        remain = end - time.time()
                        if remain <= 0:
                            return False
                        self._ai_cond.wait(remain)
                finally:
                    self._ai_waiting -= 1
            self._ai_busy += 1
            return True

    def _release_slot(self):
        with self._ai_cond:
            self._ai_busy = max(0, self._ai_busy - 1)
            self._ai_cond.notify_all()

    # ------------------------------------------------------------------
    # 弹幕触发链路（回调线程内轻量执行；AI 调用放后台线程）
    # ------------------------------------------------------------------
    def _hit_blacklist(self, cfg, text):
        bl = cfg.get('filter', {}).get('triggerBlacklist') or []
        for kw in bl:
            kw = str(kw or '').strip()
            if kw and kw in text:
                return kw
        return None

    def _hit_user_blacklist(self, cfg, username, uid):
        """用户黑名单：昵称或 UID 精确命中即拦截（命中不触发、不占限流）"""
        bl = cfg.get('trigger', {}).get('userBlacklist') or []
        uid_s = str(uid or '').strip()
        for name in bl:
            name = str(name or '').strip()
            if not name:
                continue
            if (username and name == username) or (uid_s and name == uid_s):
                return name
        return None

    def _match_trigger(self, cfg, danmu):
        """触发判定：@前缀 / 关键词 / 全量，或关系。返回 (hit, kind, payload)"""
        trig = cfg.get('trigger', {})
        if trig.get('prefixEnabled'):
            prefixes = [str(p).strip() for p in (trig.get('prefixes') or []) if str(p).strip()]
            for p in prefixes:
                if danmu.startswith(p):
                    return True, 'prefix', danmu[len(p):].strip() or danmu
        if trig.get('keywordEnabled'):
            keywords = [str(k).strip() for k in (trig.get('keywords') or []) if str(k).strip()]
            for k in keywords:
                if k and k in danmu:
                    return True, 'keyword', danmu
        if trig.get('allEnabled'):
            return True, 'all', danmu
        return False, None, danmu

    def _dedupe_check(self, uid, danmu, dedupe_secs):
        """同用户 N 秒内相同弹幕去重。返回 True=放行（非重复），False=重复拦截。
        在触发判定命中后、概率/限流前调用：重复刷屏不触发、不占限流、不污染上下文。
        窗口 0 表示关闭去重。"""
        if dedupe_secs <= 0:
            return True
        now = time.time()
        with self._dedupe_lock:
            last = self._dedupe_map.get(uid)
            if last and last[0] == danmu and now - last[1] < dedupe_secs:
                return False
            self._dedupe_map[uid] = (danmu, now)
            # 惰性清理：超过 500 条时清理过期记录，防止长期直播内存膨胀
            if len(self._dedupe_map) > 500:
                expired = [k for k, v in self._dedupe_map.items() if now - v[1] > dedupe_secs]
                for k in expired:
                    self._dedupe_map.pop(k, None)
            return True

    def _throttle_ok(self, cfg, uid):
        """限流判定（通过即占用并返回 ticket，失败方用 ticket 精确回退）。
        返回 (ok, reason, ticket)"""
        thr = cfg.get('throttle', {})
        global_cool = int(thr.get('globalCool') or 0)
        user_cool = int(thr.get('userCool') or 0)
        per_minute = int(thr.get('perMinute') or 0)
        now = time.time()
        with self._throttle_lock:
            # 全局冷却
            if global_cool > 0 and now - self._global_last_ts < global_cool:
                return False, f'全局冷却 {global_cool}s', None
            # 同用户冷却
            if user_cool > 0 and uid in self._user_last_map and now - self._user_last_map.get(uid, 0) < user_cool:
                return False, f'同用户冷却 {user_cool}s', None
            # 每分钟上限：惰性清理 60s 前的记录
            if per_minute > 0:
                while self._minute_ts and now - self._minute_ts[0][0] >= 60:
                    self._minute_ts.popleft()
                if len(self._minute_ts) >= per_minute:
                    return False, f'每分钟上限 {per_minute} 条', None
            # 占用限流（AI 失败时按 ticket 精确回退）
            self._ticket_seq += 1
            ticket = {
                'seq': self._ticket_seq,
                'global_ts': now,
                'user_ts': now,
                'uid': uid,
            }
            self._global_last_ts = now
            if uid:
                self._user_last_map[uid] = now
            if per_minute > 0:
                self._minute_ts.append((now, self._ticket_seq))
            return True, '', ticket

    def _throttle_undo(self, ticket):
        """AI 调用失败时按 ticket 精确回退限流占用（多线程并发下不会误退他人占用）"""
        if not ticket:
            return
        with self._throttle_lock:
            if self._global_last_ts == ticket.get('global_ts'):
                self._global_last_ts = 0.0
            uid = ticket.get('uid')
            if uid and self._user_last_map.get(uid) == ticket.get('user_ts'):
                self._user_last_map.pop(uid, None)
            # 按 seq 精确移除每分钟记录
            seq = ticket.get('seq')
            kept = deque()
            for ts, s in self._minute_ts:
                if s != seq:
                    kept.append((ts, s))
            self._minute_ts = kept
            now = time.time()
            while self._minute_ts and now - self._minute_ts[0][0] >= 60:
                self._minute_ts.popleft()

    def _apply_budget(self, messages, cfg):
        """上下文字符预算：单条超长截尾（保留尾部）；总量超预算从最早的条目丢弃，
        始终保留 system 与最后一条（当前触发弹幕）。"""
        ctx = cfg.get('context') or {}
        budget = max(200, int(ctx.get('budget') or 1200))
        max_input_len = max(20, int(ctx.get('maxInputLen') or 100))
        if not messages:
            return messages
        # 1) 单条消息截尾（保留尾部=最近的文本）
        for m in messages[1:]:
            c = m.get('content')
            if isinstance(c, str) and len(c) > max_input_len:
                m['content'] = c[-max_input_len:]
        # 2) 总预算：从最老的非 system 消息开始丢
        total = lambda ms: sum(len(str(m.get('content') or '')) for m in ms)
        ms = messages
        while len(ms) > 2 and total(ms) > budget:
            ms = [ms[0]] + ms[2:]  # 丢弃 ms[1]（最老条目）
        # 3) 仍超：最后一条（当前触发）截尾
        if total(ms) > budget and len(ms) > 1:
            last = ms[-1]
            if isinstance(last.get('content'), str):
                remain = max(1, budget - len(str(ms[0].get('content') or '')))
                last['content'] = last['content'][-remain:]
        return ms

    def _build_messages(self, cfg, uid, username, payload, danmu):
        """上下文三档组装：A 单条 / B 同用户小对话（含 AI 回复记忆）/ C 全局浅上下文。
        统一做字符预算截断。返回 OpenAI messages 数组。"""
        system = self._build_system_prompt(cfg)
        ctx = cfg.get('context', {})
        mode = str(ctx.get('mode') or 'B').upper()
        with_user = bool(ctx.get('withUsername', True))
        exp_min = int(ctx.get('expireMinutes') or 5) * 60
        now = time.time()

        if mode == 'A':
            user_text = f'弹幕：{payload}' if with_user and username else payload
            return self._apply_budget([
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': user_text},
            ], cfg)

        if mode == 'C':
            with self._ctx_lock:
                # 全局浅上下文：最近 globalWindow 秒内的弹幕（不含当前这条）
                recent = [(ts, u, d) for ts, u, d in self._global_recent
                          if now - ts <= int(ctx.get('globalWindow') or 15)]
                self._global_recent.append((now, username, danmu))
            lines = []
            for ts, u, d in recent:
                if with_user:
                    lines.append(f'{u}说：{d}')
                else:
                    lines.append(d)
            ctx_text = '\n'.join(lines)
            current = f'{username}说：{danmu}' if with_user else danmu
            if ctx_text:
                user_text = f'以下是直播间最近弹幕：\n{ctx_text}\n\n请只回复最新这条弹幕：{current}'
            else:
                user_text = f'弹幕：{current}'
            return self._apply_budget([
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': user_text},
            ], cfg)

        # mode B：同用户小对话（默认；会话含 user 与 assistant，形成真对话）
        with self._ctx_lock:
            sess = self._user_sessions.get(uid)
            if sess is None:
                sess = deque(maxlen=int(ctx.get('window') or 4))
                self._user_sessions[uid] = sess
            elif sess and now - sess[-1][0] > exp_min:
                sess.clear()  # 会话过期
            # 追加当前触发弹幕（role=user）
            sess.append((now, 'user', username, danmu))
            history = list(sess)
            # 惰性清理过期会话
            expired = [k for k, v in self._user_sessions.items() if v and now - v[-1][0] > exp_min]
            for k in expired:
                self._user_sessions.pop(k, None)
        messages = [{'role': 'system', 'content': system}]
        for ts, role, u, d in history:
            if role == 'assistant':
                messages.append({'role': 'assistant', 'content': d})
            else:
                text = f'{u}说：{d}' if with_user and u else d
                messages.append({'role': 'user', 'content': text})
        return self._apply_budget(messages, cfg)

    def _record_assistant_reply(self, uid, reply):
        """把 AI 回复写回该用户的会话（B 模式对话记忆；A/C 模式无会话则忽略）"""
        if not reply:
            return
        try:
            with self._ctx_lock:
                sess = self._user_sessions.get(uid)
                if sess is not None:
                    sess.append((time.time(), 'assistant', '', reply))
        except Exception:
            pass

    @staticmethod
    def _process_reply(cfg, raw, danmu):
        """回复处理：清洗引号/空白 → 内置敏感过滤 → 回复黑名单 → 硬裁剪（组合字符安全）"""
        text = (raw or '').strip()
        if not text:
            # 模型返回空内容：与"命中过滤"区分，避免空串进入发送与记忆
            return None, 'AI 返回内容为空'
        # 去掉 AI 常输出的包裹引号
        if len(text) >= 2 and text[0] in '“"「『' and text[-1] in '”"」』':
            text = text[1:-1].strip()
        # 折叠多余空白
        text = re.sub(r'\s+', ' ', text)
        # 内置敏感过滤（forbidden_check）：命中则不发送
        if cfg.get('forbidden_check', True):
            for kw in SENSITIVE_WORDS:
                if kw in text:
                    return None, f'命中内置敏感词 {kw!r}'
            for pat in SENSITIVE_PATTERNS:
                m = pat.search(text)
                if m:
                    return None, f'命中敏感格式 {m.group(0)[:10]}'
        # 回复黑名单
        rbl = cfg.get('filter', {}).get('replyBlacklist') or []
        for kw in rbl:
            kw = str(kw or '').strip()
            if kw and kw in text:
                return None, f'命中回复过滤 {kw!r}'
        # 硬裁剪（按字符，避免切断 emoji/组合字符）
        max_len = int(cfg.get('max_reply_len') or 20)
        if len(text) > max_len:
            text = _safe_slice(text, max_len)
        return text, ''

    def _add_log(self, entry):
        with self._log_lock:
            self._logs.append(entry)
        if self.log_file:
            try:
                with open(self.log_file, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + '\n')
            except Exception:
                pass

    def get_logs(self):
        with self._log_lock:
            return list(self._logs)

    # ------------------------------------------------------------------
    # 完整链路（后台线程执行：并发槽位 → 调AI → 回复处理 → 发送 → 日志）
    # ------------------------------------------------------------------
    def _run_ai_reply(self, cfg, uid, username, danmu, payload, trigger_kind, ticket=None, skip_throttle=False):
        """在独立线程中执行 AI 回复完整链路；限流在调用方已判定/占用（ticket 回退），
        skip_throttle 表示手动测试（/ai/send）不走概率与限流。
        并发槽位对自动触发与手动测试同样生效（保护本地模型不被并发打爆）。
        """
        call = cfg.get('call') or {}
        concurrency = max(1, int(call.get('concurrency') or 1))
        if not self._acquire_slot(concurrency):
            if not skip_throttle and ticket is not None:
                self._throttle_undo(ticket)
            self._add_log({
                'time': time.strftime('%H:%M:%S'), 'username': username, 'danmu': danmu,
                'reply': '', 'trigger': trigger_kind, 'filtered': False,
                'note': '并发繁忙（推理队列已满），已丢弃',
            })
            return {'ok': False, 'reply': '', 'filtered': False,
                    'note': '并发繁忙（推理队列已满），已丢弃', 'latency_ms': 0}
        try:
            messages = self._build_messages(cfg, uid, username, payload, danmu)
            t0 = time.time()
            raw, usage = self._call_ai(cfg, messages)
            latency_ms = int((time.time() - t0) * 1000)
            reply, filter_note = self._process_reply(cfg, raw, danmu)
            if reply is None:
                self._add_log({
                    'time': time.strftime('%H:%M:%S'), 'username': username, 'danmu': danmu,
                    'reply': '', 'trigger': trigger_kind, 'filtered': True, 'note': filter_note,
                })
                return {'ok': False, 'reply': '', 'filtered': True, 'note': filter_note, 'latency_ms': latency_ms}
            # 回写 AI 回复到用户会话（B 模式对话记忆；空回复不回写，避免污染上下文）
            if reply:
                self._record_assistant_reply(uid, reply)
            # 发送：复用 SenderManager.send_once（内部 _run_async_via_thread 包 sync）
            sent_ok, send_msg = False, 'sender_mgr 未注入'
            if self.sender_mgr:
                try:
                    sent_ok, send_msg = self.sender_mgr.send_once(reply)
                except Exception as e:
                    sent_ok, send_msg = False, str(e)
            usage_info = ''
            if isinstance(usage, dict) and usage:
                pt = usage.get('prompt_tokens') or usage.get('promptTokens') or 0
                ct = usage.get('completion_tokens') or usage.get('completionTokens') or 0
                if pt or ct:
                    usage_info = f'↑{pt} ↓{ct}'
            self._add_log({
                'time': time.strftime('%H:%M:%S'), 'username': username, 'danmu': danmu,
                'reply': reply, 'trigger': trigger_kind, 'filtered': False,
                'sent_ok': sent_ok, 'note': '' if sent_ok else send_msg,
                'usage': usage_info, 'latency_ms': latency_ms,
            })
            return {'ok': True, 'reply': reply, 'filtered': False, 'sent_ok': sent_ok,
                    'note': '' if sent_ok else send_msg, 'latency_ms': latency_ms,
                    'usage': usage_info}
        except Exception as e:
            if not skip_throttle and ticket is not None:
                self._throttle_undo(ticket)
            self._add_log({
                'time': time.strftime('%H:%M:%S'), 'username': username, 'danmu': danmu,
                'reply': '', 'trigger': trigger_kind, 'filtered': False, 'note': f'AI 调用失败: {e}',
            })
            return {'ok': False, 'reply': '', 'filtered': False, 'note': str(e), 'latency_ms': 0}
        finally:
            self._release_slot()

    def handle_danmaku(self, msg_dict):
        """弹幕回调入口：回调线程内轻量判定，AI 调用放后台线程。

        返回 True 表示已进入链路（判定通过），False 表示未触发/被过滤。
        """
        try:
            cfg = self._snapshot()
            if not cfg.get('enabled'):
                return False
            danmu = (msg_dict.get('danmu') or '').strip()
            if not danmu:
                return False
            username = msg_dict.get('username') or ''
            uid = msg_dict.get('uid') or username or 'anonymous'

            # 0) 用户黑名单（昵称/UID 命中直接不进入链路，不触发不占限流）
            hit_user = self._hit_user_blacklist(cfg, username, uid)
            if hit_user:
                self._add_log({
                    'time': time.strftime('%H:%M:%S'), 'username': username, 'danmu': danmu,
                    'reply': '', 'trigger': 'user_blacklist', 'filtered': True, 'note': f'用户黑名单 {hit_user!r}',
                })
                return False

            # 1) 触发黑名单
            bl_kw = self._hit_blacklist(cfg, danmu)
            if bl_kw:
                self._add_log({
                    'time': time.strftime('%H:%M:%S'), 'username': username, 'danmu': danmu,
                    'reply': '', 'trigger': 'blacklist', 'filtered': True, 'note': f'触发黑名单 {bl_kw!r}',
                })
                return False

            # 2) 触发判定（@前缀/关键词/全量，或关系）
            hit, kind, payload = self._match_trigger(cfg, danmu)
            if not hit:
                return False

            # 2.5) 同用户重复弹幕去重（命中触发后才判定，不占限流/概率）
            dedupe_secs = int((cfg.get('context') or {}).get('dedupeSeconds') or 0)
            if not self._dedupe_check(uid, danmu, dedupe_secs):
                self._add_log({
                    'time': time.strftime('%H:%M:%S'), 'username': username, 'danmu': danmu,
                    'reply': '', 'trigger': kind, 'filtered': True,
                    'note': f'重复弹幕去重（同用户 {dedupe_secs}s 内同内容）',
                })
                return False

            # 3) 概率
            prob = int(cfg.get('trigger', {}).get('probability') or 100)
            if random.randint(1, 100) > prob:
                return False

            # 4) 限流（通过即占用并返回 ticket，失败可精确回退）
            ok, reason, ticket = self._throttle_ok(cfg, uid)
            if not ok:
                self._add_log({
                    'time': time.strftime('%H:%M:%S'), 'username': username, 'danmu': danmu,
                    'reply': '', 'trigger': kind, 'filtered': True, 'note': f'限流拦截（{reason}）',
                })
                return False

            # 5) 上下文组装 + 独立线程调 AI → 发送 → 日志
            t = threading.Thread(
                target=self._run_ai_reply,
                args=(cfg, uid, username, danmu, payload, kind, ticket, False),
                daemon=True,
                name='ai-reply-thread',
            )
            t.start()
            return True
        except Exception as e:
            print(f"[AI聊天] handle_danmaku 异常: {e}")
            return False

    # ------------------------------------------------------------------
    # 接口支撑：连通性测试 / 手动发送（测试区）
    # ------------------------------------------------------------------
    def test_connection(self, mode='ping', message=''):
        """/ai/test：ping 探测 models；chat 生成一条测试回复（不走触发/限流）"""
        cfg = self._snapshot()
        base_url = self._openai_base(cfg.get('base_url'), cfg.get('provider'))
        if not base_url:
            return {'ok': False, 'provider': self._provider_name(base_url, cfg.get('provider', '')),
                    'error': '未配置 API Base URL'}
        try:
            if mode == 'ping' or not message:
                t0 = time.time()
                headers = {}
                api_key = str(cfg.get('api_key') or '').strip()
                if api_key:
                    headers['Authorization'] = 'Bearer ' + api_key
                r = requests.get(base_url.rstrip('/') + '/models', headers=headers, timeout=5)
                latency_ms = int((time.time() - t0) * 1000)
                return {'ok': r.status_code < 400,
                        'provider': self._provider_name(base_url, cfg.get('provider', '')),
                        'latency_ms': latency_ms,
                        'error': '' if r.status_code < 400 else f'HTTP {r.status_code}'}
            system = self._build_system_prompt(cfg)
            t0 = time.time()
            raw, _usage = self._call_ai(cfg, [
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': str(message)[:200]},
            ])
            latency_ms = int((time.time() - t0) * 1000)
            reply, note = self._process_reply(cfg, raw, str(message))
            return {'ok': reply is not None, 'provider': self._provider_name(base_url, cfg.get('provider', '')),
                    'reply': reply or '', 'latency_ms': latency_ms,
                    'error': note if reply is None else ''}
        except Exception as e:
            return {'ok': False, 'provider': self._provider_name(base_url, cfg.get('provider', '')),
                    'reply': '', 'latency_ms': 0, 'error': str(e)}

    def send_test(self, message, username='测试用户', uid=None):
        """/ai/send：手动触发一条回复（测试用）。

        走完整链路（AI调用→回复处理→发送→日志），跳过触发规则/概率/限流，
        保留触发黑名单过滤；并发槽位仍生效（避免测试挤占本地模型）。
        返回链路结果供前端测试区展示。
        """
        cfg = self._snapshot()
        danmu = str(message or '').strip()
        if not danmu:
            return {'ok': False, 'error': '消息不能为空'}
        uid = uid or username or 'manual'
        bl_kw = self._hit_blacklist(cfg, danmu)
        if bl_kw:
            self._add_log({
                'time': time.strftime('%H:%M:%S'), 'username': username, 'danmu': danmu,
                'reply': '', 'trigger': 'manual', 'filtered': True, 'note': f'触发黑名单 {bl_kw!r}',
            })
            return {'ok': False, 'reply': '', 'filtered': True, 'note': f'触发黑名单 {bl_kw!r}'}
        payload = danmu
        kind = 'manual'
        result = self._run_ai_reply(cfg, uid, username, danmu, payload, kind, ticket=None, skip_throttle=True)
        result['ok'] = result.get('ok', False) and not result.get('filtered', False)
        return result

    def stream_test(self, message, username='测试用户', uid=None):
        """/ai/stream：流式测试回复（SSE 事件生成器，供测试区打字机展示）。

        逐段 yield dict 事件：{'event': 'delta', 'text': ...} → {'event': 'done', ...}
        或 {'event': 'error', 'message': ...}。跳过触发规则/概率/限流，保留触发
        黑名单过滤；并发槽位仍生效；服务端不支持流式时自动回退非流式整段输出。
        """
        cfg = self._snapshot()
        danmu = str(message or '').strip()
        if not danmu:
            yield {'event': 'error', 'message': '消息不能为空'}
            return
        uid = uid or username or 'manual'
        bl_kw = self._hit_blacklist(cfg, danmu)
        if bl_kw:
            self._add_log({
                'time': time.strftime('%H:%M:%S'), 'username': username, 'danmu': danmu,
                'reply': '', 'trigger': 'manual', 'filtered': True, 'note': f'触发黑名单 {bl_kw!r}',
            })
            yield {'event': 'done', 'ok': False, 'reply': '', 'filtered': True,
                   'note': f'触发黑名单 {bl_kw!r}', 'latency_ms': 0}
            return
        call = cfg.get('call') or {}
        concurrency = max(1, int(call.get('concurrency') or 1))
        if not self._acquire_slot(concurrency):
            yield {'event': 'error', 'message': '并发繁忙（推理队列已满），请稍后重试'}
            return
        try:
            messages = self._build_messages(cfg, uid, username, danmu, danmu)
            t0 = time.time()
            full = ''
            usage_box = {}
            streamed = False
            try:
                for piece in self._call_ai_stream(cfg, messages, usage_box):
                    if piece:
                        full += piece
                        streamed = True
                        yield {'event': 'delta', 'text': piece}
            except RuntimeError as e:
                # 流式失败（如服务端不支持）：回退非流式整段输出
                if not full:
                    raw, usage_box = self._call_ai(cfg, messages)
                    full = str(raw or '')
                    if full:
                        streamed = True
                        yield {'event': 'delta', 'text': full}
                else:
                    # 已输出部分内容后失败：补一个 error 提示但保留已输出
                    yield {'event': 'delta', 'text': f'\n[生成中断: {e}]'}
            latency_ms = int((time.time() - t0) * 1000)
            reply, filter_note = self._process_reply(cfg, full, danmu)
            if reply is None:
                self._add_log({
                    'time': time.strftime('%H:%M:%S'), 'username': username, 'danmu': danmu,
                    'reply': full, 'trigger': 'manual', 'filtered': True, 'note': filter_note,
                })
                yield {'event': 'done', 'ok': False, 'reply': '', 'filtered': True,
                       'note': filter_note, 'latency_ms': latency_ms}
                return
            # 回写 AI 回复到用户会话（B 模式对话记忆；空回复不回写）
            if reply:
                self._record_assistant_reply(uid, reply)
            sent_ok, send_msg = False, 'sender_mgr 未注入'
            if self.sender_mgr:
                try:
                    sent_ok, send_msg = self.sender_mgr.send_once(reply)
                except Exception as e:
                    sent_ok, send_msg = False, str(e)
            usage_info = ''
            if usage_box:
                pt = usage_box.get('prompt_tokens') or usage_box.get('promptTokens') or 0
                ct = usage_box.get('completion_tokens') or usage_box.get('completionTokens') or 0
                if pt or ct:
                    usage_info = f'↑{pt} ↓{ct}'
            self._add_log({
                'time': time.strftime('%H:%M:%S'), 'username': username, 'danmu': danmu,
                'reply': reply, 'trigger': 'manual', 'filtered': False,
                'sent_ok': sent_ok, 'note': '' if sent_ok else send_msg,
                'usage': usage_info, 'latency_ms': latency_ms,
            })
            yield {'event': 'done', 'ok': True, 'reply': reply, 'filtered': False,
                   'sent_ok': sent_ok, 'note': '' if sent_ok else send_msg,
                   'usage': usage_info, 'latency_ms': latency_ms}
        except Exception as e:
            yield {'event': 'error', 'message': str(e)}
        finally:
            self._release_slot()
