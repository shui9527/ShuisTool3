# -*- coding: utf-8 -*-
"""
MusicList：点歌队列 + 播放状态机（方案 B 重构版）

核心变化（相对旧 string[] 版）：
  H1   compare() 改为【原地裁剪】共享列表（del self.music_list[:index]），
       严禁重新绑定列表对象，根治 all_queue_dict['music'] 共享引用断裂
  H2   队列条目升级为 SongEntry[]，携带 songmid/source，与洛雪实际播放强绑定：
       洛雪播哪首，队列头部即对齐哪首（pending -> playing -> played）
  H3   播放推进双通道：前端 sync_playing 回传 + 后台轮询兜底（洛雪无播放完成事件）
  M3   点歌上限 MAX_MUSIC=50，超限拒绝并返回原因
  M4   HTTP 轮询（snapshot_playing）与队列变更（apply_playing）分离：
       HTTP 在锁外执行（timeout=2 + 有限退避），锁内仅做内存操作
  M9   切歌先调用洛雪再 pop，调用失败时返回错误，前端可提示

统一返回约定：状态机方法返回 (changed: bool, events: list[dict])，
events 元素形如 {'event': 'song_started'|'song_ended', 'data': {...}}，
由调用方（main 路由 / 监控线程）负责 SSE 广播与持久化。
"""
import re
import time
import threading

from function import song_entry
from function.lxbridge import lx_bridge

MAX_MUSIC = 10  # 点歌上限（M3，路线B：50 -> 10）
RECENT_POPPED_TTL = 5.0  # 切歌后旧歌快照忽略窗口（秒），洛雪切换延迟期内抑制旧歌插回
REMOVED_TTL = 600.0  # 已移除歌曲自动切歌有效期（秒）= 10 分钟；超过视为失效，不再自动切（路线B）
# B16：自动切歌依赖 removed_keys 的 10 分钟 TTL——主播删除歌曲后超过 10 分钟的
# 弹幕不触发切歌，属已知行为设计（非缺陷）。
SKIP_DEBOUNCE_TTL = 30.0  # 同一首歌自动切歌防抖窗口（秒）：30s 内不重复触发（路线B）
MAX_CONSECUTIVE_SKIPS = 10  # 连续自动切歌上限（= 队列上限），达到后暂停（路线B）
CONSECUTIVE_PAUSE_SECONDS = 60.0  # 连续超限后的暂停时长（秒）（路线B）


