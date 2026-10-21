# -*- coding: utf-8 -*-
"""
SongEntry：点歌队列结构化条目（方案 B 重构核心数据结构）

将旧的 string[]（纯歌名文本）升级为结构化条目，携带曲目元数据与队列状态：
  id        队列唯一标识（uuid4 字符串，跨会话稳定）
  name      歌名（洛雪搜索关键字 / 展示名）
  singer    歌手（可选，弹幕解析或洛雪回填）
  username  点歌人（弹幕用户，可选）
  source    播放源 kw/kg/tx/wy/mg（洛雪命中后回填，可选）
  songmid   曲目 id（洛雪命中后回填，精确绑定，可选）
  status    队列状态机：pending -> playing -> played
  playLater 是否稍后播放（入队时决定；首播/立即播放为 False）
  createdAt 入队时间戳（秒）

兼容策略：
  - from_value() 统一将「旧 string / 新 dict」转成完整 SongEntry dict，
    queue.json 旧数据（string[]）启动时经 migrate 升级为 SongEntry[]
  - 所有字段缺失时给出安全默认值，保证旧数据/脏数据不崩溃
"""
import re
import time
import uuid


def new_id():
    return uuid.uuid4().hex


def norm_name(name):
    """歌名归一化：去首尾空白、折叠空白（存储/展示用，保留原始大小写）"""
    if not name:
        return ''
    text = re.sub(r'\s+', ' ', str(name)).strip()
    # 全角空格/不间断空格折叠为普通空格后 trim
    text = text.replace('\u3000', ' ').replace('\xa0', ' ').strip()
    return text


def norm_key(name, singer=''):
    """去重判定键：中文按原样，英文忽略大小写与空白（仅用于匹配，不改存储）"""
    n = norm_name(name)
    s = norm_name(singer)
    return (n.lower(), s.lower())


def make_entry(name, singer='', username='', source='', songmid='', play_later=False, status='pending'):
    """创建一个新的 SongEntry dict"""
    now = int(time.time())
    return {
        'id': new_id(),
        'name': norm_name(name),
        'singer': norm_name(singer),
        'username': username or '',
        'source': source or '',
        'songmid': songmid or '',
        'status': status if status in ('pending', 'playing', 'played') else 'pending',
        'playLater': bool(play_later),
        'createdAt': now,
    }


def entry_name(entry):
    """从 SongEntry dict / 旧 string 中取展示歌名"""
    if isinstance(entry, dict):
        return str(entry.get('name') or '')
    return str(entry or '')


def entry_singer(entry):
    """从 SongEntry dict / 旧 string 中取歌手（旧数据无歌手）"""
    if isinstance(entry, dict):
        return str(entry.get('singer') or '')
    return ''


def from_value(value):
    """将旧 string / 新 dict 统一转为合法 SongEntry dict"""
    if isinstance(value, dict):
        name = norm_name(value.get('name'))
        if not name:
            return None
        status = value.get('status', 'pending')
        if status not in ('pending', 'playing', 'played'):
            status = 'pending'
        return {
            'id': str(value.get('id') or new_id()),
            'name': name,
            'singer': norm_name(value.get('singer')),
            'username': str(value.get('username') or ''),
            'source': str(value.get('source') or ''),
            'songmid': str(value.get('songmid') or ''),
            'status': status,
            'playLater': bool(value.get('playLater', False)),
            'createdAt': int(value.get('createdAt') or 0) or int(time.time()),
        }
    name = norm_name(value)
    if not name:
        return None
    return make_entry(name)


def migrate_list(raw_list):
    """将旧 string[] / 混合 list 升级为 SongEntry[]（丢弃无法识别的项）"""
    if not isinstance(raw_list, list):
        return []
    result = []
    for item in raw_list:
        entry = from_value(item)
        if entry:
            result.append(entry)
    return result


def find_index(entries, name, singer=''):
    """按归一化键查找条目下标；找不到返回 -1。
    匹配规则（歌名为唯一主键）：
      - 目标 singer 为空（快照/点歌未带歌手）→ 仅按歌名弱匹配首个条目；
      - 队列项 singer 为空（旧数据/未回填）→ 同样退化为仅按歌名匹配，
        避免"快照带 singer、队列项无 singer"时强匹配漏命中（导致切歌后同名歌重复插入）；
      - 双方 singer 均存在 → 要求归一化后 (name, singer) 完全一致（大小写/空白差异已归一化）。
    """
    target_key = norm_key(name, singer)
    target_name = target_key[0]
    if not target_name:
        return -1
    for i, entry in enumerate(entries):
        if isinstance(entry, dict):
            key = norm_key(entry.get('name'), entry.get('singer'))
        else:
            key = norm_key(entry, '')
        if key[0] and key[0] == target_name and (not target_key[1] or not key[1] or key == target_key):
            return i
    return -1
