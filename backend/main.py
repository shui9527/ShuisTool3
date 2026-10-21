# -*- coding: utf-8 -*-
"""
shuitool3 后端入口（重写版）

对照分析报告修复项：
  B1  路由直接引用 processmsg 模块名导致 500      -> 全局唯一 ProcessMsg 实例，路由全部走实例方法
  B2  biliclient_callback 点歌/切歌/礼物分支 pass -> 完整分发到 ProcessMsg.handle_message
  B3  main 与 processmsg 各建队列对象数据不互通   -> ProcessMsg 单例持有 settings/queue/lock
  B4  catch_playing 逻辑反写 + 无限递归          -> 修复于 makeMusicList（退避重试）
  B5  compare 静默覆盖首项                       -> 修复于 makeMusicList
  B6  空列表 pop(0) 崩溃                         -> 修复于 makeMusicList
  B7  开放平台密钥硬编码                          -> 移入 settings/gallery.openlive
  B8  disconnect 不真正停线程                    -> 调用 OpenLiveClient.stop_and_close() 同步等待
  B12 findPath 无 None 保护                      -> 增加找不到/非法路径处理
  B13 并发无锁                                   -> 全局 RLock + 写操作加锁
  B16 刷屏线程句柄不可控                          -> SenderManager 托管线程生命周期
  B17 cookies 明文回显                            -> 前端密码框掩码展示（后端结构不变）
  F12 update_queue_list split 越界               -> 安全解析 + 新增 /update_music_list 路由
  附加: queueCommands 逗号分隔未拆分              -> load_orders 拆分；open-music 误改 q_order_list 修复
"""

import os
import sys
import json
import queue
import glob
import re
import time
import datetime
import threading
import webbrowser
import atexit
from collections import OrderedDict

from flask import Flask, jsonify, request, Response, send_from_directory

# PyInstaller 打包适配：onefile 运行时 sys._MEIPASS 为解压目录（代码/静态资源只读），
# 数据目录（settings/temp/日志）落 EXE 同目录保证持久化；首次启动从内置模板复制 settings。
_BUNDLED = bool(getattr(sys, '_MEIPASS', None))
if _BUNDLED:
    BASE_DIR = sys._MEIPASS
    _APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
    _TPL_DIR = os.path.join(sys._MEIPASS, 'settings_template')
    _DST_DIR = os.path.join(_APP_DIR, 'settings')
    if os.path.isdir(_TPL_DIR) and not os.path.exists(os.path.join(_DST_DIR, 'settings.json')):
        try:
            import shutil as _shutil
            os.makedirs(_DST_DIR, exist_ok=True)
            for _n in os.listdir(_TPL_DIR):
                _s = os.path.join(_TPL_DIR, _n)
                if os.path.isfile(_s):
                    _shutil.copy2(_s, os.path.join(_DST_DIR, _n))
            print(f"[打包] 已从模板初始化 settings 到 {_DST_DIR}")
        except Exception as _e:
            print(f"[打包] 初始化 settings 失败: {_e}")
    ROOT_DIR = _APP_DIR
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    ROOT_DIR = os.path.normpath(os.path.join(BASE_DIR, '..'))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

SETTINGS_FILE = os.path.join(ROOT_DIR, 'settings', 'settings.json')
QUEUE_FILE = os.path.join(ROOT_DIR, 'settings', 'queue.json')
FONTDICT_FILE = os.path.join(ROOT_DIR, 'settings', 'font_dict.json')
COOKIES_FILE = os.path.join(ROOT_DIR, 'settings', 'cookies.json')
SENDER_CONFIG_FILE = os.path.join(ROOT_DIR, 'settings', 'sender_config.json')
AI_CONFIG_FILE = os.path.join(ROOT_DIR, 'settings', 'ai_config.json')
TEMP_DIR = os.path.join(ROOT_DIR, 'temp')
AI_LOG_FILE = os.path.join(TEMP_DIR, 'ai_chat.log')
FRONTEND_DIST = os.path.join(BASE_DIR, 'static')

# 实时弹幕持久化：按日期存临时 log（temp/logs/danmu_YYYY-MM-DD.log），
# 启动删除 7 天前文件；页面加载读当日记录恢复，弹幕实时追加写入，清空删当日文件
DANMU_LOG_DIR = os.path.join(TEMP_DIR, 'logs')
DANMU_LOG_KEEP_DAYS = 7
danmu_log_lock = threading.Lock()  # 并发追加写保护（SSE 推送线程多路回调）

from function.processmsg import ProcessMsg
from function.liveConnection import LiveConnection
from function.messagesender import SenderManager
from function.ai_chat import AiChatEngine
from function import song_entry  # 方案 B：SongEntry 结构化队列 + 旧数据迁移

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False

# ---------------------------------------------------------------------------
# 全局状态与持久化
# ---------------------------------------------------------------------------
lock = threading.RLock()  # B13 全局锁


def _read_json(path, default):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"读取 {path} 失败: {e}，使用默认值")
        return default


def _write_json(path, data):
    # B8：原子写——先写临时文件再 os.replace，进程崩溃/磁盘满不会损坏原文件
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    os.replace(tmp, path)


settings_temp = _read_json(SETTINGS_FILE, {})
all_queue_dict = _read_json(QUEUE_FILE, {'queue': [], 'music': []})

# 方案 B：queue.json 旧数据（music 为 string[]）启动时迁移为 SongEntry[]，
# 兼容旧数据并写入 version=2；此后队列条目统一为结构化 dict。
all_queue_dict.setdefault('queue', [])
all_queue_dict.setdefault('music', [])
all_queue_dict['music'] = song_entry.migrate_list(all_queue_dict['music'])
if all_queue_dict.get('version') != 2:
    all_queue_dict['version'] = 2
    _write_json(QUEUE_FILE, all_queue_dict)

# 连接标志启动复位（不信任磁盘持久化）：LiveConnection 是内存态，后端刚启动时
# 必然未连接。强制将 settings.json 中残留的 biliclientRunning 复位为 False，
# 否则 /connection_status 返回 true 会令前端误显"已连接"、按钮显示"断开"，
# 且 /connect_services 会被 "already running" 拦截无法重连。
settings_temp.setdefault('gallery', {})['biliclientRunning'] = False

# 排队/点歌开关启动初始化：初次打开（settings 无该字段）默认关闭并显式落盘，
# 已持久化的状态原样保留；后端弹幕处理 handle_message 读取该字段控制真实启停。
settings_temp.setdefault('queue', {}).setdefault('queueEnabled', False)
settings_temp.setdefault('music', {}).setdefault('musicEnabled', False)

# B3 全局唯一业务实例
# sender_mgr 先实例化（携带 sender_config.json 路径，供自动答谢/回复读取配置），
# 再注入 processmsg 作为触发通道
sender_mgr = SenderManager(COOKIES_FILE, SENDER_CONFIG_FILE)
# AI 弹幕聊天引擎：配置存 settings/ai_config.json，日志存 temp/ai_chat.log；
# 注入 processmsg 作为弹幕触发通道（内部独立线程调 AI + 复用 sender_mgr 发送）
ai_engine = AiChatEngine(AI_CONFIG_FILE, sender_mgr=sender_mgr, log_file=AI_LOG_FILE)
processmsg = ProcessMsg(all_queue_dict=all_queue_dict, settings_temp=settings_temp, lock=lock, sender_mgr=sender_mgr, ai_chat=ai_engine)