class MusicList:
    def __init__(self):
        self.music_list = []  # SongEntry[]，与 all_queue_dict['music'] 共享同一列表对象
        self.lx = lx_bridge
        # 最近被切走的歌（切歌 pop 后记录）：norm_key -> 时间戳，保留 RECENT_POPPED_TTL 秒
        # 用于抑制洛雪切换延迟期内旧歌快照被 apply_playing 重新插回队列（线程安全：均在锁内读写）
        self.recent_popped = {}
        # 被用户移除的歌曲（update_music_list 记录）：norm_key -> 移除时间戳（秒）。
        # 洛雪 playLater 预排队导致已移除歌曲仍可能在洛雪侧播放/残留，
        # apply_playing 遇到这些歌时忽略快照、不插回队列；移除记录带 TTL
        # （REMOVED_TTL=10 分钟），仅 TTL 内触发自动切歌（路线B），过期视为
        # 非移除（惰性清理，不产生异常）；重新点播时由 add_music_list 清除。
        self.removed_keys = {}
        # --- 路线B：SSE / 轮询自动切歌保护状态（均在锁内读写） ---
        self.last_skip_at = {}  # norm_key -> 上次自动切歌时间戳（秒），30s 防抖
        self.consecutive_skips = 0  # 连续自动切歌计数（队列发生变化时复位）
        self.consecutive_pause_until = 0.0  # 连续达到上限后的暂停截止时间戳（秒）
        self._last_skip_sig = None  # 上次自动切歌时的队列签名（用于队列变化复位计数）
        # 上一次消息解析暂存（兼容 processmsg.processVal 调用链）
        self.song = None
        self.singer = None
        self.username = None

    # ------------------------------------------------------------------
    # 消息暂存（兼容旧调用链）
    # ------------------------------------------------------------------
    def processVal(self, msg_dict):
        if 'song' in msg_dict:
            self.song = msg_dict['song']
        if 'singer' in msg_dict:
            self.singer = msg_dict['singer']
        if 'username' in msg_dict:
            self.username = msg_dict['username']

    # ------------------------------------------------------------------
    # 入队（去重含歌手 + 上限 + 状态机）
    # ------------------------------------------------------------------
    def add_music_list(self, msg_dict):
        """添加歌曲到队列；返回 (ok, reason)。去重键为 (name, singer) 归一化二元组（M2）。"""
        self.processVal(msg_dict)
        if not self.song:
            return False, '歌曲名为空'

        # M2 去重：归一化 (name, singer) 二元组，中文原样、英文忽略大小写与空白
        if song_entry.find_index(self.music_list, self.song, self.singer) >= 0:
            return False, '队列中已存在相同歌曲'

        # M3 上限：超过 MAX_MUSIC 拒绝
        if len(self.music_list) >= MAX_MUSIC:
            return False, f'点歌队列已满（上限 {MAX_MUSIC} 首）'

        play_later = len(self.music_list) > 0
        entry = song_entry.make_entry(
            name=self.song, singer=self.singer, username=self.username,
            play_later=play_later, status='pending')
        self.music_list.append(entry)
        # 重新点播同一首歌：允许再次入队并被播放，从"已移除"集合中清除
        key = song_entry.norm_key(entry['name'], entry['singer'])
        if key[0]:
            # removed_keys 现为 dict：清除完整键与按歌名退化键
            self.removed_keys.pop(key, None)
            self.removed_keys.pop(song_entry.norm_key(entry['name'], ''), None)

        if play_later:
            # 已有待播：仅入队，稍后播放（洛雪侧 playLater=true 会放入其播放列表尾部）
            err = self.lx.open_url(self.lx.search_play_url(entry['name'], entry['singer'], play_later=True))
            if err:
                return False, err
            return True, None

        # 队列原为空：立即播放（洛雪 searchPlay 立即播放）
        err = self.lx.open_url(self.lx.search_play_url(entry['name'], entry['singer'], play_later=False))
        if err:
            return False, err
        entry['status'] = 'playing'
        return True, None

    # ------------------------------------------------------------------
    # 切歌（先洛雪后 pop，M9）
    # ------------------------------------------------------------------
    def remove_first_song(self):
        """切歌：先调用洛雪切歌，成功（无法验证失败）后移除队列第一首；返回 (ok, reason)"""
        if not self.music_list:
            return False, '点歌队列为空'
        # M9：先调用洛雪；webbrowser.open 不抛异常即视为已发起（无法验证洛雪内部结果），
        # 仅当打开 scheme 本身失败时才保留队列项
        err = self.lx.open_url(self.lx.skip_next_url())
        if err:
            return False, err
        popped = self.music_list.pop(0)
        # 记录刚被切走的歌（洛雪切换有延迟，短时间内 apply_playing 可能仍收到旧歌快照，
        # 借此抑制其被插回队列）。锁内读写，线程安全。
        key = song_entry.norm_key(song_entry.entry_name(popped), song_entry.entry_singer(popped))
        if key[0]:
            self.recent_popped[key] = time.time()
        return True, song_entry.entry_name(popped)

    # ------------------------------------------------------------------
    # 洛雪播放状态 -> 队列状态机（核心，H1/H2）
    # ------------------------------------------------------------------
    def apply_playing(self, snapshot):
        """
        锁内：将洛雪实际播放快照对齐到队列（compare 的强关联版）。
        返回 (changed, events)。
        snapshot: {'name','singer','songmid','source','playing'} 或 None
        """
        events = []
        if not snapshot or not snapshot.get('name'):
            # 洛雪已停止/无曲目：头部 playing 项标记 played 并 pop（自动推进，
            # 避免队列头部长期残留"正在播放"假象）。暂停时洛雪仍保留歌名，
            # 不会走到这里。
            if self.music_list and self.music_list[0].get('status') == 'playing':
                played = self.music_list.pop(0)
                events.append({'event': 'song_ended', 'data': {'name': song_entry.entry_name(played)}})
                return True, events
            return False, events

        name = snapshot['name']
        singer = snapshot.get('singer') or ''
        idx = song_entry.find_index(self.music_list, name, singer)
        changed = False

        if idx == 0:
            # 头部即该歌：回填元数据，确保 songmid/source 与洛雪一致（H2 精确绑定）
            changed = self._sync_meta(0, snapshot) or self.music_list[0].get('status') != 'playing'
            self.music_list[0]['status'] = 'playing'
            return changed, events

        if idx > 0:
            # 命中队列中间：原地裁剪掉前面的项（视为被跳过），该歌成为头部并标记 playing
            del self.music_list[:idx]
            self._sync_meta(0, snapshot)
            self.music_list[0]['status'] = 'playing'
            events.append({'event': 'song_started', 'data': self._entry_brief(0)})
            return True, events

        # 队列中未命中：洛雪在播队列外的歌（手动播放 / 连播）
        # 先检查该快照是否为 5 秒内刚从队头被切走的旧歌（洛雪切换延迟期），
        # 命中则忽略快照不插入队列，避免旧歌被重新插回（修复切歌后旧歌回插问题）。
        if self._is_recent_popped(name, singer):
            return False, events

        # 已移除歌曲：用户已把该歌移出队列（update_music_list 记录），洛雪
        # playLater 预排队残留仍在播放时忽略快照、不插回队列，避免"移除后
        # 又被重新加回 / 只剩最后一项时点移除没反应"。
        # 路线B：命中 removed_keys（TTL 内）同时走统一自动切歌（与 SSE 通道
        # 共用防抖与连续计数），把残留的已移除歌从洛雪侧切走。
        if self._is_removed(name, singer):
            try:
                self.check_and_skip_removed(name, singer)
            except Exception as e:
                print(f"轮询自动切歌失败: {e}")
            return False, events

        if not self.music_list:
            entry = song_entry.make_entry(name, singer, status='playing')
            entry.update({'source': snapshot.get('source') or '', 'songmid': snapshot.get('songmid') or ''})
            self.music_list.insert(0, entry)
            events.append({'event': 'song_started', 'data': {'name': name, 'singer': singer}})
            return True, events

        # 防重兜底：上面 find_index 放宽后仍未命中时，insert 前先按歌名弱匹配
        # 查找同名条目（如队列项 singer 非空但与快照 singer 归一化后不同等漏匹配场景）。
        # 命中则不 insert，复用该条目：裁剪提升到头部、回填 songmid/source、置 playing，
        # 杜绝同名单曲以新 SongEntry 插入第一位导致重复。
        dup_idx = song_entry.find_index(self.music_list, name, '')
        if dup_idx >= 0:
            if dup_idx > 0:
                del self.music_list[:dup_idx]
            self._sync_meta(0, snapshot)
            self.music_list[0]['status'] = 'playing'
            events.append({'event': 'song_started', 'data': self._entry_brief(0)})
            return True, events

        # 列表非空且未命中：
        #  - 头部已是 playing（洛雪切走了正在播的歌）-> 该首标记 played 并 pop
        #  - 头部是 pending（洛雪手动播放接管）-> 新歌插入头部，用户点的歌保留在后
        if self.music_list[0].get('status') == 'playing':
            played = self.music_list.pop(0)
            events.append({'event': 'song_ended', 'data': {'name': song_entry.entry_name(played)}})
            changed = True
        entry = song_entry.make_entry(name, singer, status='playing')
        entry.update({'source': snapshot.get('source') or '', 'songmid': snapshot.get('songmid') or ''})
        self.music_list.insert(0, entry)
        events.append({'event': 'song_started', 'data': {'name': name, 'singer': singer}})
        return True, events

    # ------------------------------------------------------------------
    # 前端回传：播放状态变化（H3 通道一）
    # ------------------------------------------------------------------
    def sync_playing(self, payload):
        """
        前端把洛雪 playing 变化回传（/sync_playing）驱动状态机。
        payload: {'name','singer','songmid','status'}，status: playing|paused|ended|stop
        返回 (changed, events, err)
        """
        if not isinstance(payload, dict):
            return False, [], '无效请求'
        status = str(payload.get('status') or '').lower().strip()

        if status in ('ended', 'stop', 'stopped', 'finished'):
            # 洛雪停止/播放完成：头部 playing 项标记 played 并 pop（自动推进）
            if self.music_list and self.music_list[0].get('status') == 'playing':
                played = self.music_list.pop(0)
                return True, [{'event': 'song_ended', 'data': {'name': song_entry.entry_name(played)}}], None
            return False, [], None

        # playing / paused / 其它：一律按洛雪当前播放对齐（paused 时歌曲未变，仅对齐元数据）
        name = str(payload.get('name') or '').strip()
        if not name:
            return False, [], '缺少歌曲名'
        snapshot = {
            'name': name,
            'singer': str(payload.get('singer') or ''),
            'songmid': str(payload.get('songmid') or ''),
            'source': str(payload.get('source') or ''),
            'playing': status in ('playing', 'play'),
        }
        changed, events = self.apply_playing(snapshot)
        return changed, events, None

    # ------------------------------------------------------------------
    # 洛雪轮询（锁外 HTTP + 锁内 apply 分离，M4）
    # ------------------------------------------------------------------
    def snapshot_playing(self):
        """锁外：轮询洛雪 /status；返回 (snapshot | None, err)"""
        return self.lx.get_status()

    def catch_playing(self):
        """组合入口（路由 /catch_playing 使用）：锁外快照 + 锁内对齐"""
        snapshot, err = self.snapshot_playing()
        if snapshot is None:
            return False, [], err or '洛雪未连接'
        return self.apply_playing(snapshot)

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _is_recent_popped(self, name, singer=''):
        """快照歌曲是否在 RECENT_POPPED_TTL 秒内刚从队头被切走（锁内调用）。
        命中说明洛雪切换存在延迟、快照仍是旧歌，调用方应忽略该快照。
        匹配规则与 find_index 一致：完整 (name, singer) 键未命中时，若记录里存在
        同名且无 singer 的条目（队列项 singer 为空时写入的就是 ('<name>', '') 键），
        退化为仅按歌名匹配——避免快照带 singer、队列项无 singer 时旧歌漏拦截被插回。
        """
        if not self.recent_popped:
            return False
        now = time.time()
        # 惰性清理过期项
        expired = [k for k, ts in self.recent_popped.items() if now - ts > RECENT_POPPED_TTL]
        for k in expired:
            del self.recent_popped[k]
        if not self.recent_popped:
            return False
        key = song_entry.norm_key(name, singer)
        if key in self.recent_popped:
            return True
        # 完整键未命中：退化为仅按歌名匹配（singer 任一为空视为同一首歌）
        return song_entry.norm_key(name, '') in self.recent_popped

    def _is_removed(self, name, singer=''):
        """快照歌曲是否已被用户移除且移除时间仍在 REMOVED_TTL 窗口内（锁内调用）。
        命中说明用户已把该歌移出队列（洛雪 playLater 预排队仍可能在播），
        调用方应忽略该快照，避免已移除歌曲被重新插回队列；TTL 内同时触发自动切歌。
        removed_keys 现为 norm_key -> 移除时间戳（秒）：
          - 命中且未过期 -> True
          - 过期 -> 视为非移除：惰性删除该键并返回 False（不抛异常、不残留脏数据）
        匹配规则与 find_index 一致：完整 (name, singer) 键未命中时，
        退化为仅按歌名匹配（任一 singer 为空视为同一首歌）。
        """
        if not self.removed_keys:
            return False
        now = time.time()
        # 惰性清理过期项（TTL 外视为非移除）
        expired = [k for k, ts in self.removed_keys.items() if now - ts > REMOVED_TTL]
        for k in expired:
            del self.removed_keys[k]
        if not self.removed_keys:
            return False
        key = song_entry.norm_key(name, singer)
        if key in self.removed_keys:
            return True
        return song_entry.norm_key(name, '') in self.removed_keys

    # ------------------------------------------------------------------
    # 路线B：SSE 新歌事件触发自动切歌
    # ------------------------------------------------------------------
    def _queue_sig(self):
        """当前队列签名：长度 + 头部歌曲键。用于判断队列是否发生变化（复位连续计数）"""
        if not self.music_list:
            return None
        head = self.music_list[0]
        return (len(self.music_list),
                song_entry.norm_key(song_entry.entry_name(head), song_entry.entry_singer(head)))

    def check_and_skip_removed(self, name, singer=''):
        """路线B：新歌事件自动切歌统一检测函数（锁内调用）。

        SSE 通道（/api/lx/subscribe 解析 event: name）与轮询通道（apply_playing）
        共用此函数：仅当歌曲命中 removed_keys（被用户主动移除）且移除时间在
        REMOVED_TTL（10 分钟）内时，才调用 skip_next_url() 切走；其余任何
        "不在队列"的歌（主播手动播放、洛雪连播等）一律不动作。

        保护条件（满足任一即不动作）：
          - 队列为空
          - recent_popped 窗口内（刚被切走的旧歌残留）
          - 同一首歌 30s 内已尝试跳过（SKIP_DEBOUNCE_TTL 防抖）
          - 连续跳过累计达 MAX_CONSECUTIVE_SKIPS 后的暂停期内
          - 队列发生变化 -> 复位连续计数与暂停（用户新增/移除歌曲视为新场景）

        返回 (actioned: bool, reason: str)
        """
        name = song_entry.norm_name(name)
        if not name:
            return False, 'empty_name'
        if not self._is_removed(name, singer):
            return False, 'not_removed'
        if not self.music_list:
            return False, 'queue_empty'
        if self._is_recent_popped(name, singer):
            return False, 'recent_popped'
        key = song_entry.norm_key(name, singer)
        if not key[0]:
            return False, 'empty_key'
        # 防抖键统一为歌名退化键（与 _is_removed 退化匹配一致，路线B修复）：
        # SSE 通道 singer 为空、前端回传/轮询通道 singer 为洛雪真实歌手，
        # 若按完整 (name, singer) 键记录，同一首歌在两条通道防抖互不命中，
        # 会导致被移除歌被连续跳过两次（B->C->D）。退化键使两通道共享同一防抖槽。
        debounce_key = song_entry.norm_key(name, '')
        now = time.time()
        # 队列签名变化（用户新增/移除歌曲等）-> 复位连续计数与暂停
        sig = self._queue_sig()
        if self._last_skip_sig != sig:
            self.consecutive_skips = 0
            self.consecutive_pause_until = 0.0
        if now < self.consecutive_pause_until:
            return False, 'paused'
        if now - self.last_skip_at.get(debounce_key, 0.0) < SKIP_DEBOUNCE_TTL:
            return False, 'debounced'
        self.consecutive_skips += 1
        if self.consecutive_skips >= MAX_CONSECUTIVE_SKIPS:
            self.consecutive_pause_until = now + CONSECUTIVE_PAUSE_SECONDS
        self.last_skip_at[debounce_key] = now
        self._last_skip_sig = sig
        # B11：open_url 移出持锁上下文——改为独立守护线程执行，避免锁内
        # 进程级副作用（webbrowser.open 可能阻塞所有持锁接口）。返回值语义
        # 保持：动作已触发（skipped）；真正的打开失败在异步线程内记日志。
        url = self.lx.skip_next_url()
        if not url:
            return False, 'skip_failed:empty_url'
        try:
            threading.Thread(
                target=self._open_url_async, args=(url,), daemon=True
            ).start()
        except Exception as e:
            print(f"[自动切歌] 启动打开URL线程失败: {e}")
            return False, f'skip_failed:{e}'
        return True, 'skipped'

    def _open_url_async(self, url):
        """锁外异步打开 URL（B11）：独立线程中调用 open_url，失败仅记日志"""
        try:
            err = self.lx.open_url(url)
            if err:
                print(f"[自动切歌] open_url 失败: {err}")
        except Exception as e:
            print(f"[自动切歌] open_url 异常: {e}")

    def _sync_meta(self, index, snapshot):
        """回填 source/songmid 等元数据；返回是否有变化"""
        entry = self.music_list[index]
        changed = False
        if snapshot.get('songmid') and entry.get('songmid') != snapshot['songmid']:
            entry['songmid'] = snapshot['songmid']
            changed = True
        if snapshot.get('source') and entry.get('source') != snapshot['source']:
            entry['source'] = snapshot['source']
            changed = True
        return changed

    def _entry_brief(self, index):
        entry = self.music_list[index]
        return {
            'name': entry.get('name'),
            'singer': entry.get('singer') or '',
            'songmid': entry.get('songmid') or '',
        }

    # ------------------------------------------------------------------
    # 兼容旧调用（processmsg / 外部仍可能使用）
    # ------------------------------------------------------------------
    def compare(self, playingsong):
        """旧接口兼容：接受歌名或 snapshot dict，转为 apply_playing"""
        if isinstance(playingsong, dict):
            return self.apply_playing(playingsong)
        if not playingsong:
            return False, []
        return self.apply_playing({'name': playingsong, 'singer': '', 'songmid': '', 'source': '', 'playing': True})

    @staticmethod
    def is_chinese(string):
        return bool(re.search(r'[\u4e00-\u9fa5]', str(string)))

    @staticmethod
    def is_english(string):
        return bool(re.search(r'[a-zA-Z]', str(string)))
