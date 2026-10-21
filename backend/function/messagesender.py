# -*- coding: utf-8 -*-
"""
MessageSender：自动刷屏（重写版）

修复项：
  B16 线程句柄不可控、stop_event 模块级单例导致重启立即退出
      -> SenderManager 托管线程生命周期，每次 start 创建新 Event
"""
import datetime
import json
import os
import random
import threading
import time

from bilibili_api import Credential, Danmaku, sync, user as bili_user
from bilibili_api.live import LiveRoom

# 自动答谢/回复触发链路落盘日志：temp/sender_trigger.log
# 复测定位用（事件是否到达、开关状态、命中、发送结果），线程安全追加写
TRIGGER_LOG = os.path.normpath(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    '..', 'temp', 'sender_trigger.log'))
_trigger_log_lock = threading.Lock()


def trigger_log(msg):
    try:
        with _trigger_log_lock:
            with open(TRIGGER_LOG, 'a', encoding='utf-8') as f:
                f.write('[%s] %s\n' % (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), msg))
    except Exception:
        pass


class MessageSender:
    def __init__(self, cookies):
        self.room_id = cookies.get("room_id", "")
        self.credential = Credential(
            sessdata=cookies.get("SESSDATA", ""),
            bili_jct=cookies.get("bili_jct", ""),
            buvid3=cookies.get("buvid3", ""),
            dedeuserid=cookies.get("dedeuserid", ""),
        )
        self.liveroom = LiveRoom(room_display_id=self.room_id, credential=self.credential)

    def sender(self, text):
        sync(self.liveroom.send_danmaku(Danmaku(text)))


def _run_async_via_thread(func, timeout=20.0):
    """在独立普通线程中执行 func（func 内部可安全调用 bilibili_api.sync）。

    弹幕回调运行在 asyncio 事件循环线程内，直接 sync() 会抛
    "This event loop is already running"；新建普通线程执行，
    线程内没有运行中的事件循环，sync() 会自建 loop 正常完成。
    返回 (ok, err)；err 为 'timeout' 表示超时。
    """
    result = {'ok': False, 'err': None}

    def _runner():
        try:
            func()
            result['ok'] = True
        except Exception as e:
            result['err'] = e

    t = threading.Thread(target=_runner, daemon=True, name='async-sender-thread')
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        return False, 'timeout'
    if result['ok']:
        return True, None
    return False, result['err']


def _livesender_loop(text_list, mode, interval, manager, stop_event, max_retries=3):
    """刷屏循环：stop_event 为本次线程私有，start 时可重新创建（B16）

    B1/B2：发送统一走 manager._send（内部持同一把 _send_lock + 共享
    MessageSender 实例），与 send_once 同一入口，避免与自动答谢/回复
    并发触发 B 站限速/风控。
    """
    while not stop_event.is_set():
        texts = [t for t in (text_list or []) if t and str(t).strip()]
        if not texts:
            time.sleep(1)
            continue

        candidates = texts if mode == 'loop' else [random.choice(texts)]
        for text in candidates:
            if stop_event.is_set():
                return
            for retry in range(max_retries):
                ok, msg = manager._send(text)
                if ok:
                    break
                print(f"发送消息时发生错误：{msg}。重试次数：{retry + 1}/{max_retries}。")
                time.sleep(interval)
            else:
                print(f"发送消息失败，已达到最大重试次数：{max_retries}。")
            time.sleep(interval)