# 存储拆分迁移：cookies.json 仅保留凭据五字段，功能配置独立到 sender_config.json。
# 启动时检测 cookies.json 残留的 screeningMode/interval/text_list，迁移后删除，
# 保证旧版本数据无缝升级（迁移以"目标键不存在"为条件，不覆盖已保存的新配置）。
_MIGRATE_SENDER_KEYS = ('screeningMode', 'interval', 'text_list')
cookies_data = _read_json(COOKIES_FILE, {})
sender_config_data = _read_json(SENDER_CONFIG_FILE, {})
if any(k in cookies_data for k in _MIGRATE_SENDER_KEYS):
    migrated = False
    for k in _MIGRATE_SENDER_KEYS:
        if k in cookies_data and k not in sender_config_data:
            sender_config_data[k] = cookies_data[k]
            migrated = True
    if migrated:
        _write_json(SENDER_CONFIG_FILE, sender_config_data)
    for k in _MIGRATE_SENDER_KEYS:
        cookies_data.pop(k, None)
    _write_json(COOKIES_FILE, cookies_data)
    print("[迁移] cookies.json 残留功能配置已迁移到 sender_config.json")

live_connection = None  # 当前 LiveConnection 实例（供断开时真正停止）


def write_settings():
    with lock:
        _write_json(SETTINGS_FILE, settings_temp)


def write_queue():
    """queue.json 原子写（方案 B）：先写临时文件再 os.replace，避免写一半崩溃损坏队列"""
    with lock:
        # B15：礼物记录随 queue.json 持久化（惰性清理过期项），后端重启后恢复
        all_queue_dict['gift_records'] = processmsg.queuelistclass.gift_records()
        tmp_path = QUEUE_FILE + '.tmp'
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(all_queue_dict, f, ensure_ascii=False, indent=4)
        os.replace(tmp_path, QUEUE_FILE)


def _on_exit_reset_connection():
    """进程退出时把连接标志持久化为未连接，防止 settings.json 残留已连接状态"""
    try:
        with lock:
            settings_temp.setdefault('gallery', {})['biliclientRunning'] = False
            _write_json(SETTINGS_FILE, settings_temp)
    except Exception as e:
        print(f"退出时复位连接标志失败: {e}")


atexit.register(_on_exit_reset_connection)


# ---------------------------------------------------------------------------
# SSE 推送（替代前端轮询）
# ---------------------------------------------------------------------------
SSE_MAX_CLIENTS = 5
# 订阅者"不活跃"判定阈值（秒）：超过该时长未消费任何数据/心跳的连接视为死连接，
# 淘汰时优先清理，避免误伤仍活跃的多页面/多设备订阅者（旧实现无条件踢最旧，
# 活跃的主页连接被踢后前端 onerror 重连再踢别人，形成"踢来踢去"导致状态来回跳）
SSE_INACTIVE_TIMEOUT = 15
sse_clients = OrderedDict()  # 有序订阅表：key=队列，value=最后活跃时间戳（time.time()）
sse_clients_lock = threading.Lock()


def sse_broadcast(event: str, data):
    """向所有 SSE 订阅者推送一条消息（单通道 data 行，载荷含事件名）"""
    try:
        payload = f"data: {json.dumps({'event': event, 'data': data}, ensure_ascii=False)}\n\n"
    except Exception as e:
        print(f"SSE 序列化失败: {e}")
        return
    with sse_clients_lock:
        dead = []
        for q in sse_clients:
            try:
                q.put_nowait(payload)
            except queue.Full:
                # 队列满：跳过本消息但保留订阅者，避免误删仍活跃的连接
                # （页面刷新瞬间旧连接短暂未消费时，被误删会导致前端状态失联）
                pass
            except Exception:
                dead.append(q)
        for q in dead:
            sse_clients.discard(q)


@app.route('/api/stream')
@app.route('/events')
def api_stream():
    """SSE 事件流：queue_changed / music_changed / settings_changed /
    connection_changed / sender_changed / danmu"""
    q = queue.Queue(maxsize=200)

    def gen():
        with sse_clients_lock:
            sse_clients[q] = time.time()
            sse_clients.move_to_end(q)
            # 淘汰策略：优先清理"超过 SSE_INACTIVE_TIMEOUT 秒未活跃"的死连接
            # （页面已关闭/网络已断，gen 未及时退出），避免误伤仍活跃的订阅者；
            # 全部活跃仍超限时才保底淘汰最旧订阅者
            stale = [k for k, v in sse_clients.items() if (v or 0) < time.time() - SSE_INACTIVE_TIMEOUT]
            for old in stale:
                sse_clients.pop(old, None)
                try:
                    old.put_nowait(None)
                except Exception:
                    pass
            if len(sse_clients) > SSE_MAX_CLIENTS:
                while len(sse_clients) > SSE_MAX_CLIENTS:
                    oldest_q, _ = sse_clients.popitem(last=False)
                    try:
                        oldest_q.put_nowait(None)
                    except Exception:
                        pass
        try:
            yield "retry: 3000\n\n"
            while True:
                try:
                    msg = q.get(timeout=1.5)
                    if msg is None:
                        break
                    yield msg
                except queue.Empty:
                    # 心跳：改 data 型心跳（前端以"收到推送"判定已连接，
                    # 注释行不会触发 EventSource.onmessage）；间隔 1.5s，
                    # 保证前端 2s 无推送判定窗口内必有心跳，避免临界抖动
                    with sse_clients_lock:
                        sse_clients[q] = time.time()
                    yield "data: {\"event\":\"heartbeat\"}\n\n"
        except GeneratorExit:
            pass
        finally:
            with sse_clients_lock:
                sse_clients.pop(q, None)

    return Response(gen(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no',
    })


# ---------------------------------------------------------------------------
# 洛雪音乐开放 API 代理（LX Music Desktop openApi，默认 127.0.0.1:23330）
# 前端经同源代理访问，规避浏览器直连 127.0.0.1:23330 的跨域失败并统一错误提示。
# 播放状态 SSE：/subscribe-player-status 推送 JSON，字段：
#   status/name/singer/albumName/duration/progress/playbackRate/picUrl/lyricLineText
#   - progress 单位为秒；lyricLineText 是当前行文本（未启用逐句歌词时为"歌手 - 歌名"）
# 逐句歌词：/lyric 返回 LRC 文本（可 ?filter=lrc），需另拉取解析。
# ---------------------------------------------------------------------------
LX_BASE = 'http://127.0.0.1:23330'
LX_TIMEOUT = 5


def _lx_get(path):
    """GET 洛雪开放 API；成功返回 (status, body_text)，失败返回 (None, 错误信息)"""
    import urllib.request
    try:
        with urllib.request.urlopen(f'{LX_BASE}{path}', timeout=LX_TIMEOUT) as resp:
            return resp.status, resp.read().decode('utf-8', 'replace')
    except Exception as e:
        return None, str(e)


