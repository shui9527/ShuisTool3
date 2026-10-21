# -*- coding: utf-8 -*-
"""
lx-bridge：洛雪音乐交互统一封装（方案 B）

将散落各处的洛雪交互收敛到单一模块：
  - Scheme URL 生成（searchPlay / skipNext / music/play 精确播放）
  - /status HTTP 轮询（短超时 + 错误码归一）
  - 播放状态快照提取（name / singer / songmid / source）
  - 浏览器打开 scheme 的失败兜底

统一错误约定：
  get_status() 返回 (snapshot | None, errmsg)
    snapshot = {'name','singer','songmid','source','playing'}，playing 为
    False 表示洛雪已连接但未在播放（暂停/停止）
"""
import re
import time
import webbrowser

import requests

from function.lxplayer import lxmusic

LX_STATUS_URL = 'http://127.0.0.1:23330/status'
LX_TIMEOUT = 2          # 短超时：避免 HTTP 请求长时间占用线程/锁（原 3s+ 退避过久）
STATUS_RETRIES = 2      # 有限重试次数（原 catch_playing 无限递归已修复，此处再收紧）


class LxBridge:
    def __init__(self):
        self.lxplayer = lxmusic()

    # ------------------------------------------------------------------
    # Scheme URL
    # ------------------------------------------------------------------
    def search_play_url(self, name, singer='', play_later=False):
        """生成「搜索并播放」scheme URL（点歌主路径）"""
        return self.lxplayer.music_searchPlay(name=name, singer=singer, playLater=play_later)

    def skip_next_url(self):
        """生成切歌 scheme URL"""
        return self.lxplayer.skipNext()

    def play_url(self, source, songmid, name, singer=''):
        """生成精确播放 scheme URL（洛雪命中后按 songmid 精确播放）"""
        return self.lxplayer.music_play(source=source, songmid=songmid, name=name, singer=singer)

    def open_url(self, url):
        """打开 scheme URL；失败时返回错误信息（供调用方反馈）"""
        try:
            webbrowser.open(url=url)
            return None
        except Exception as e:
            return f'打开洛雪失败: {e}'

    # ------------------------------------------------------------------
    # /status 轮询
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_status(data):
        """将洛雪 /status 响应解析为归一快照"""
        if not isinstance(data, dict):
            return None
        playing_raw = str(data.get('status') or '').lower()
        playing = playing_raw in ('playing', 'play')
        name = re.sub(r'\s+', ' ', str(data.get('name') or '')).strip()
        return {
            'name': name,
            'singer': re.sub(r'\s+', ' ', str(data.get('singer') or '')).strip(),
            'songmid': str(data.get('songmid') or data.get('songId') or ''),
            'source': str(data.get('source') or ''),
            'playing': playing,
        }

    def get_status(self):
        """锁外轮询洛雪当前播放状态；返回 (snapshot | None, errmsg)"""
        last_err = ''
        for attempt in range(STATUS_RETRIES):
            try:
                resp = requests.get(LX_STATUS_URL, timeout=LX_TIMEOUT)
                resp.raise_for_status()
                data = resp.json()
                snapshot = self._parse_status(data)
                if snapshot is None:
                    return None, 'status 响应格式异常'
                return snapshot, ''
            except (requests.RequestException, ValueError) as e:
                last_err = str(e)
                if attempt < STATUS_RETRIES - 1:
                    time.sleep(0.5 + attempt)  # 退避：0.5s、1.5s
        return None, last_err or '洛雪未连接'


# 全局单例（供 makeMusicList / main 路由复用）
lx_bridge = LxBridge()
