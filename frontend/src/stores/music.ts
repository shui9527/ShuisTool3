import { defineStore } from 'pinia'
import { ref, reactive } from 'vue'
import { http } from '@/api/http'
import { sseBridge } from '@/api/sse'
import type { SongEntry } from '@/types'

export interface PlayingSong {
  status: string
  name: string
  singer: string
  duration: number
  playTime: number
}

// 模块级：歌词 SSE 连接句柄，保证全应用仅建立一条连接（App.vue / LyricsView 均会触发）
let lyricsSse: EventSource | null = null
// 切歌竞态保护：只采用最后一次 /api/lx/lyric 的返回
let lyricReqSeq = 0
// progress 兜底补拉限频（毫秒）
let lastLyricFallbackFetch = 0
// 换歌后定时兜底轮询句柄：不依赖洛雪 progress/status 推送，确保 lyricLines 最终恢复多行
let lyricRetryTimer: ReturnType<typeof setInterval> | null = null

// ---------------------------------------------------------------------------
// 方案 B 兼容层：把后端返回的 music 列表（string[] 旧数据 或 SongEntry[]）
// 归一化为 SongEntry[]，展示层统一按 SongEntry 字段渲染。
// ---------------------------------------------------------------------------
function normalizeMusicList(raw: unknown): SongEntry[] {
  if (!Array.isArray(raw)) return []
  const out: SongEntry[] = []
  for (const item of raw) {
    if (item && typeof item === 'object') {
      const e = item as Partial<SongEntry>
      if (typeof e.name !== 'string') continue
      out.push({
        id: typeof e.id === 'string' ? e.id : '',
        name: e.name,
        singer: typeof e.singer === 'string' ? e.singer : '',
        username: typeof e.username === 'string' ? e.username : '',
        source: typeof e.source === 'string' ? e.source : '',
        songmid: typeof e.songmid === 'string' ? e.songmid : '',
        status: e.status === 'playing' || e.status === 'played' || e.status === 'pending' ? e.status : 'pending',
        playLater: Boolean(e.playLater),
        createdAt: typeof e.createdAt === 'number' ? e.createdAt : 0,
      })
    } else {
      // 旧 string 数据：转最小 SongEntry（保留歌名，其余字段缺省）
      const name = String(item ?? '').trim()
      if (!name) continue
      out.push({ id: '', name, singer: '', username: '', source: '', songmid: '', status: 'pending', playLater: false, createdAt: 0 })
    }
  }
  return out
}

// sync_playing 回传限频/去重：同一歌名+状态至少间隔 SYNC_MIN_INTERVAL 才发一次，
// 避免 progress 高频事件刷爆后端；仅歌名或状态变化时立即发送
let lastSyncKey = ''
let lastSyncAt = 0
const SYNC_MIN_INTERVAL = 1000