@app.route('/api/lx/status')
def lx_status():
    status, body = _lx_get('/status')
    if status is None:
        return jsonify({'error': f'LX Music 开放 API 未连接: {body}'}), 503
    return Response(body, mimetype='application/json'), 200


@app.route('/api/lx/lyric')
def lx_lyric():
    """逐句歌词 LRC 文本，供前端切歌时拉取解析"""
    # 无参调用：洛雪 /lyric 无 filter 参数，默认即返回当前歌曲纯文本 LRC；
    # 移除原 ?filter=lrc（官方无效参数），避免部分版本返回 JSON 包装
    status, body = _lx_get('/lyric')
    if status is None:
        return jsonify({'error': f'LX Music 开放 API 未连接: {body}'}), 503
    # JSON 包装返回：前端 http 封装会把以 '[' 开头的文本误判为 JSON 导致
    # parse 失败抛错（LRC 恰好以 [offset:0] 开头），故统一包成 {'lyric': ...}
    # 供前端 normalizeLrcText 提取，避免 lyricLines 永远为空回退单行
    return jsonify({'lyric': body}), 200


def _sse_data_to_song(data_text):
    """从洛雪 SSE 帧 data 行解析 (name, singer)。
    data 为 JSON 值（字符串带引号 / 对象）或裸文本歌名：
      - JSON 字符串 -> 歌名
      - JSON 对象 -> 取 name / singer 字段
      - 裸文本 -> 整体视为歌名
    """
    text = (data_text or '').strip()
    if not text:
        return '', ''
    try:
        value = json.loads(text)
    except Exception:
        value = text
    if isinstance(value, dict):
        return str(value.get('name') or ''), str(value.get('singer') or '')
    if isinstance(value, str):
        return value.strip(), ''
    return '', ''


def _auto_skip_removed_on_sse(name, singer=''):
    """SSE 新歌事件 -> 统一自动切歌检测（锁内调用 check_and_skip_removed）"""
    try:
        with lock:
            ok, reason = processmsg.musiclistclass.check_and_skip_removed(name, singer)
        if ok:
            print(f"[SSE自动切歌] 已跳过被移除歌曲: {name} {singer}".rstrip())
    except Exception as e:
        print(f"[SSE自动切歌] 失败: {e}")


@app.route('/api/lx/subscribe')
def lx_subscribe():
    """SSE 代理：透传洛雪 /subscribe-player-status 播放状态推送。

    对前端保持原透传行为完全不变（歌词/状态/连接判定依赖的事件与 data 格式
    原样 yield）；额外在服务端解析 event: name 帧的 data 值作为新歌名，
    命中 removed_keys（10 分钟 TTL 内）时调用 check_and_skip_removed 自动切歌
    （路线B），未命中任何条件时零副作用。

    B16：自动切歌依赖 removed_keys 的 10 分钟 TTL，主播删除歌曲后超过
    10 分钟的弹幕不再触发切歌，属已知行为设计（非缺陷）。
    """
    import urllib.request

    def gen():
        req = urllib.request.Request(f'{LX_BASE}/subscribe-player-status')
        try:
            with urllib.request.urlopen(req, timeout=None) as resp:
                event_name = None
                data_parts = []
                while True:
                    line = resp.readline()
                    if not line:
                        break
                    text = line.decode('utf-8', 'replace')
                    # 透传：原样 yield 每一行（前端行为完全不变）
                    yield text
                    # 附加解析（不影响透传）：识别 event: name 帧的 data 新歌名
                    s = text.strip()
                    if not s:
                        # 帧结束（空行）：event: name + data 已齐 -> 触发自动切歌检测
                        if event_name == 'name' and data_parts:
                            song_name, song_singer = _sse_data_to_song(''.join(data_parts))
                            if song_name:
                                _auto_skip_removed_on_sse(song_name, song_singer)
                        event_name = None
                        data_parts = []
                    elif s.startswith('event:'):
                        event_name = s[len('event:'):].strip()
                        data_parts = []
                    elif s.startswith('data:') and event_name:
                        data_parts.append(s[len('data:'):].strip())
                    else:
                        # 未知行：丢弃当前累积事件，避免脏数据粘连
                        event_name = None
                        data_parts = []
        except GeneratorExit:
            pass
        except Exception as e:
            yield f"data: {json.dumps({'error': 'lx_disconnected', 'message': str(e)}, ensure_ascii=False)}\n\n"

    return Response(gen(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no',
    })


# ---------------------------------------------------------------------------
# 弹幕持久化（按日期 log）
# ---------------------------------------------------------------------------
def _danmu_log_path(day=None):
    day = day or datetime.date.today().strftime('%Y-%m-%d')
    return os.path.join(DANMU_LOG_DIR, 'danmu_%s.log' % day)


def danmu_append_log(msg_dict):
    """弹幕实时追加写入当日 log（每行一条 JSON），并发写加锁"""
    try:
        os.makedirs(DANMU_LOG_DIR, exist_ok=True)
        entry = {
            'time': datetime.datetime.now().strftime('%H:%M:%S'),
            'type': msg_dict.get('type', ''),
            'username': msg_dict.get('username', ''),
            'danmu': msg_dict.get('danmu', ''),
        }
        line = json.dumps(entry, ensure_ascii=False)
        with danmu_log_lock:
            with open(_danmu_log_path(), 'a', encoding='utf-8') as f:
                f.write(line + '\n')
    except Exception as e:
        print(f"写入弹幕日志失败: {e}")


def danmu_read_today():
    """读取当日弹幕记录（日志按写入顺序，即时间正序）"""
    try:
        path = _danmu_log_path()
        if not os.path.exists(path):
            return []
        logs = []
        with danmu_log_lock:
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        logs.append(json.loads(line))
                    except Exception:
                        continue
        return logs
    except Exception as e:
        print(f"读取弹幕日志失败: {e}")
        return []


def danmu_clear_today():
    """清空当日弹幕记录：删除当日日志文件（写锁保护，避免与追加写竞态）"""
    try:
        path = _danmu_log_path()
        with danmu_log_lock:
            if os.path.exists(path):
                os.remove(path)
        return True
    except Exception as e:
        print(f"清空弹幕日志失败: {e}")
        return False


def danmu_cleanup_old():
    """启动清理：删除 7 天前的弹幕记录文件（按文件名日期判断）"""
    try:
        if not os.path.isdir(DANMU_LOG_DIR):
            return
        cutoff = datetime.date.today() - datetime.timedelta(days=DANMU_LOG_KEEP_DAYS)
        for p in glob.glob(os.path.join(DANMU_LOG_DIR, 'danmu_*.log')):
            name = os.path.basename(p)
            date_part = name[len('danmu_'):-len('.log')]
            try:
                d = datetime.datetime.strptime(date_part, '%Y-%m-%d').date()
            except ValueError:
                continue
            if d < cutoff:
                try:
                    os.remove(p)
                    print(f"已删除过期弹幕日志: {p}")
                except Exception as e:
                    print(f"删除过期弹幕日志失败 {p}: {e}")
    except Exception as e:
        print(f"清理弹幕日志失败: {e}")