class SenderManager:
    """刷屏线程管理器：统一 start/stop/status；自动答谢/回复触发通道 send_once"""

    def __init__(self, cookies_file, config_file=None):
        self.cookies_file = cookies_file
        self.config_file = config_file
        self._thread = None
        self._stop_event = None
        # 单发发送串行锁：send_once 与刷屏线程共享 B 站弹幕通道，串行化避免并发限速
        self._send_lock = threading.Lock()
        # 共享 MessageSender 实例（B1/B2 实例复用）：懒加载，cookies 更新后
        # invalidate_sender() 置空，下次发送按新 cookies 重建
        self._sender = None
        # 配置 TTL 缓存（自动答谢/回复每弹幕事件都会读取，避免频繁读盘）
        self._config_cache = None
        self._config_cache_ts = 0.0
        self._config_lock = threading.Lock()
        # 登录用户昵称缓存（replyIgnoreSelf 判断用）
        self._self_uname = None
        self._self_uname_ts = 0.0

    def _load_cookies(self):
        if not os.path.exists(self.cookies_file):
            return {}
        try:
            with open(self.cookies_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"读取 cookies 失败: {e}")
            return {}

    def _get_sender(self):
        """懒加载共享 MessageSender 实例（B2）。

        需在 _send_lock 内调用（_send 保证）；cookies 未配置完整时返回 None。
        cookies 更新后由 invalidate_sender() 置空，下次发送按新 cookies 重建。
        """
        if self._sender is None:
            cookies = self._load_cookies()
            if not cookies.get("room_id") or not cookies.get("SESSDATA"):
                return None
            try:
                self._sender = MessageSender(cookies)
            except Exception as e:
                print(f"初始化共享 MessageSender 失败: {e}")
                return None
        return self._sender

    def invalidate_sender(self):
        """cookies 更新后调用：丢弃共享 MessageSender，下次发送按新 cookies 重建"""
        with self._send_lock:
            self._sender = None

    def _send(self, text):
        """统一发送入口（B1）：_send_lock 内使用共享 MessageSender 发送。

        刷屏循环 _livesender_loop 与 send_once 均经此方法，保证 B 站弹幕
        通道全局串行，避免并发触发限速/风控。返回 (ok, msg)。
        """
        with self._send_lock:
            sender = self._get_sender()
            if sender is None:
                return False, "cookies 未配置完整（room_id / SESSDATA 必填）"
            try:
                sender.sender(text)
            except Exception as e:
                return False, f"发送失败: {e}"
        return True, "ok"

    def send_once(self, text):
        """单发弹幕（自动答谢/回复触发通道）。

        B1/B2：经统一入口 _send 使用共享 MessageSender 实例发送（同一把
        _send_lock，与刷屏线程全局串行），不再每次新建实例；仍必须经
        _run_async_via_thread 在独立线程执行——弹幕回调运行在 asyncio 事件
        循环线程内，直接 sync() 会报 already running，此机制不可回退。
        返回 (ok, msg)，失败写日志。
        """
        text = str(text or '').strip()
        if not text:
            trigger_log("send_once 弹幕内容为空，拒绝发送")
            return False, "弹幕内容为空"
        holder = {}

        def _do_send():
            holder['ok'], holder['msg'] = self._send(text)

        ok, err = _run_async_via_thread(_do_send, timeout=20.0)
        if ok:
            if holder.get('ok'):
                trigger_log(f"send_once 发送成功: {text}")
                return True, "ok"
            trigger_log(f"send_once 发送弹幕失败: {holder.get('msg')}（text={text}）")
            print(f"send_once 发送弹幕失败: {holder.get('msg')}")
            return False, holder.get('msg', '发送失败')
        if err == 'timeout':
            trigger_log(f"send_once 发送超时（20s）: {text}")
            return False, "发送超时"
        trigger_log(f"send_once 发送弹幕失败: {err}（text={text}）")
        print(f"send_once 发送弹幕失败: {err}")
        return False, f"发送失败: {err}"

    def get_sender_config(self):
        """读取 sender_config.json 配置（0.5s TTL 缓存 + 锁，线程安全）。

        返回 dict：screeningMode/interval/text_list/thanksEnabled/thanksMode/
        thanks_list/replyEnabled/replyMatchMode/replyCooldown/replyIgnoreSelf/
        reply_list。handle_message 每次事件调用本方法获取最新已保存配置。
        """
        now = time.time()
        with self._config_lock:
            if self._config_cache is not None and now - self._config_cache_ts < 0.5:
                return self._config_cache
            data = {}
            if self.config_file and os.path.exists(self.config_file):
                try:
                    with open(self.config_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                except Exception as e:
                    print(f"读取 sender_config.json 失败: {e}")
            self._config_cache = data if isinstance(data, dict) else {}
            self._config_cache_ts = now
            return self._config_cache

    def flush_config(self):
        """重置配置 TTL 缓存（B9）：保存配置写盘后调用，新配置立即生效"""
        with self._config_lock:
            self._config_cache = None
            self._config_cache_ts = 0.0

    def get_self_uname(self):
        """当前登录账号昵称（10 分钟 TTL 缓存）；供 replyIgnoreSelf 判断。

        失败时降级返回 ''（视为不忽略，仅打日志）。
        """
        now = time.time()
        if self._self_uname is not None and now - self._self_uname_ts < 600:
            return self._self_uname
        uname = ''
        cookies = self._load_cookies()
        uid = int(cookies.get("dedeuserid") or 0)
        if uid:
            holder = {'uname': ''}

            def _fetch():
                info = sync(bili_user.User(uid).get_user_info())
                if isinstance(info, dict):
                    holder['uname'] = str(info.get('name') or '')

            ok, err = _run_async_via_thread(_fetch, timeout=10.0)
            if ok:
                uname = holder['uname']
            else:
                trigger_log(f"获取登录用户昵称失败: {err}")
                print(f"获取登录用户昵称失败: {err}")
        self._self_uname = uname
        self._self_uname_ts = now
        if uname:
            trigger_log(f"获取登录用户昵称成功: {uname}")
        return uname

    def start(self, text_list=None, mode='loop', interval=5):
        if self.is_running():
            return False, "刷屏线程已在运行"
        # P1 修复：interval 可能为字符串（cookies.json 存储），统一转 float，
        # 并钳制最小 1s，避免过快发送触发 B 站限速/风控
        try:
            interval = max(float(interval or 5), 1.0)
        except (TypeError, ValueError):
            interval = 5.0
        mode = mode if mode in ('loop', 'random') else 'loop'
        cookies = self._load_cookies()
        if not cookies.get("room_id") or not cookies.get("SESSDATA"):
            return False, "cookies 未配置完整（room_id / SESSDATA 必填）"
        # B2：不在 start 时创建独立 MessageSender，刷屏循环与 send_once
        # 共用 manager._send -> _get_sender() 的共享实例（B1 统一入口 + 实例复用）

        self._stop_event = threading.Event()  # B16 每次新 Event
        self._thread = threading.Thread(
            target=_livesender_loop,
            args=(text_list or [], mode, interval, self, self._stop_event),
            daemon=True,
        )
        self._thread.start()
        return True, "started"

    def stop(self):
        if self._thread and self._thread.is_alive() and self._stop_event:
            self._stop_event.set()
            self._thread.join(timeout=5)
        self._thread = None
        self._stop_event = None
        return True

    def is_running(self):
        return self._thread is not None and self._thread.is_alive()
