# -*- coding: utf-8 -*-
"""
QueueList：排队列表（时间戳惰性过期版）

修复项：
  B9  remove_user 未找到用户时误返回 True -> 改为返回 False
  B13 并发访问加锁（由 ProcessMsg 统一持有，本类操作视为在锁内）
  B15 礼物累计改为"时间戳 + 惰性过期"：gift_dict 记录 {coin, expire}，
      滚动窗口（每次送礼刷新 expire=now+45min），排序/增删/导出前惰性剔除
      过期项，消灭每用户 45 分钟 Timer 风暴；礼物记录可随 queue.json
      持久化，后端重启后自动恢复（启动时注入 gift_records 并剔除过期项）。
"""
import time


class QueueList:
    GIFT_WINDOW = 45 * 60  # 礼物累计窗口：最近一次送礼后 45 分钟内有效（滚动）

    def __init__(self, settings=None, lock=None, gift_records=None):
        self.queue_list = []      # 与 all_queue_dict['queue'] 共享同一列表对象
        self.gift_dict = {}       # 用户礼物总值：username -> {'coin': int, 'expire': float}
        self.settings = settings or {}
        self.lock = lock          # M8：调用方持锁操作，避免并发篡改队列/排序
        if gift_records:
            self._load_gift_records(gift_records)

    def _load_gift_records(self, records):
        """启动时注入持久化的礼物记录（剔除已过期项）"""
        now = time.time()
        if not isinstance(records, dict):
            return
        for username, g in records.items():
            if not isinstance(g, dict):
                continue
            try:
                coin = int(g.get('coin', 0) or 0)
                expire = float(g.get('expire', 0) or 0)
            except (TypeError, ValueError):
                continue
            if coin > 0 and expire > now:
                self.gift_dict[str(username)] = {'coin': coin, 'expire': expire}

    def gift_records(self):
        """导出礼物记录快照（惰性清理后），供 queue.json 持久化"""
        self._expire_gifts()
        return self.gift_dict

    def _expire_gifts(self, now=None):
        """惰性剔除已过期礼物记录（排序/导出前调用，锁内）"""
        now = now or time.time()
        expired = [u for u, g in self.gift_dict.items() if g['expire'] <= now]
        for u in expired:
            del self.gift_dict[u]

    def add_gift_list(self, username, total_coin):
        """添加或更新用户礼物值（滚动 45 分钟窗口：每次送礼刷新过期时间）"""
        if not username:
            return False
        now = time.time()
        old = self.gift_dict.get(username)
        base = old['coin'] if old and old['expire'] > now else 0
        self.gift_dict[username] = {'coin': base + total_coin, 'expire': now + self.GIFT_WINDOW}
        self.sort_list()
        return True

    def add_user(self, username):
        """添加用户到排队列表（去重 + 人数上限 queueLimit）"""
        if not username:
            return False
        q = self.settings.get('queue', {}) if isinstance(self.settings, dict) else {}
        limit_raw = str(q.get('queueLimit', '') or '').strip()
        if limit_raw:
            try:
                limit = int(limit_raw)
            except ValueError:
                limit = 0
            if limit > 0 and len(self.queue_list) >= limit:
                return False
        if username not in self.queue_list:
            self.queue_list.append(username)
            self.sort_list()
            return True
        return False

    def remove_user(self, username):
        """从排队列表移除用户（同时删除其礼物记录）；不存在时返回 False（修复 B9）"""
        if username in self.queue_list:
            self.queue_list.remove(username)
            if username in self.gift_dict:
                del self.gift_dict[username]
            self.sort_list()
            return True
        return False

    def sort_list(self):
        """按礼物价值降序排序（仅当「礼物优先」开启且存在有效礼物记录时；默认开启）

        排序前惰性清理过期礼物记录：过期用户的排序权重归零，保留入队相对顺序。
        """
        self._expire_gifts()
        q = self.settings.get('queue', {}) if isinstance(self.settings, dict) else {}
        gift_priority = str(q.get('giftPriority', 'true')).lower() != 'false'
        if gift_priority and self.gift_dict:
            self.queue_list.sort(key=lambda x: self.gift_dict.get(x, {}).get('coin', 0), reverse=True)
            return True
        return False

    def clear_list(self):
        """清空排队列表与礼物记录"""
        self.queue_list.clear()
        self.gift_dict.clear()