# ---------------------------------------------------------------------------
# 弹幕接入
# ---------------------------------------------------------------------------
def biliclient_callback(msg_dict):
    """B站开放平台回调：分发给统一业务实例（B2/B3）"""
    try:
        changed = processmsg.handle_message(msg_dict)
        # 队列/点歌变更立即持久化到 queue.json：仅靠 SSE 广播只同步其它在线页面，
        # 不落盘则后端重启后弹幕产生的排队/点歌全部丢失；写入保证刷新/重启均可恢复
        if changed.get('queue') or changed.get('music'):
            write_queue()
        if changed.get('queue'):
            sse_broadcast('queue_changed', {'queue': all_queue_dict.get('queue', []), 'music': all_queue_dict.get('music', [])})
        if changed.get('music'):
            sse_broadcast('music_changed', {'music': all_queue_dict.get('music', []), 'queue': all_queue_dict.get('queue', [])})
        sse_broadcast('danmu', {
            'type': msg_dict.get('type'),
            'username': msg_dict.get('username', ''),
            'danmu': msg_dict.get('danmu', ''),
        })
        # 弹幕持久化：实时追加写入当日日志（页面加载时据此恢复）
        danmu_append_log(msg_dict)
    except Exception as e:
        print(f"处理弹幕消息时出错: {e}")


def biliclient_thread(livecode, callback, started_event=None, result_box=None):
    """后台线程：持有事件循环运行 LiveConnection（B8 可在断开时停止）"""
    global live_connection
    loop = None
    try:
        loop = asyncio_new_event_loop()
        async def _run():
            global live_connection
            live_connection = LiveConnection(livecode, callback, started_event=started_event)
            # B14：超时取消标记——构造后若已 cancelled 则不启动连接
            if result_box is not None and result_box.get('cancelled'):
                return
            await live_connection.start()

        loop.run_until_complete(_run())
        # B14：start 返回后若被取消，则停止刚建立的连接（避免"界面未连接、实际已连"）
        if result_box is not None and result_box.get('cancelled') and live_connection is not None:
            try:
                live_connection.stop_sync()
            except Exception as e:
                print(f"取消后停止客户端失败: {e}")
    except Exception as e:
        print(f"BiliClient 线程异常退出: {e}")
        if result_box is not None:
            result_box['error'] = str(e)
        # 连接失败必须复位运行标志，否则残留 true 会拦截后续重连（“无法连接”根因）
        try:
            with lock:
                settings_temp.setdefault('gallery', {})['biliclientRunning'] = False
                write_settings()
        except Exception as e2:
            print(f"复位连接标志失败: {e2}")
    finally:
        if loop is not None:
            try:
                loop.close()
            except Exception:
                pass


def asyncio_new_event_loop():
    import asyncio
    try:
        return asyncio.new_event_loop()
    except Exception:
        return asyncio.get_event_loop()


@app.route('/connect_services', methods=['POST'])
def connect_services():
    import time
    if request.is_json:
        body = request.json or {}
        livecode = body.get('code') or body.get('livecode')
    else:
        livecode = request.form.get('livecode') or request.form.get('code')
    if not livecode:
        return jsonify({'error': '直播码不能为空'}), 400
    with lock:
        if settings_temp.get('gallery', {}).get('biliclientRunning'):
            return jsonify({'error': 'BiliClient is already running，请先断开'}), 400
        started_event = threading.Event()
        result_box = {}
        t = threading.Thread(
            target=biliclient_thread,
            args=(livecode, biliclient_callback, started_event, result_box),
            daemon=True,
        )
        t.start()
    # 等待线程内连接尝试的真实结果：成功 set 事件；失败/超时均视为未连接并复位标志
    deadline = time.time() + 15
    while time.time() < deadline:
        if started_event.is_set():
            break
        if result_box.get('error'):
            break
        time.sleep(0.2)
    if not started_event.is_set():
        with lock:
            settings_temp.setdefault('gallery', {})['biliclientRunning'] = False
            write_settings()
        # B14：标记取消，令后台线程不再启动/立即停止刚建立的连接
        result_box['cancelled'] = True
        conn = None
        with lock:
            conn = live_connection
        if conn is not None:
            # 后台线程可能已构造 LiveConnection 并正在连接/已连上，异步 stop_sync，
            # 防止"界面显示未连接、实际已连且无法界面断开"
            def _do_stop():
                try:
                    conn.stop_sync()
                except Exception as e:
                    print(f"超时停止客户端出错: {e}")

            threading.Thread(target=_do_stop, daemon=True).start()
        err = result_box.get('error') or '连接超时，请检查 openlive 密钥与直播码后重试'
        return jsonify({'error': err}), 500
    with lock:
        settings_temp.setdefault('gallery', {})['biliclientRunning'] = True
        # 连接成功后回填直播码：与前端 gallery.livecode 自动保存字段保持一致，
        # 保证后续 /connect_services 读取的 livecode 即页面默认值来源
        settings_temp.setdefault('gallery', {})['livecode'] = livecode
        write_settings()
    sse_broadcast('connection_changed', {'biliclientRunning': True})
    return jsonify({'success': 'BiliClient is connected'}), 200


@app.route('/disconnect_services')
def disconnect_services():
    global live_connection
    conn = None
    with lock:
        # 快速路径：锁内仅摘除引用 + 复位标志（毫秒级），不阻塞其它接口
        conn = live_connection
        live_connection = None
        settings_temp['gallery']['biliclientRunning'] = False
        write_settings()
    # 锁外真正停止客户端：stop_sync 可能阻塞数秒（等待子线程事件循环收尾），
    # 放到独立线程执行，避免持有全局锁拖死刷新页面所需的 /loadSettings 等接口
    if conn is not None:
        def _do_stop():
            try:
                conn.stop_sync()
            except Exception as e:
                print(f"停止客户端出错: {e}")

        threading.Thread(target=_do_stop, daemon=True).start()
    sse_broadcast('connection_changed', {'biliclientRunning': False})
    return jsonify({'success': 'BiliClient is disconnected'}), 200


@app.route('/connection_status')
def connection_status():
    return jsonify({'biliclientRunning': bool(settings_temp.get('gallery', {}).get('biliclientRunning'))}), 200


# ---------------------------------------------------------------------------
# 设置读写
# ---------------------------------------------------------------------------
@app.route('/loadSettings')
def loadSettings():
    return jsonify(settings_temp), 200


def _flat_to_nested(form):
    """将 {'key': 'a.b.c', 'value': v} 转为嵌套 dict {'a': {'b': {'c': v}}}"""
    key = (form.get('key') or '').strip()
    if not key or 'value' not in form:
        return {}
    parts = key.split('.')
    result = {}
    node = result
    for part in parts[:-1]:
        node[part] = {}
        node = node[part]
    node[parts[-1]] = form.get('value')
    return result


def _expand_dotted_keys(data):
    """将点号键 JSON（{'queue.titleText': v}）展开为嵌套 dict（{'queue': {'titleText': v}}）。

    前端旧 saveSection 逐字段提交 'section.key' 点号键，若直接 merge 会被当成
    顶层扁平键追加进 settings_temp（'settings_temp[\"queue.titleText\"]'），导致
    settings_temp['queue'] 永远不更新、所有设置项保存后不生效。此处统一展开。
    值本身为 dict 时递归展开，兼容嵌套键（如 gallery.openlive.appId）。
    """
    result = {}
    for key, value in data.items():
        if isinstance(value, dict):
            expanded = _expand_dotted_keys(value)
            if isinstance(key, str) and '.' in key:
                parts = key.split('.')
                node = result
                for part in parts[:-1]:
                    node = node.setdefault(part, {})
                node[parts[-1]] = expanded
            else:
                result[key] = expanded
        elif isinstance(key, str) and '.' in key:
            parts = key.split('.')
            node = result
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = value
        else:
            result[key] = value
    return result