export const useMusicStore = defineStore('music', () => {
  const musicList = ref<SongEntry[]>([])
  const musicEnabled = ref(false)
  const lyricsConnected = ref(false)
  // 洛雪连通性（总览页状态芯片依据）：收到洛雪任意正常业务推送（命名事件/
  // 非错误消息）置 true；收到错误推送或通道断连置 false。与 lyricsConnected
  // 的区别：不因 SSE 通道 onopen 即置 true（通道建立 ≠ 洛雪可达），必须等到
  // 洛雪实际推送后才判定已连接，避免洛雪未启动时短暂误报"已连接"。
  const lxConnected = ref(false)
  const lyricsError = ref('')
  const playing = reactive<PlayingSong>({ status: '', name: '', singer: '', duration: 0, playTime: 0 })
  const lyricLine = ref('')
  const lyricArtist = ref('')
  const lyricLines = ref<{ time: number; text: string }[]>([])
  // 当前歌词行索引（-1 表示无匹配行），供渲染页取"下一行"时精确定位（避免 indexOf 撞重复文本）
  const lyricIndex = ref(-1)

  async function refreshList(): Promise<void> {
    try {
      const data = await http.get<any>('/get_queue')
      if (data && Array.isArray(data.music)) musicList.value = normalizeMusicList(data.music)
      // 打开页面/刷新时恢复持久化的开关真实状态（不依赖 SSE 广播）
      if (data && typeof data === 'object' && 'musicEnabled' in data) {
        musicEnabled.value = Boolean(data.musicEnabled)
      }
    } catch (e) {
      console.error('获取音乐列表失败', e)
    }
  }

  // 乐观更新：点击开关立即切换本地状态（不等网络往返），再异步同步后端；
  // 请求失败回滚到操作前状态。避免 SSE 广播回传前 UI 无响应（"点了没反应"）
  async function openMusic(): Promise<boolean> {
    const prev = musicEnabled.value
    musicEnabled.value = true
    try {
      await http.get<any>('/open-music')
      return true
    } catch {
      musicEnabled.value = prev
      return false
    }
  }

  async function closeMusic(): Promise<boolean> {
    const prev = musicEnabled.value
    musicEnabled.value = false
    try {
      await http.get<any>('/close-music')
      return true
    } catch {
      musicEnabled.value = prev
      return false
    }
  }

  async function next(): Promise<boolean> {
    try {
      await http.get<any>('/nextMusic')
      return true
    } catch {
      return false
    }
  }

  async function catchPlaying(): Promise<boolean> {
    try {
      await http.get<any>('/catch_playing')
      return true
    } catch (e) {
      console.error('同步当前播放失败', e)
      return false
    }
  }

  // 乐观更新：点击移除/添加/拖拽排序后本地立即生效（不等网络往返），
  // 再异步同步后端；请求失败回滚到操作前的列表，避免界面与后端不一致
  async function updateMusicList(list: SongEntry[]): Promise<boolean> {
    const prev = musicList.value
    musicList.value = list
    try {
      await http.post<any>('/update_music_list', { music_list: list })
      return true
    } catch {
      musicList.value = prev
      return false
    }
  }

  // 手动添加点歌：POST /add_music 复用后端 add_music_list 状态机
  // （自动去重/上限10/空队列立即播放/非空 playLater/清 removed_keys），
  // 成功后用返回的新队列整体更新本地；失败回滚到操作前列表
  async function addMusic(name: string, singer = ''): Promise<boolean> {
    const prev = musicList.value
    const optimistic: SongEntry = {
      id: '', name, singer, username: '', source: '', songmid: '',
      status: 'pending', playLater: false, createdAt: Date.now(),
    }
    musicList.value = [...musicList.value, optimistic]
    try {
      const data = await http.post<any>('/add_music', { name, singer })
      if (data && Array.isArray(data.music)) musicList.value = normalizeMusicList(data.music)
      return true
    } catch {
      musicList.value = prev
      return false
    }
  }

  // 洛雪播放状态回传（H3 通道一 / 方案 B）：前端从洛雪 SSE 感知到歌曲切换后，
  // 把当前曲目与状态 POST 给后端 /sync_playing，驱动队列状态机
  // （pending -> playing -> played）并回填 songmid/source，保证本地队列与
  // 洛雪实际播放强对齐。限频去重见模块级 lastSyncKey / lastSyncAt。
  async function syncPlaying(status: string, name: string, singer: string): Promise<void> {
    const key = `${name}::${status}`
    const now = Date.now()
    if (!name || (key === lastSyncKey && now - lastSyncAt < SYNC_MIN_INTERVAL)) return
    lastSyncKey = key
    lastSyncAt = now
    try {
      await http.post<any>('/sync_playing', { name, singer: singer || '', status })
    } catch (e) {
      console.error('回传播放状态失败', e)
    }
  }

  // 歌词 SSE：经 Flask 同源代理连接洛雪开放 API（默认 127.0.0.1:23330）。
  // LX 推送为【命名事件流】：每个字段独立一个事件（event: status/name/singer/
  // albumName/duration/progress/playbackRate/lyricLineText），data 是 JSON 值
  // （字符串带引号、数字为裸值）；progress 单位为秒，lyricLineText 是当前行文本
  // （未启用逐句歌词时为"歌手 - 歌名"），逐句歌词需另调 /api/lx/lyric 拉取 LRC。
  // 旧实现直连 127.0.0.1:23330（跨域不可靠）且只用 onmessage 收默认事件——洛雪
  // 从不推默认事件，导致字段全部丢失、歌词永远为空；同时误映射 playTime/source/
  // lines 等不存在字段。现改为逐个监听命名事件。
  function connectLyricsSse(): void {
    if (lyricsSse) return
    const es = new EventSource('/api/lx/subscribe')
    lyricsSse = es
    es.onopen = () => {
      lyricsConnected.value = true
      lyricsError.value = ''
    }
    // 洛雪命名事件统一入口：data 均为 JSON 值，解析失败静默忽略
    const onField = (field: string, handler: (v: any) => void) => {
      es.addEventListener(field, (ev: MessageEvent) => {
        try {
          // 收到洛雪任意命名事件推送 = 洛雪开放 API 连通（仅正常推送进入此分支）
          lxConnected.value = true
          handler(JSON.parse(ev.data))
        } catch {
          // 忽略非 JSON 数据
        }
      })
    }
    onField('status', (v) => {
      if (typeof v === 'string') playing.status = v
      // 方案 B：播放状态变化（playing/paused/stop）回传后端对齐队列；
      // 限频去重由 syncPlaying 内部处理，stop 类由后端 pop 已播完的头部
      void syncPlaying(String(v), playing.name, playing.singer)
    })
    onField('name', (v) => {
      if (typeof v !== 'string') return
      const isNewSong = v !== playing.name
      playing.name = v
      if (isNewSong) {
        lyricLines.value = []
        lyricLine.value = ''
        lyricIndex.value = -1
        void fetchLyrics()
        startLyricRetryWatch()
        // 方案 B：歌曲切换 → 回传 sync_playing 驱动后端队列状态机；
        // 延迟 300ms 等 singer 事件先到达，确保回传歌手字段完整
        setTimeout(() => {
          if (playing.name === v) void syncPlaying('playing', v, playing.singer)
        }, 300)
      }
    })
    onField('singer', (v) => {
      playing.singer = typeof v === 'string' ? v : ''
      lyricArtist.value = playing.singer
    })
    onField('duration', (v) => {
      playing.duration = Number(v) || 0
    })
    // lyric 事件：洛雪推送当前歌曲完整 LRC（data 为带引号 JSON 字符串，
    // JSON.parse 后是 LRC 文本）。这是多行歌词的【主数据源】，优先级高于
    // HTTP 轮询兜底：
    // - JSON.parse 失败时 fallback 到原始文本（兼容洛雪直接推裸 LRC 的情况）
    // - 解析成功后使进行中的 fetchLyrics 轮询链失效（lyricReqSeq++），
    //   避免较慢的 HTTP 轮询结果覆盖 SSE 推送的最新歌词
    // - 清除 lyricLine 兜底文本，避免单行/多行渲染状态不一致
    es.addEventListener('lyric', (ev: MessageEvent) => {
      // 洛雪正常推送歌词事件同样证明开放 API 连通
      lxConnected.value = true
      let raw = ev.data
      try {
        const parsed = JSON.parse(ev.data)
        if (typeof parsed === 'string' && parsed.trim()) raw = parsed
        else raw = ev.data
      } catch {
        // parse 失败：洛雪直接推裸 LRC 文本，保留原始 data
      }
      const lines = parseLrc(normalizeLrcText(raw))
      if (lines.length) {
        lyricReqSeq++
        lyricLines.value = lines
        updateCurrentLyric(playing.playTime)
        lyricLine.value = ''
      }
    })
    onField('progress', (v) => {
      playing.playTime = Number(v) || 0
      updateCurrentLyric(playing.playTime)
      // 兜底补拉：换歌瞬间 LRC 可能未就绪导致首次拉取为空/失败。
      // 条件不依赖 status==='playing'（换歌瞬间洛雪可能不推 status 或推送非
      // playing 的缓冲态），只要歌名已更新且 lyricLines 仍为空就限频重拉，
      // 防止歌词页长期回退单行
      const now = Date.now()
      if (playing.name && !lyricLines.value.length && now - lastLyricFallbackFetch > 2000) {
        lastLyricFallbackFetch = now
        void fetchLyrics()
      }
    })
    // lyricLineText 兜底：逐句歌词未就绪时先展示当前行（注意可能是"歌手 - 歌名"）
    onField('lyricLineText', (v) => {
      if (typeof v === 'string' && v && !lyricLines.value.length) {
        lyricLine.value = v
      }
    })
    // 默认 message 事件：兼容后端断连提示（data: {"error":"lx_disconnected"}）与
    // 老式整包 JSON 推送（洛雪当前版本不推，保留兼容以适配后端聚合格式）
    es.onmessage = (event) => {
      let data: any = null
      try {
        data = JSON.parse(event.data)
      } catch {
        // 非 JSON：识别裸错误文本（disconnected/error 提示），其余静默忽略
        if (/error|disconnect/i.test(String(event.data || ''))) {
          lyricsConnected.value = false
          lxConnected.value = false
          lyricsError.value = 'LX Music 开放 API 未连接'
        }
        return
      }
      // 错误事件统一过滤：data.error 存在（lx_disconnected 或其它错误码）、
      // event 字段为 error 均视为错误推送——不置已连接、不进入业务分支，
      // 避免持续收到的错误推送被误当有效推送
      const isError = data && typeof data === 'object' && (
        data.error !== undefined ||
        data.event === 'error' ||
        /disconnect/i.test(String(data.error || ''))
      )
      if (isError) {
        lyricsConnected.value = false
        lxConnected.value = false
        lyricsError.value = data.message || 'LX Music 开放 API 未连接'
        return
      }
      // 正常业务推送（非错误 JSON）即证明洛雪开放 API 连通
      lxConnected.value = true
      if (data && typeof data === 'object' && data.name) {
          const isNewSong = data.name !== playing.name
          playing.status = data.status || 'playing'
          playing.name = data.name
          playing.singer = data.singer || ''
          playing.duration = Number(data.duration) || 0
          playing.playTime = Number(data.progress) || 0
          lyricArtist.value = data.singer || ''
          if (isNewSong) {
            lyricLines.value = []
            lyricLine.value = ''
            lyricIndex.value = -1
            void fetchLyrics()
            startLyricRetryWatch()
            // 方案 B：同命名事件分支，歌曲切换回传后端对齐队列
            setTimeout(() => {
              if (playing.name === data.name) void syncPlaying('playing', data.name, playing.singer)
            }, 300)
          }
          if (typeof data.progress !== 'undefined') updateCurrentLyric(playing.playTime)
        }
    }
    es.onerror = () => {
      lyricsConnected.value = false
      lxConnected.value = false
      lyricsError.value = 'LX Music 开放 API 未连接（请确认已开启开放 API 并正在播放）'
    }
  }

  // 还原 LRC 文本中的字面转义序列：洛雪部分版本/事件推送对 LRC 做了双重 JSON
  // 编码（如 lyric 事件 data 为带引号 JSON 字符串，内部换行是字面 \n 双字符），
  // 若不做还原，parseLrc 按真实换行 split 会失败，导致整段歌词挤成一行、
  // 时间标签残留在行文本中（表现为"歌词乱码"）。歌词正文不会包含字面 \n，
  // 全局替换安全。
  function unescapeLrc(text: string): string {
    return text
      .replace(/\\r\\n/g, '\n')
      .replace(/\\n/g, '\n')
      .replace(/\\r/g, '\r')
  }

  // 将洛雪 /lyric 或 lyric 事件返回归一化为纯 LRC 文本：
  // - 纯 LRC（以 [ 开头的时间标签行）原样返回（还原字面转义换行）
  // - JSON 包装（洛雪 /lyric 无 filter 时默认返回
  //   {"lrc":{"lyric":...},"translatedLrc":{...},"rawLrc":"..."}，
  //   部分版本为 {"lyric":"..."} 或 {"data":{"lyric":"..."}}）
  //   提取其中 lyric/rawLrc/lrc/lrc.lyric/lyricText 等字段，
  //   避免 parseLrc 解析 JSON 文本得到空结果或把 JSON 结构混入歌词
  function normalizeLrcText(raw: unknown): string {
    let text = ''
    if (typeof raw === 'string') {
      text = raw
    } else {
      // HTTP 路径 http.get 已自动 JSON.parse，text 可能是对象而非字符串，
      // 先序列化再走统一提取逻辑，避免 raw.trim() 抛 TypeError
      try {
        text = JSON.stringify(raw)
      } catch {
        return ''
      }
    }
    text = text.trim()
    if (!text) return ''
    if (text.startsWith('[')) return unescapeLrc(text)
    try {
      const j = JSON.parse(text)
      if (typeof j === 'string' && j.trim()) return unescapeLrc(j.trim())
      if (!j || typeof j !== 'object') return ''
      const data = j.data && typeof j.data === 'object' ? j.data : j
      const lrc = data.lrc
      const translated = data.translatedLrc
      const candidates = [
        data.lyric,
        data.rawLrc,
        lrc && typeof lrc === 'object' ? lrc.lyric : lrc,
        data.lyricText,
        translated && typeof translated === 'object' ? translated.lyric : translated,
        data.lrcInfo && data.lrcInfo.lrc,
      ]
      for (const c of candidates) {
        if (typeof c === 'string' && c.trim()) return unescapeLrc(c.trim())
      }
    } catch {
      // 非 JSON 且非 LRC 开头：原样返回，parseLrc 自行过滤无效行
    }
    return unescapeLrc(text)
  }

  // 拉取逐句歌词（LRC 文本）并解析为时间轴。
  // 换歌瞬间洛雪歌词数据可能尚未就绪/返回旧歌或 JSON 包装，故采取
  // "延迟首拉 + 空结果重试 + 竞态保护"策略：
  // - 先等 400ms 让洛雪完成切歌歌词装载，再发起首拉
  // - 归一化后无有效时间标签（空 LRC / JSON 包装 / 旧歌空档）且歌曲未再
  //   切换时，最多重试 5 次（间隔 800ms）；仍空则交给 progress / 定时兜底
  async function fetchLyrics(): Promise<void> {
    const seq = ++lyricReqSeq
    await new Promise((r) => setTimeout(r, 400))
    if (seq !== lyricReqSeq) return
    const tryFetch = async (attempt: number): Promise<void> => {
      if (seq !== lyricReqSeq) return
      try {
        const text = await http.get<string>('/api/lx/lyric')
        if (seq !== lyricReqSeq) return
        const lines = parseLrc(normalizeLrcText(text || ''))
        if (lines.length) {
          lyricLines.value = lines
          updateCurrentLyric(playing.playTime)
          return
        }
        // 空结果：换歌瞬间歌词未就绪是常态，继续重试拿真实歌词
        if (attempt < 5) {
          setTimeout(() => void tryFetch(attempt + 1), 800)
          return
        }
        // 重试耗尽：保持空，等待 progress/定时兜底再补拉
      } catch (e) {
        console.error('获取逐句歌词失败', e)
        if (attempt < 5) {
          setTimeout(() => void tryFetch(attempt + 1), 800)
          return
        }
      }
    }
    void tryFetch(0)
  }

  // 换歌后定时兜底：不依赖洛雪 progress/status 推送（换歌瞬间可能都不推），
  // 每 2s 检查一次，lyricLines 仍为空则补拉，最多 12 次（约 24s）或拿到歌词为止
  function startLyricRetryWatch(): void {
    if (lyricRetryTimer) {
      clearInterval(lyricRetryTimer)
      lyricRetryTimer = null
    }
    let tries = 0
    lyricRetryTimer = setInterval(() => {
      tries += 1
      if (lyricLines.value.length || tries >= 12) {
        if (lyricRetryTimer) {
          clearInterval(lyricRetryTimer)
          lyricRetryTimer = null
        }
        return
      }
      const now = Date.now()
      if (now - lastLyricFallbackFetch > 2000) {
        lastLyricFallbackFetch = now
        void fetchLyrics()
      }
    }, 2000)
  }

  // 解析 LRC：支持一行多个时间标签（[00:10.00][00:20.00]歌词）与分钟/秒/毫秒
  function parseLrc(lrcText: string): { time: number; text: string }[] {
    const lines: { time: number; text: string }[] = []
    const meta = /^\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]/
    for (const raw of lrcText.split(/\r?\n/)) {
      const text = raw.trim()
      if (!text) continue
      const tags: number[] = []
      let rest = text
      let match = rest.match(meta)
      while (match) {
        const min = parseInt(match[1], 10)
        const sec = parseInt(match[2], 10)
        const frac = match[3] ? parseInt(match[3].padEnd(3, '0').slice(0, 3), 10) / 1000 : 0
        tags.push(min * 60 + sec + frac)
        rest = rest.slice(match[0].length)
        match = rest.match(meta)
      }
      if (tags.length && rest) {
        for (const t of tags) lines.push({ time: t, text: rest.trim() })
      }
    }
    return lines.sort((a, b) => a.time - b.time)
  }

  // 按播放进度（秒）定位当前歌词行
  function updateCurrentLyric(playTimeSec: number): void {
    let current = ''
    let idx = -1
    for (let i = 0; i < lyricLines.value.length; i++) {
      if (lyricLines.value[i].time <= playTimeSec) {
        current = lyricLines.value[i].text
        idx = i
      } else {
        break
      }
    }
    lyricIndex.value = idx
    if (current) lyricLine.value = current
  }

  function listenSse(): void {
    sseBridge.on('music_changed', (payload: any) => {
      if (payload && Array.isArray(payload.music)) {
        musicList.value = normalizeMusicList(payload.music)
      } else if (payload && typeof payload === 'object' && 'musicEnabled' in payload) {
        musicEnabled.value = Boolean(payload.musicEnabled)
      }
    })
    sseBridge.on('music_status_changed', (payload: any) => {
      if (payload && 'musicEnabled' in payload) musicEnabled.value = Boolean(payload.musicEnabled)
    })
    sseBridge.on('connection_changed', (payload: any) => {
      if (payload && 'musicEnabled' in payload) musicEnabled.value = Boolean(payload.musicEnabled)
    })
  }

  return {
    musicList, musicEnabled, lyricsConnected, lxConnected, lyricsError, playing, lyricLine, lyricArtist, lyricLines, lyricIndex,
    refreshList, openMusic, closeMusic, next, catchPlaying, updateMusicList, addMusic, syncPlaying,
    connectLyricsSse, fetchLyrics, listenSse,
  }
})
