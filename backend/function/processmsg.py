# -*- coding: utf-8 -*-
"""
ProcessMsg：弹幕消息统一业务处理（重写版）

修复项：
  B2  点歌/切歌/礼物分支 pass -> 完整分发
  B3  实例不互通 -> 共享 all_queue_dict 引用 + 内部 QueueList/MusicList 直接操作同一列表
  B10 点歌 split(' ') 空串 -> 使用 processToolUnit 统一解析（括号/引号/空格多格式）
  B14 礼物排序接入 -> gift 分支完整调用 QueueList.add_gift_list
"""
import random
import re
import threading
import time

from function.makeMusicList import MusicList
from function.makeQueueList import QueueList
from function.messagesender import trigger_log
from function.processToolUnit import ProcssTool


class ProcessMsg:
    def __init__(self, all_queue_dict, settings_temp, lock=None, sender_mgr=None, ai_chat=None):
        self.all_queue_dict = all_queue_dict
        self.settings_temp = settings_temp
        self.lock = lock or threading.RLock()
        # 自动答谢/回复触发通道：由 main 注入 SenderManager 实例
        self.sender_mgr = sender_mgr
        # AI 弹幕聊天触发通道：由 main 注入 AiChatEngine 实例（None 表示不启用）
        self.ai_chat = ai_chat
        # 自动回复冷却状态：username -> 最近回复时间戳（单位时间同人只回一次）
        self._reply_cooldown_map = {}
        self._reply_lock = threading.Lock()
        # R3：答谢 loop 模式轮询游标（gift/sc/guard 共用，多弹幕回调线程下加锁）
        self._thanks_index = 0
        self._thanks_lock = threading.Lock()

        # 确保数据结构存在
        if 'queue' not in self.all_queue_dict:
            self.all_queue_dict['queue'] = []
        if 'music' not in self.all_queue_dict:
            self.all_queue_dict['music'] = []

        # B3 关键：QueueList/MusicList 直接操作与 all_queue_dict 相同的列表对象
        # B15：启动时注入持久化的礼物记录（剔除过期项），后端重启后礼物排序权重不丢
        self.queuelistclass = QueueList(settings=settings_temp, lock=self.lock,
                                        gift_records=self.all_queue_dict.get('gift_records'))
        self.queuelistclass.queue_list = self.all_queue_dict['queue']
        self.queuelistclass.sort_list()
        self.musiclistclass = MusicList()
        self.musiclistclass.music_list = self.all_queue_dict['music']

        self.ptu = ProcssTool()
        self.orders_dict = None
        self.refresh_orders()

    # ------------------------------------------------------------------
    # 命令配置
    # ------------------------------------------------------------------
    def refresh_orders(self):
        """从 settings 读取命令配置，生成 orders_dict"""
        q = self.settings_temp.get('queue', {})
        m = self.settings_temp.get('music', {})
        self.orders_dict = {
            'q_order_list': self._split_commands(q.get('queueCommands', '')),
            'c_order_list': self._split_commands(q.get('cancelCommands', '')),
            'm_order_list': self._split_commands(m.get('musicCommands', '')),
            'n_order_list': self._split_commands(m.get('skipCommands', '')),
        }

    @staticmethod
    def _split_commands(raw):
        """将逗号/中文逗号分隔的命令串拆分为列表，去空去重"""
        if isinstance(raw, list):
            items = [str(x) for x in raw]
        else:
            items = re.split(r'[,，]', str(raw or ''))
        result = []
        for item in items:
            item = item.strip()
            if item and item not in result:
                result.append(item)
        return result

    # ------------------------------------------------------------------
    # 开关状态（默认关闭：settings 缺失该字段视为 False）
    # ------------------------------------------------------------------
    def _queue_enabled(self):
        return bool(self.settings_temp.get('queue', {}).get('queueEnabled', False))

    def _music_enabled(self):
        return bool(self.settings_temp.get('music', {}).get('musicEnabled', False))

    # ------------------------------------------------------------------
    # 队列开关（持久化 + 命令同步）
    # ------------------------------------------------------------------
    def open_queue(self):
        with self.lock:
            self.settings_temp.setdefault('queue', {})['queueEnabled'] = True

    def close_queue(self):
        with self.lock:
            self.settings_temp.setdefault('queue', {})['queueEnabled'] = False

    def open_music(self):
        with self.lock:
            self.settings_temp.setdefault('music', {})['musicEnabled'] = True

    def close_music(self):
        with self.lock:
            self.settings_temp.setdefault('music', {})['musicEnabled'] = False

    def clear_queue(self):
        # B13：复用 QueueList.clear_list()（内部 cancel 全部 gift_timers 定时器，
        # 再清空 queue_list / gift_dict / gift_timers），不再手动清 gift_timers，
        # 避免遗留回调在 45 分钟后仍触发 _remove_gift 并对新队列误排序。
        with self.lock:
            self.queuelistclass.clear_list()

    # ------------------------------------------------------------------
    # 消息解析
    # ------------------------------------------------------------------
    def contains_brackets(self, s):
        return bool(re.search(r'[()（）]', s))

    def process_music_danmu(self, msg_dict):
        """解析点歌弹幕：支持括号/引号/空格三种格式（修复 B10/B11）"""
        danmu = msg_dict['danmu'].strip()
        # 去掉命令前缀后交给通用解析器
        try:
            song, singer = self.ptu.split_music_danmu(danmu)
        except Exception:
            song, singer = None, None
        if not song:
            return None
        msg_dict['song'] = song.strip()
        if singer:
            msg_dict['singer'] = singer.strip()
        return msg_dict

    # ------------------------------------------------------------------
    # 弹幕类型分发
    # ------------------------------------------------------------------
    def processTypeChat(self, msg_dict):
        danmu = msg_dict['danmu'].strip()
        if not danmu:
            return
        # M5 修复：命令词切分兼容全角空格/制表符/连续空白（原 danmu.split(' ') 只认半角空格）
        danmu_order = re.split(r'\s+', danmu)[0] if re.split(r'\s+', danmu) else ''
        if not danmu_order:
            return

        with self.lock:
            if danmu_order in self.orders_dict['q_order_list']:
                msg_dict['type'] = 'queue'
                # 开关关闭时不执行入队（仅标记类型供弹幕展示，不产生业务副作用）
                if self._queue_enabled():
                    self.queuelistclass.add_user(msg_dict['username'])

            elif danmu_order in self.orders_dict['c_order_list']:
                msg_dict['type'] = 'cancel'
                if self._queue_enabled():
                    self.queuelistclass.remove_user(msg_dict['username'])

            elif danmu_order in self.orders_dict['m_order_list']:
                msg_dict['type'] = 'music'
                if self._music_enabled():
                    parsed = self.process_music_danmu(msg_dict)
                    if parsed:
                        # M2/M6：入队结果含失败原因；重复/超限等失败在控制台反馈（弹幕单向不可回推）
                        ok, reason = self.musiclistclass.add_music_list(parsed)
                        if not ok:
                            print(f'[点歌] 入队失败（{msg_dict.get("username", "?")}）：{reason}')

            elif danmu_order in self.orders_dict['n_order_list']:
                msg_dict['type'] = 'skip'
                if self._music_enabled():
                    ok, reason = self.musiclistclass.remove_first_song()
                    if not ok:
                        print(f'[切歌] 失败：{reason}')

    def handle_message(self, msg_dict):
        """统一入口：根据消息类型分发（B2 全分支修复）"""
        msg_type = msg_dict.get('type')
        changed = {'queue': False, 'music': False}

        if msg_type == 'chat':
            with self.lock:
                before_q = list(self.all_queue_dict['queue'])
                before_m = list(self.all_queue_dict['music'])
                self.processTypeChat(msg_dict)
                changed['queue'] = self.all_queue_dict['queue'] != before_q
                changed['music'] = self.all_queue_dict['music'] != before_m
            # 自动回复：命令匹配之后、锁外执行（发送为网络阻塞，不持锁）
            self._auto_reply(msg_dict)
            # AI 弹幕聊天：与点歌/排队解耦的独立触发链路（锁外、不阻塞弹幕循环）
            self._ai_trigger(msg_dict)

        elif msg_type == 'gift':
            # B14 礼物排序接入（受排队开关控制：关闭时不入队）
            if self._queue_enabled():
                with self.lock:
                    self.queuelistclass.add_gift_list(msg_dict.get('username', ''), msg_dict.get('coin', 0))
                    changed['queue'] = True
            # 自动答谢：独立于排队开关，收到付费礼物即答谢
            self._auto_thanks(msg_dict)

        elif msg_type == 'clear':
            if self._queue_enabled():
                with self.lock:
                    self.queuelistclass.clear_list()
                    changed['queue'] = True

        elif msg_type == 'catch_playing':
            # 同步洛雪当前播放到列表（受点歌开关控制）
            # M4：HTTP 轮询锁外执行，锁内仅做队列对齐，避免持锁阻塞请求
            if self._music_enabled():
                changed['music'] = self.catch_playing()

        elif msg_type in ('sc', 'guard'):
            # 醒目留言/大航海复用答谢文案
            self._auto_thanks(msg_dict)

        elif msg_type == 'like':
            # 预留扩展
            pass

        return changed

    # ------------------------------------------------------------------
    # AI 弹幕聊天触发（注入式，失败不影响主流程）
    # ------------------------------------------------------------------
    def _ai_trigger(self, msg_dict):
        if not self.ai_chat:
            return
        try:
            self.ai_chat.handle_danmaku(msg_dict)
        except Exception as e:
            print(f"[AI聊天] 触发异常（已隔离，不影响主流程）: {e}")

    # ------------------------------------------------------------------
    # 自动答谢与自动回复（触发通道）
    # ------------------------------------------------------------------
    @staticmethod
    def _match_keyword(mode, keyword, danmu):
        """按 replyMatchMode 匹配单条关键词：contains / exact / regex"""
        if mode == 'exact':
            return danmu == keyword
        if mode == 'regex':
            try:
                return re.search(keyword, danmu) is not None
            except re.error:
                return False
        # contains（默认）
        return keyword in danmu

    def _auto_reply(self, msg_dict):
        """自动回复：弹幕命中关键词规则时从该规则 replies 随机一条发送。

        规则按 reply_list 顺序取首个命中；replyCooldown 秒内同人只回一次；
        replyIgnoreSelf 开启时忽略登录账号自己的弹幕。
        """
        if not self.sender_mgr:
            trigger_log("_auto_reply: sender_mgr 未注入，跳过")
            return
        try:
            cfg = self.sender_mgr.get_sender_config()
        except Exception as e:
            print(f"[自动回复] 读取配置失败: {e}")
            trigger_log(f"_auto_reply 读取配置失败: {e}")
            return
        if not cfg.get('replyEnabled'):
            trigger_log(f"_auto_reply 开关关闭，跳过（danmu={msg_dict.get('danmu', '')!r}）")
            return
        danmu = (msg_dict.get('danmu') or '').strip()
        username = msg_dict.get('username') or ''
        if not danmu:
            return
        trigger_log(f"_auto_reply 收到弹幕 username={username} danmu={danmu!r}")

        # 忽略自己弹幕（昵称识别失败降级为不忽略）
        if cfg.get('replyIgnoreSelf') and username:
            try:
                if username == self.sender_mgr.get_self_uname():
                    trigger_log(f"_auto_reply 忽略自己的弹幕: {username}")
                    return
            except Exception as e:
                print(f"[自动回复] 自身昵称判断失败: {e}")

        # 冷却：单位时间同人只回一次（cooldown<=0 表示无冷却）
        cooldown = 0
        try:
            cooldown = int(cfg.get('replyCooldown') or 0)
        except (TypeError, ValueError):
            cooldown = 0
        if cooldown > 0 and username:
            now = time.time()
            with self._reply_lock:
                # B6：惰性清理——遍历时删除已过冷却期的旧条目，防止 map 无限增长
                expired = [u for u, ts in self._reply_cooldown_map.items() if now - ts >= cooldown]
                for u in expired:
                    self._reply_cooldown_map.pop(u, None)
                last = self._reply_cooldown_map.get(username, 0.0)
                if now - last < cooldown:
                    trigger_log(f"_auto_reply 冷却中，跳过（{username}）")
                    return
                # B7：不再提前写时间戳（未命中任何规则时不能占用冷却窗口）；
                # 仅在下方"实际命中并发送回复"后写入

        mode = str(cfg.get('replyMatchMode') or 'contains')
        reply_list = cfg.get('reply_list') or []
        for rule in reply_list:
            if not isinstance(rule, dict):
                continue
            keywords = rule.get('keywords')
            replies = rule.get('replies')
            # B10：脏数据容错——keywords/replies 必须是 list，非法规则跳过并记日志
            if not isinstance(keywords, list) or not isinstance(replies, list):
                trigger_log(f"_auto_reply 跳过非法规则（keywords/replies 非列表）: {rule}")
                print(f"[自动回复] 跳过非法规则（keywords/replies 非列表）: {rule}")
                continue
            if not replies:
                continue
            for kw in keywords:
                kw = str(kw or '').strip()
                if not kw:
                    continue
                # B10（顺带 B4 长度防护）：regex 模式关键词超 50 字符直接跳过，
                # 避免灾难性回溯正则（如 (a+)+$）阻塞弹幕回调线程
                if mode == 'regex' and len(kw) > 50:
                    trigger_log(f"_auto_reply 跳过超长正则关键词（>{50} 字符）: {kw[:30]}...")
                    continue
                if not self._match_keyword(mode, kw, danmu):
                    continue
                valid_replies = [r for r in replies if str(r or '').strip()]
                if not valid_replies:
                    continue
                reply = random.choice(valid_replies)
                ok, msg = self.sender_mgr.send_once(reply)
                # B7：仅在"实际命中并发送回复"（发送成功）后写入冷却时间戳
                if cooldown > 0 and username and ok:
                    with self._reply_lock:
                        self._reply_cooldown_map[username] = time.time()
                trigger_log(f"_auto_reply 命中关键词 {kw!r} -> 发送 {reply!r}，结果 ok={ok} msg={msg}")
                if not ok:
                    print(f"[自动回复] 发送失败（命中关键词 {kw}，回复 {reply}）：{msg}")
                return

    def _auto_thanks(self, msg_dict):
        """自动答谢：thanksEnabled 为真时发送答谢文案。

        R3：按 thanksMode 选择取用策略——
          loop   ：记录已用文案序号，按序轮询（取模循环），保证各条文案轮流被使用；
          random ：保持现状，随机抽取。
        R7：按事件类型分池取文案（gift/sc/guard 各自独立池，字段
          thanks_gift_list / thanks_sc_list / thanks_guard_list）；
          对应事件专用池为空时回退通用池 thanks_list。
        gift / sc / guard 三个分支共用；发送失败写日志。
        """
        if not self.sender_mgr:
            trigger_log("_auto_thanks: sender_mgr 未注入，跳过")
            return
        try:
            cfg = self.sender_mgr.get_sender_config()
        except Exception as e:
            print(f"[自动答谢] 读取配置失败: {e}")
            trigger_log(f"_auto_thanks 读取配置失败: {e}")
            return
        if not cfg.get('thanksEnabled'):
            trigger_log(f"_auto_thanks 开关关闭，跳过（type={msg_dict.get('type')}）")
            return
        # R7：按事件类型选池，专用池为空则回退通用池 thanks_list
        pool_key = {
            'gift': 'thanks_gift_list',
            'sc': 'thanks_sc_list',
            'guard': 'thanks_guard_list',
        }.get(str(msg_dict.get('type') or ''))
        pool = cfg.get(pool_key) if pool_key else None
        if not pool:
            pool = cfg.get('thanks_list') or []
        valid = [t for t in pool if str(t or '').strip()]
        if not valid:
            trigger_log(f"_auto_thanks 文案池为空，跳过（type={msg_dict.get('type')}）")
            return
        # R3：loop 按序轮询（取模循环），其余模式（random）随机抽取
        mode = str(cfg.get('thanksMode') or 'loop')
        if mode == 'loop':
            with self._thanks_lock:
                text = valid[self._thanks_index % len(valid)]
                self._thanks_index += 1
        else:
            text = random.choice(valid)
        ok, msg = self.sender_mgr.send_once(text)
        trigger_log(f"_auto_thanks 事件 {msg_dict.get('type')}（{msg_dict.get('username', '?')}）-> 发送 {text!r}，结果 ok={ok} msg={msg}")
        if not ok:
            print(f"[自动答谢] 发送失败（{msg_dict.get('type')} 来自 {msg_dict.get('username', '?')}）：{msg}")

    def next_music(self):
        """手动切歌；返回 (ok, reason)，队列为空/洛雪调用失败时可反馈"""
        with self.lock:
            return self.musiclistclass.remove_first_song()

    def catch_playing(self):
        """同步当前播放歌曲到列表（M4：HTTP 锁外，队列对齐锁内）"""
        snapshot, err = self.musiclistclass.snapshot_playing()
        if snapshot is None:
            return False
        with self.lock:
            changed, _ = self.musiclistclass.apply_playing(snapshot)
            return changed