@app.route('/changeValue', methods=['POST'])
def get_settings_change():
    if request.is_json:
        data = _expand_dotted_keys(request.json or {})
    else:
        data = _flat_to_nested(request.form.to_dict())
    if not isinstance(data, dict) or not data:
        return jsonify({'error': 'invalid body'}), 400
    with lock:
        merge_dicts(data, settings_temp)
        write_settings()
        # 队列/点歌命令或开关变化时同步 orders 与持久化开关
        processmsg.refresh_orders()
    sse_broadcast('settings_changed', data)
    return jsonify({'success': 'Data received'}), 200


def merge_dicts(updata, target):
    """递归合并，仅更新目标中已存在的键，避免污染配置结构"""
    for key, value in updata.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            merge_dicts(value, target[key])
        elif key in target:
            target[key] = value
        else:
            # 未知键直接追加（保留灵活性）
            target[key] = value
    return target


# 中文字体英文注册表名 -> 中文显示名（本机注册表字体值名多为英文，如 "SimHei"、"Microsoft YaHei & Microsoft YaHei UI"）
_FONT_ZH_MAP = {
    'Microsoft YaHei': '微软雅黑',
    'Microsoft YaHei UI': '微软雅黑',
    'Microsoft YaHei Light': '微软雅黑 Light',
    'Microsoft JhengHei': '微软正黑体',
    'Microsoft JhengHei UI': '微软正黑体',
    'SimHei': '黑体',
    'SimSun': '宋体',
    'NSimSun': '新宋体',
    'KaiTi': '楷体',
    'KaiTi_GB2312': '楷体_GB2312',
    'FangSong': '仿宋',
    'FangSong_GB2312': '仿宋_GB2312',
    'DengXian': '等线',
    'DengXian Light': '等线 Light',
    'YouYuan': '幼圆',
    'LiSu': '隶书',
    'STLiti': '华文隶书',
    'STSong': '华文宋体',
    'STZhongsong': '华文中宋',
    'STFangsong': '华文仿宋',
    'STKaiti': '华文楷体',
    'STXihei': '华文黑体',
    'STXingkai': '华文行楷',
    'STCaiyun': '华文彩云',
    'STHupo': '华文琥珀',
    'STXinwei': '华文新魏',
    'FZShuTi': '方正舒体',
    'FZXingKai': '方正行楷',
    'FZYaoTi': '方正姚体',
    'FZXiaoBiaoSong': '方正小标宋',
    'FZXiaoBiaoSong-Bold': '方正小标宋',
    'FZSongYi-B13': '方正宋一',
    'FZHei-B01': '方正黑体',
    'FZKai-Z03': '方正楷体',
    'FZFangSong-Z02': '方正仿宋',
    'SimSun-ExtB': '宋体扩展B',
    'SimSun-ExtG': '宋体扩展G',
}

# 常用中文字体置顶（按给定顺序），value 直接使用中文字体名
_FONT_COMMON = [
    '微软雅黑', '黑体', '宋体', '仿宋', '楷体', '幼圆', '隶书', '等线',
    '微软雅黑 Light', '新宋体', '华文中宋', '华文楷体', '华文仿宋', '华文黑体',
    '华文行楷', '华文隶书', '方正舒体', '方正行楷', '方正姚体', '方正小标宋',
]


def _enum_system_fonts():
    """动态枚举 Windows 系统中文字体：注册表优先，C:\\Windows\\Fonts 中文文件名兜底。

    仅保留中文字体：注册表值名含中文的直接保留，值名为英文的经 _FONT_ZH_MAP
    映射为中文后保留（映射未命中说明非中文字体，丢弃）。
    返回 [{'name': 中文字体名, 'value': 中文字体名}]，常用字体置顶，其余按拼音（GBK）排序。
    """
    families = set()
    # 1) 注册表枚举：HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Fonts
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r'SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts')
        idx = 0
        while True:
            try:
                name, _val, _type = winreg.EnumValue(key, idx)
                idx += 1
            except OSError:
                break
            clean = re.sub(r'\s*\((TrueType|OpenType|Type 1)\)\s*$', '', name).strip()
            if not clean:
                continue
            parts = [p.strip() for p in re.split(r'\s*&\s*', clean) if p.strip()]
            # 值名含中文：直接保留中文部件
            zh_parts = [p for p in parts if re.search(r'[\u4e00-\u9fff]', p)]
            if zh_parts:
                for p in zh_parts:
                    families.add(p)
            else:
                # 值名为英文：仅保留映射命中项（即中文字体）
                for p in parts:
                    disp = _FONT_ZH_MAP.get(p)
                    if disp:
                        families.add(disp)
    except Exception:
        pass
    # 2) 兜底：扫描 C:\\Windows\\Fonts 中中文命名的字体文件
    try:
        font_dir = r'C:\Windows\Fonts'
        if os.path.isdir(font_dir):
            for fn in os.listdir(font_dir):
                if not fn.lower().endswith(('.ttf', '.ttc', '.otf')):
                    continue
                base = os.path.splitext(fn)[0]
                if base and re.search(r'[\u4e00-\u9fff]', base):
                    families.add(base)
    except Exception:
        pass
    # 3) 常用字体置顶（按给定顺序），value 直接用中文字体名本身
    result = []
    seen = set()
    for disp in _FONT_COMMON:
        if disp in families:
            result.append({'name': disp, 'value': disp})
            seen.add(disp)
    # 4) 其余按拼音（GBK）近似排序
    rest = []
    for fam in sorted(families, key=lambda s: s.encode('gbk', errors='ignore')):
        if fam in seen:
            continue
        rest.append({'name': fam, 'value': fam})
    return result + rest


@app.route('/setting/fontdict')
def fontdict():
    return jsonify({'font_dict': _enum_system_fonts()})


# ---------------------------------------------------------------------------
# 队列功能
# ---------------------------------------------------------------------------
@app.route('/open-queue')
def open_queue():
    with lock:
        processmsg.open_queue()
        write_settings()
    sse_broadcast('queue_status_changed', {'queueEnabled': True})
    return jsonify({'success': 'queue opened'}), 200


@app.route('/close-queue')
def close_queue():
    with lock:
        processmsg.close_queue()
        write_settings()
    sse_broadcast('queue_status_changed', {'queueEnabled': False})
    return jsonify({'success': 'queue closed'}), 200


@app.route('/clear-queue')
def clear_queue():
    with lock:
        processmsg.clear_queue()
        write_queue()
    sse_broadcast('queue_changed', {'queue': all_queue_dict.get('queue', []), 'music': all_queue_dict.get('music', [])})
    return jsonify({'success': 'queue cleared'}), 200


