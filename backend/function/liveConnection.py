# -*- coding: utf-8 -*-
"""
LiveConnection：B站开放平台弹幕接入（重写版）

修复项：
  B7  密钥硬编码 -> 从 settings/gallery/openlive 读取，支持空值兜底
  B8  停止不停线程 -> stop_sync 调用 OpenLiveClient.stop_and_close() 真正停止
"""
import asyncio
import json
import os
import threading
import time

import function.blivedm as blivedm

SETTINGS_FILE = os.path.normpath(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    '..', 'settings', 'settings.json'))


def _load_openlive_config():
    """从 settings.json 读取开放平台密钥，避免硬编码（B7）"""
    try:
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        gallery = data.get('gallery', {})
        return {
            'access_key_id': (gallery.get('openlive') or {}).get('accessKeyId', ''),
            'access_key_secret': (gallery.get('openlive') or {}).get('accessKeySecret', ''),
            'app_id': (gallery.get('openlive') or {}).get('appId', ''),
        }
    except Exception as e:
        print(f"读取开放平台配置失败: {e}")
        return {'access_key_id': '', 'access_key_secret': '', 'app_id': ''}


class LiveHandler(blivedm.BaseHandler):
    def __init__(self, biliclient_callback):
        self.biliclient_callback = biliclient_callback
        # B15：点赞限流——5 秒窗口内仅回调一次，避免高频点赞打爆日志与 SSE 队列
        self._last_like_cb_ts = 0.0

    def _on_open_live_danmaku(self, client, message):
        # AI 弹幕聊天接入：补充 uid（open_id，用户唯一标识）与 timestamp，
        # 供 AiChatEngine 做同用户会话/限流；不影响现有 msg_dict 契约字段
        msg_dict = {
            'type': 'chat',
            'username': message.uname,
            'danmu': message.msg,
            'uid': getattr(message, 'open_id', '') or message.uname,
            'timestamp': getattr(message, 'timestamp', 0) or time.time(),
        }
        self.biliclient_callback(msg_dict)

    def _on_open_live_gift(self, client, message):
        total_coin = message.price * message.gift_num
        msg_dict = {
            'type': 'gift',
            'username': message.uname,
            'gift': message.gift_name,
            'num': message.gift_num,
            'coin': total_coin,
            'danmu': f'{message.uname} 赠送{message.gift_name}x{message.gift_num}',
        }
        if message.paid:
            self.biliclient_callback(msg_dict)

    def _on_open_live_super_chat(self, client, message):
        msg_dict = {
            'type': 'sc',
            'username': message.uname,
            'danmu': f'¥{message.rmb} 醒目留言 ：{message.message}',
            'rmb': message.rmb,
        }
        self.biliclient_callback(msg_dict)

    def _on_open_live_buy_guard(self, client, message):
        msg_dict = {
            'type': 'guard',
            'username': message.user_info.uname,
            'level': message.guard_level,
            'danmu': f'购买 大航海等级={message.guard_level}',
        }
        self.biliclient_callback(msg_dict)

    def _on_open_live_like(self, client, message):
        # B15：限流——5 秒窗口内仅回调一次，点赞风暴只产生低频事件
        now = time.time()
        if now - self._last_like_cb_ts < 5.0:
            return
        self._last_like_cb_ts = now
        msg_dict = {'type': 'like', 'username': message.uname}
        self.biliclient_callback(msg_dict)


class LiveConnection:
    def __init__(self, livecode, biliclient_callback, config=None, started_event=None):
        self.livecode = livecode
        self.biliclient_callback = biliclient_callback
        self.config = config or _load_openlive_config()
        self.started_event = started_event
        self.client = None
        self.handler = None
        self.is_running = False
        self._loop = None

    async def start(self):
        """启动直播连接（在后台线程的事件循环中运行）"""
        self._loop = asyncio.get_running_loop()
        try:
            print(f"开始连接直播间，直播码: {self.livecode}")
            # B7 使用配置化密钥，空值给出明确提示
            if not self.config['access_key_id'] or not self.config['access_key_secret']:
                raise ValueError("开放平台密钥未配置，请先在「设置」中填写 openlive 密钥")

            self.client = blivedm.OpenLiveClient(
                access_key_id=self.config['access_key_id'],
                access_key_secret=self.config['access_key_secret'],
                app_id=int(self.config['app_id'] or 0),
                room_owner_auth_code=self.livecode,
            )
            self.handler = LiveHandler(self.biliclient_callback)
            self.client.set_handler(self.handler)
            self.client.start()
            self.is_running = True
            print("客户端启动成功")
            if self.started_event is not None:
                self.started_event.set()
            await self.client.join()
        except Exception as e:
            print(f"连接直播间时出错: {str(e)}")
            await self.cleanup()
            raise

    def stop_sync(self):
        """同步停止客户端（B8：真正停止底层网络协程与资源）"""
        if self.client and self.is_running:
            print("正在停止客户端...")
            stopped = False
            try:
                if self._loop is not None and self._loop.is_running():
                    # 调度回客户端所属事件循环执行，避免跨 loop 操作 Future 失效
                    # （stop_and_close 的 cancel/shield 必须与 client 在同一 loop）
                    fut = asyncio.run_coroutine_threadsafe(self._stop_async(), self._loop)
                    try:
                        fut.result(timeout=8)
                        stopped = True
                    except Exception as e:
                        print(f"停止客户端异常: {e}")
                else:
                    asyncio.run(self._stop_async())
                    stopped = True
            except Exception as e:
                print(f"停止客户端异常: {e}")
            if not stopped and self._loop is not None and self._loop.is_running():
                # 兜底：stop_and_close 未完成时强制退出子线程事件循环，
                # run_until_complete 抛出后由 biliclient_thread finally 执行
                # loop.close() 关闭底层传输，确保连接真正断开、线程退出
                print("stop_and_close 未完成，强制退出事件循环")
                try:
                    self._loop.call_soon_threadsafe(self._loop.stop)
                except Exception as e:
                    print(f"强制退出事件循环失败: {e}")
            self.is_running = False
            print("客户端已停止")
        self.client = None

    async def _stop_async(self):
        if self.client:
            try:
                # blivedm 提供的便利方法：stop() + join() + close()
                await self.client.stop_and_close()
            except Exception as e:
                print(f"stop_and_close 异常: {e}")

    async def cleanup(self):
        if self.client:
            await self._stop_async()
            self.client = None