@app.route('/get_queue')
def get_queue():
    return jsonify({
        'queue': all_queue_dict.get('queue', []),
        'music': all_queue_dict.get('music', []),
        'all_queue': all_queue_dict,
        # B15：礼物记录（含过期时间），前端礼物排队模式下展示送礼价值
        'gift_records': processmsg.queuelistclass.gift_records(),
        # 开关真实状态随列表一并返回：前端刷新/打开页面时据此恢复开关显示，
        # 与 settings 持久化状态保持一致（前端不依赖 SSE 广播也能同步）
        'queueEnabled': settings_temp.get('queue', {}).get('queueEnabled', False),
        'musicEnabled': settings_temp.get('music', {}).get('musicEnabled', False),
    }), 200


@app.route('/api/danmu_logs')
def api_danmu_logs():
    """实时弹幕持久化：读取当日弹幕记录（页面加载时恢复）"""
    return jsonify({'logs': danmu_read_today()}), 200


@app.route('/api/danmu_logs/clear', methods=['POST'])
def api_danmu_logs_clear():
    """实时弹幕持久化：清空当日弹幕记录（删除当日日志文件）"""
    ok = danmu_clear_today()
    if ok:
        return jsonify({'success': 'danmu logs cleared'}), 200
    return jsonify({'error': 'clear failed'}), 500


@app.route('/update_queue_list', methods=['POST'])
def update_queue_list():
    if request.is_json:
        data = request.json or {}
        new_queue_list = data.get('queue_list')
        if new_queue_list is None:
            new_queue_list = data.get('list')
    else:
        raw = request.form.get('list', '')
        new_queue_list = raw.split(',') if raw else []
    if not isinstance(new_queue_list, list):
        return jsonify({'error': 'queue_list must be a list'}), 400
    cleaned = _clean_numbered_list(new_queue_list)
    with lock:
        all_queue_dict['queue'] = cleaned
        processmsg.queuelistclass.queue_list = cleaned
        write_queue()
    sse_broadcast('queue_changed', {'queue': all_queue_dict.get('queue', []), 'music': all_queue_dict.get('music', [])})
    return jsonify({'success': 'queue list updated'}), 200


# ---------------------------------------------------------------------------
# 点歌功能（方案 B：SongEntry 结构化 + 队列状态机 + lx-bridge + sync_playing 回传）
# ---------------------------------------------------------------------------
@app.route('/open-music')
def open_music():
    with lock:
        processmsg.open_music()
        write_settings()
    sse_broadcast('music_status_changed', {'musicEnabled': True})
    return jsonify({'success': 'music opened'}), 200


@app.route('/close-music')
def close_music():
    with lock:
        processmsg.close_music()
        write_settings()
    sse_broadcast('music_status_changed', {'musicEnabled': False})
    return jsonify({'success': 'music closed'}), 200


def _music_payload():
    return {'music': all_queue_dict.get('music', []), 'queue': all_queue_dict.get('queue', [])}


def _broadcast_music_events(events):
    """将状态机事件（song_started / song_ended）广播给 SSE 订阅者（H3）"""
    for ev in events or []:
        try:
            sse_broadcast(ev['event'], ev.get('data') or {})
        except Exception as e:
            print(f"广播音乐事件失败 {ev}: {e}")


@app.route('/nextMusic')
def next_music():
    # L6：路由校验点歌开关，未开启直接拒绝并反馈
    if not settings_temp.get('music', {}).get('musicEnabled', False):
        return jsonify({'error': '点歌功能未开启'}), 400
    with lock:
        ok, reason = processmsg.next_music()
    if ok:
        write_queue()
        sse_broadcast('music_changed', _music_payload())
        return jsonify({'success': 'Skin Music'}), 200
    return jsonify({'error': reason or '点歌队列为空'}), 400


@app.route('/catch_playing')
def catch_playing():
    """同步洛雪当前播放到队列（M4：HTTP 轮询在锁外执行，锁内仅做队列对齐）"""
    if not settings_temp.get('music', {}).get('musicEnabled', False):
        return jsonify({'error': '点歌功能未开启'}), 400
    try:
        changed = processmsg.catch_playing()
    except Exception as e:
        print(f"catch_playing 出错: {e}")
        return jsonify({'error': f'同步失败: {e}'}), 500
    if changed:
        write_queue()
        sse_broadcast('music_changed', _music_payload())
    return jsonify({'success': 'catch_playing'}), 200


@app.route('/sync_playing', methods=['POST'])
def sync_playing():
    """前端回传洛雪播放状态变化，驱动队列状态机（H3 通道一）：
    payload = {'name','singer','songmid','status'}，status: playing|paused|ended|stop
    - playing/paused：对齐队列头部与洛雪实际播放
    - ended/stop：头部 playing 项标记 played 并 pop（自动推进）
    """
    if not settings_temp.get('music', {}).get('musicEnabled', False):
        return jsonify({'error': '点歌功能未开启'}), 400
    payload = request.json or {}
    with lock:
        changed, events, err = processmsg.musiclistclass.sync_playing(payload)
    if err:
        return jsonify({'error': err}), 400
    if changed:
        write_queue()
        sse_broadcast('music_changed', _music_payload())
        _broadcast_music_events(events)
    return jsonify({'success': True}), 200


@app.route('/update_music_list', methods=['POST'])
def update_music_list():
    data = request.json or {}
    new_music_list = data.get('music_list')
    if not isinstance(new_music_list, list):
        return jsonify({'error': 'music_list must be a list'}), 400
    # 兼容：旧 string[] / 新 SongEntry[] 统一迁移为 SongEntry[]（dict 原样保留字段）
    cleaned = song_entry.migrate_list(new_music_list)
    with lock:
        # 计算被移除的歌曲（旧列表有、新列表无），写入状态机"已移除"集合：
        # 洛雪 playLater 预排队导致被移除歌曲仍可能在洛雪侧播放，若不记录，
        # 后台 5s 轮询 / 前端 sync_playing 回传会把该歌重新插回队列——
        # 表现即"只剩最后一项时点移除没反应 / 移除后又出现"。
        old_list = all_queue_dict.get('music', [])
        old_keys = {song_entry.norm_key(song_entry.entry_name(e), song_entry.entry_singer(e)) for e in old_list}
        new_keys = {song_entry.norm_key(song_entry.entry_name(e), song_entry.entry_singer(e)) for e in cleaned}
        for key in old_keys - new_keys:
            if key[0]:
                # 路线B：removed_keys 改为 key -> 移除时间戳（秒），TTL=REMOVED_TTL（10 分钟）
                processmsg.musiclistclass.removed_keys[key] = time.time()
        all_queue_dict['music'] = cleaned
        processmsg.musiclistclass.music_list = cleaned
        write_queue()
    sse_broadcast('music_changed', _music_payload())
    return jsonify({'success': 'music list updated'}), 200


@app.route('/add_music', methods=['POST'])
def add_music():
    """手动添加点歌：复用 makeMusicList.add_music_list 状态机。
    自动去重（name+singer 归一化）、上限 10、空队列立即播放（play_later=False）、
    非空队列 playLater 入洛雪尾部、清除 removed_keys；返回添加后的新队列。"""
    data = request.json or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name is required'}), 400
    singer = (data.get('singer') or '').strip()
    with lock:
        ok, reason = processmsg.musiclistclass.add_music_list({
            'song': name, 'singer': singer, 'username': '',
        })
        if not ok:
            return jsonify({'error': reason}), 400
        write_queue()
        sse_broadcast('music_changed', _music_payload())
    return jsonify({'success': True, 'music': all_queue_dict.get('music', [])}), 200


# 后台播放监控：周期性轮询洛雪 /status，自动推进队列状态机（H3 通道二）。
# 洛雪无「播放完成」事件时，靠轮询发现"洛雪已换歌" -> 头部 playing 项 pop 并标记
#  played，再对齐新歌；HTTP 在锁外执行，锁内仅做内存对齐，不阻塞业务请求（M4）。
MUSIC_MONITOR_INTERVAL = 5  # 秒


def music_monitor_loop():
    while True:
        time.sleep(MUSIC_MONITOR_INTERVAL)
        try:
            if not settings_temp.get('music', {}).get('musicEnabled', False):
                continue
            snapshot, err = processmsg.musiclistclass.snapshot_playing()
            if snapshot is None:
                continue
            with lock:
                changed, events = processmsg.musiclistclass.apply_playing(snapshot)
            if changed:
                write_queue()
                sse_broadcast('music_changed', _music_payload())
                _broadcast_music_events(events)
        except Exception as e:
            print(f"音乐监控线程出错: {e}")


def _clean_numbered_list(items):
    """兼容展示页拖拽数据 '1. xxx'，去重、去空、修复 F12 split 越界"""
    cleaned = []
    for item in items:
        text = str(item).strip()
        parts = text.split('.', 1)
        if len(parts) == 2 and parts[0].strip().isdigit():
            text = parts[1].strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


# ---------------------------------------------------------------------------
# 自动刷屏
# ---------------------------------------------------------------------------
def _mask_credential(value):
    """B3 凭据脱敏：保留前3后3字符，中间用 *** 替换；短于6字符整串掩码为 ******"""
    if not value:
        return ''
    if len(value) <= 6:
        return '******'
    return value[:3] + '***' + value[-3:]


@app.route('/get_cookies')
def get_cookies():
    # B3：仅返回凭据五字段（cookies.json 已拆分为纯凭据存储），敏感字段脱敏回显
    data = _read_json(COOKIES_FILE, {})
    return jsonify({
        'room_id': data.get('room_id', ''),
        'SESSDATA': _mask_credential(data.get('SESSDATA', '')),
        'bili_jct': _mask_credential(data.get('bili_jct', '')),
        'buvid3': _mask_credential(data.get('buvid3', '')),
        'dedeuserid': _mask_credential(data.get('dedeuserid', '')),
    }), 200


@app.route('/savecookies', methods=['POST'])
def save_account():
    data = request.json
    if not isinstance(data, dict):
        return jsonify({'error': 'invalid body'}), 400
    # B3：合并写入——先读回 cookies.json 原值，仅覆盖请求中提交的字段，未提交字段保留原值（禁止写空串）
    allowed = ('room_id', 'SESSDATA', 'bili_jct', 'buvid3', 'dedeuserid')
    with lock:
        payload = _read_json(COOKIES_FILE, {})
        for k in allowed:
            if k in data:
                payload[k] = data[k]
        _write_json(COOKIES_FILE, payload)
    # B2：cookies 更新后重建共享 MessageSender（下次发送自动懒加载新实例）
    sender_mgr.invalidate_sender()
    return jsonify({'success': True}), 200


@app.route('/get_sender_config')
def get_sender_config():
    # 功能配置独立文件：screeningMode/interval/text_list/thanksEnabled/thanksMode/thanks_list
    # + R7 事件分池 thanks_gift_list/thanks_sc_list/thanks_guard_list + reply 自动回复字段
    data = _read_json(SENDER_CONFIG_FILE, {})
    return jsonify({
        'screeningMode': data.get('screeningMode', 'loop') or 'loop',
        'interval': data.get('interval', 5),
        'text_list': data.get('text_list', []) or [],
        'thanksEnabled': bool(data.get('thanksEnabled', False)),
        'thanksMode': data.get('thanksMode', 'loop') or 'loop',
        'thanks_list': data.get('thanks_list', []) or [],
        'thanks_gift_list': data.get('thanks_gift_list', []) or [],
        'thanks_sc_list': data.get('thanks_sc_list', []) or [],
        'thanks_guard_list': data.get('thanks_guard_list', []) or [],
        'replyEnabled': bool(data.get('replyEnabled', False)),
        'replyMatchMode': data.get('replyMatchMode', 'contains') or 'contains',
        'replyCooldown': int(data.get('replyCooldown', 0) or 0),
        'replyIgnoreSelf': bool(data.get('replyIgnoreSelf', True)),
        'reply_list': data.get('reply_list', []) or [],
    }), 200


@app.route('/save_sender_config', methods=['POST'])
def save_sender_config():
    data = request.json
    if not isinstance(data, dict):
        return jsonify({'error': 'invalid body'}), 400
    allowed = ('screeningMode', 'interval', 'text_list', 'thanksEnabled', 'thanksMode', 'thanks_list',
               'thanks_gift_list', 'thanks_sc_list', 'thanks_guard_list',
               'replyEnabled', 'replyMatchMode', 'replyCooldown', 'replyIgnoreSelf', 'reply_list')
    # 风险4：合并写入——先读回 sender_config.json 原值，仅覆盖请求中提交的字段，
    # 未提交字段保留原值（与 /savecookies 语义对齐，禁止把未提交字段写为 None/默认值）
    with lock:
        payload = _read_json(SENDER_CONFIG_FILE, {})
        for k in allowed:
            if k in data:
                payload[k] = data[k]
        _write_json(SENDER_CONFIG_FILE, payload)
    # B9：写盘后立即重置 sender_mgr 的 0.5s TTL 配置缓存，新配置马上生效
    sender_mgr.flush_config()
    return jsonify({'success': True}), 200


@app.route('/start_sender')
def start_sender():
    # P0 修复：启动刷屏必须把已保存的 text_list / mode / interval 传入线程，
    # 否则线程以空列表启动只空转、永不发送弹幕
    # 存储拆分后这些参数改从 sender_config.json 读取
    cfg = _read_json(SENDER_CONFIG_FILE, {})
    text_list = cfg.get('text_list', []) or []
    mode = cfg.get('screeningMode', 'loop') or 'loop'
    interval = cfg.get('interval', 5)
    ok, msg = sender_mgr.start(text_list=text_list, mode=mode, interval=interval)
    if not ok:
        return jsonify({'error': msg}), 400
    sse_broadcast('sender_changed', {'running': True})
    return jsonify({'success': 'ls_thread is start'}), 200


@app.route('/stop_sender')
def stop_sender():
    sender_mgr.stop()
    sse_broadcast('sender_changed', {'running': False})
    return jsonify({'success': 'ls_thread is stop'}), 200


@app.route('/sender_status')
def sender_status():
    return jsonify({'running': sender_mgr.is_running()}), 200


# ---------------------------------------------------------------------------
# AI 弹幕聊天（function/ai_chat.py：配置 / 状态 / 测试 / 手动发送 / 日志）
# ---------------------------------------------------------------------------
@app.route('/ai/config', methods=['GET', 'POST'])
def ai_config():
    """读写 AI 配置（GET 返回脱敏 key；POST 合并写入语义，只覆盖提交键）"""
    if request.method == 'POST':
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict) or not body:
            return jsonify({'error': '请求体不能为空'}), 400
        try:
            ai_engine.save_config(body)
        except Exception as e:
            return jsonify({'error': f'保存配置失败: {e}'}), 500
        return jsonify({'ok': True, 'config': ai_engine.get_config()}), 200
    return jsonify(ai_engine.get_config()), 200


@app.route('/ai/status')
def ai_status():
    """AI 服务商状态（后端缓存 60s；配置变更后自动失效并即时探测）"""
    data = ai_engine.check_status()
    cfg = ai_engine.get_config()
    data['enabled'] = bool(cfg.get('enabled'))
    data['model'] = str(cfg.get('model') or '')
    data['provider'] = cfg.get('provider_name') or ''
    return jsonify(data), 200


@app.route('/ai/test', methods=['POST'])
def ai_test():
    """连通性测试 / 测试回复：body {mode: 'ping'|'chat', message?: str}"""
    body = request.get_json(silent=True) or {}
    mode = str(body.get('mode') or ('chat' if body.get('message') else 'ping'))
    message = str(body.get('message') or '').strip()
    return jsonify(ai_engine.test_connection(mode=mode, message=message)), 200


@app.route('/ai/send', methods=['POST'])
def ai_send():
    """手动触发一条 AI 回复（测试区）：走完整链路（AI调用→回复处理→发送→日志），
    跳过触发规则/概率/限流，保留触发黑名单过滤。"""
    body = request.get_json(silent=True) or {}
    message = str(body.get('message') or '').strip()
    username = str(body.get('username') or '测试用户')
    uid = str(body.get('uid') or '') or None
    if not message:
        return jsonify({'error': '消息不能为空'}), 400
    return jsonify(ai_engine.send_test(message, username=username, uid=uid)), 200


@app.route('/ai/stream', methods=['POST'])
def ai_stream():
    """流式测试回复（SSE）：测试区打字机效果。payload {message, username?, uid?}
    事件：delta（逐段文本）→ done（最终结果）/ error（失败）"""
    body = request.get_json(silent=True) or {}
    message = str(body.get('message') or '').strip()
    if not message:
        return jsonify({'error': '消息不能为空'}), 400
    username = str(body.get('username') or '测试用户')
    uid = str(body.get('uid') or '') or None

    def gen():
        for ev in ai_engine.stream_test(message, username=username, uid=uid):
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    return Response(gen(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no',
    })


@app.route('/ai/logs')
def ai_logs():
    """交互日志（内存最近 60 条；同时落盘 temp/ai_chat.log）"""
    return jsonify({'logs': ai_engine.get_logs()}), 200


@app.route('/ai/models')
def ai_models():
    """拉取当前运行时已安装/可用的模型列表：
    Ollama 走 /api/tags；llama.cpp / LM Studio / 云端走 OpenAI 兼容 /models"""
    return jsonify(ai_engine.list_models()), 200


# ---------------------------------------------------------------------------
# 前端静态资源托管（Vue 构建产物）
# ---------------------------------------------------------------------------
@app.route('/')
def index():
    if os.path.exists(os.path.join(FRONTEND_DIST, 'index.html')):
        return send_from_directory(FRONTEND_DIST, 'index.html')
    return jsonify({'name': 'shuitool3', 'message': '前端未构建，请运行 npm run build 或将 dist 输出到 backend/static'}), 200


@app.route('/assets/<path:path>')
def assets(path):
    return send_from_directory(os.path.join(FRONTEND_DIST, 'assets'), path)


@app.route('/<path:path>')
def spa_fallback(path):
    """SPA 路由回退：非 API 路径且存在前端构建产物时返回 index.html"""
    if path.startswith('api/'):
        return jsonify({'error': 'not found'}), 404
    if os.path.exists(os.path.join(FRONTEND_DIST, 'index.html')):
        return send_from_directory(FRONTEND_DIST, 'index.html')
    return jsonify({'error': 'not found'}), 404


def _start_tray():
    """系统托盘常驻（方案二）：pystray 创建托盘图标，右键菜单含「打开前端」「退出程序」。
    容错：托盘初始化失败（无桌面/无权限等）仅打印日志，继续以无托盘模式运行，不影响主服务。"""
    try:
        import pystray
        from PIL import Image as _PILImage
        _icon_file = None
        for _p in (
            os.path.join(BASE_DIR, 'tray_icon', 'tray_icon.png'),  # 打包：_internal/tray_icon/tray_icon.png（--add-data 目录源）
            os.path.join(BASE_DIR, 'tray_icon.png'),       # 打包兜底
            os.path.join(ROOT_DIR, 'backend', 'tray_icon.png'),  # 开发模式候选
            os.path.join(ROOT_DIR, 'frontend', 'public', 'icon.png'),  # 开发模式兜底
        ):
            if os.path.isfile(_p):
                _icon_file = _p
                break
        if _icon_file is None:
            print("[托盘] 未找到托盘图标文件，跳过托盘常驻，服务继续运行")
            return

        def _tray_open_frontend(_icon, _item):
            try:
                webbrowser.open('http://127.0.0.1:5000/')
                print("[托盘] 已调用打开前端")
            except Exception as e:
                print(f"[托盘] 打开前端失败: {e}")

        def _tray_quit(_icon, _item):
            # 先执行既有退出清理（atexit 回调不会被 os._exit 触发，需手动调用），再强制退出
            try:
                _on_exit_reset_connection()
            except Exception as e:
                print(f"[托盘] 退出清理失败: {e}")
            try:
                _icon.stop()
            except Exception:
                pass
            print("[托盘] 退出程序")
            os._exit(0)

        _img = _PILImage.open(_icon_file).convert('RGBA').resize((64, 64), _PILImage.LANCZOS)
        _menu = pystray.Menu(
            pystray.MenuItem('打开前端', _tray_open_frontend, default=True),
            pystray.MenuItem('退出程序', _tray_quit),
        )
        _icon = pystray.Icon('shuitool3_tray', _img, 'ShuisTool3 工具箱', _menu)
        threading.Thread(target=_icon.run, daemon=True, name='shuitool3-tray').start()
        print("[托盘] 系统托盘常驻已启动")
    except Exception as e:
        print(f"[托盘] 托盘初始化失败，以无托盘模式运行: {e}")


if __name__ == '__main__':
    print("shuitool3 Flask 后端启动中...")
    print(f"  设置文件: {SETTINGS_FILE}")
    print(f"  前端目录: {FRONTEND_DIST}")
    # 启动清理：删除 7 天前的实时弹幕记录文件
    danmu_cleanup_old()
    # 方案 B：启动洛雪播放状态监控线程（自动推进队列状态机，H3）
    threading.Thread(target=music_monitor_loop, daemon=True).start()
    # 可选自动打开浏览器（保留原功能，但避免在无 GUI 环境报错）
    try:
        webbrowser.open('http://127.0.0.1:5000/')
    except Exception:
        pass
    # 方案二：系统托盘常驻（容错：初始化失败不阻塞主服务）
    _start_tray()
    app.run(host='127.0.0.1', port=5000, debug=False, threaded=True)
