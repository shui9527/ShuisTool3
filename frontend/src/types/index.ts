// 全局类型定义（对齐原 settings.json / queue.json 结构）

export interface OpenLiveConfig {
  accessKeyId: string
  accessKeySecret: string
  appId: string
}

export interface GallerySettings {
  biliclientRunning: boolean
  livecode: string
  openlive: OpenLiveConfig
}

export interface QueueSettings {
  queueType: string
  queueLimit: string
  queueCommands: string
  cancelCommands: string
  titleText: string
  titleCheckbox: boolean
  titleFontFamily: string
  titleFontSize: string
  titleFontColor: string
  titleUnderlineCheckbox: boolean
  titleUnderlineText: string
  titleUnderlineColor: string
  queueFontFamily: string
  queueFontSize: string
  queueFontColor: string
  BackgroundColor: string
  BackgroundOpacity: string
  QueueBackgroundColorCheckbox: boolean
  QueueBackgroundColor: string
  QueueOpacity: string
  giftPriority: string
  queueEnabled: boolean
}

export interface MusicSettings {
  musicCommands: string
  skipCommands: string
  musicTitleText: string
  musicTitleCheckbox: boolean
  musicTitleFontFamily: string
  musicTitleFontSize: string
  musicTitleFontColor: string
  musicTitleUnderlineCheckbox: boolean
  musicTitleUnderlineText: string
  musicTitleUnderlineColor: string
  musicFontFamily: string
  musicFontSize: string
  musicFontColor: string
  musicBackgroundColor: string
  musicBackgroundOpacity: string
  musicBackgroundColorCheckbox: boolean
  musicQueueBackgroundColor: string
  musicQueueOpacity: string
  musicEnabled: boolean
}

export interface LyricsSettings {
  lyricstitleEnabled: boolean
  lyricstitleFontFamily: string
  lyricstitleFontColor: string
  lyricstitleOpacity: string
  lyricstitleFontSize: string
  lyricsartistEnabled: boolean
  lyricsartistFontFamily: string
  lyricsartistFontColor: string
  lyricsartistOpacity: string
  lyricsartistFontSize: string
  lyricsFontFamily: string
  lyricsFontColor: string
  lyricsOpacity: string
  lyricsFontSize: string
  layoutposition: string
  lyricstitleAlign: string
  lyricsartistAlign: string
  lyricsAlign: string
  lyricstitleWidth: string
  lyricsbackgroundColor: string
  lyricsbackgroundOpacity: string
  lyricsFadein: boolean
  // v6 布局设置
  lineSpacing: string
  layoutMargin: string
  layoutGap: string
  layoutWidth: string
  // v7 歌名框体宽度（仅左右布局生效，最大 = layoutWidth - 200）
  titleBoxWidth: string
}

export interface Settings {
  gallery: GallerySettings
  queue: QueueSettings
  music: MusicSettings
  lyrics: LyricsSettings
}

export interface QueueData {
  queue: string[]
  music: Array<string | SongEntry>
  // B15：礼物累计记录 username -> {coin, expire}，礼物排队模式下展示送礼价值
  gift_records?: GiftRecords
  // 后端 /get_queue 附带返回的开关真实状态（来自 settings 持久化）
  queueEnabled?: boolean
  musicEnabled?: boolean
}

// 礼物累计记录：coin 为金瓜子数，expire 为过期时间戳（最近送礼 + 45 分钟滚动窗口）
export interface GiftRecord {
  coin: number
  expire: number
}

export type GiftRecords = Record<string, GiftRecord>

// ---------------------------------------------------------------------------
// 方案 B：点歌队列结构化条目（SongEntry）
// 后端 queue.json 已迁移为 SongEntry[]；旧 string 数据经兼容工具安全兜底，
// 展示层一律通过 entryName() / entrySinger() 取字段，不直接假设条目类型。
// ---------------------------------------------------------------------------
export type SongStatus = 'pending' | 'playing' | 'played'

export interface SongEntry {
  id: string
  name: string
  singer: string
  username: string
  source: string
  songmid: string
  status: SongStatus
  playLater: boolean
  createdAt: number
}

export function isSongEntry(v: unknown): v is SongEntry {
  return typeof v === 'object' && v !== null && typeof (v as { name?: unknown }).name === 'string'
}

// 从 string（旧数据）或 SongEntry（新数据）中取展示歌名
export function entryName(v: string | SongEntry | null | undefined): string {
  if (!v) return ''
  return typeof v === 'string' ? v : (v.name || '')
}

// 从 string 或 SongEntry 中取歌手（旧 string 无歌手）
export function entrySinger(v: string | SongEntry | null | undefined): string {
  if (!v || typeof v === 'string') return ''
  return v.singer || ''
}

export function entryStatus(v: string | SongEntry | null | undefined): SongStatus {
  if (!v || typeof v === 'string') return 'pending'
  return v.status || 'pending'
}

export interface CookiesData {
  room_id: string
  SESSDATA: string
  bili_jct: string
  buvid3: string
  dedeuserid: string
}

// 自动回复规则（多关键词一规则 + 回复随机池）
export interface ReplyRule {
  keywords: string[]
  replies: string[]
}

// 自动刷屏功能配置（存储于 settings/sender_config.json，与账号凭据分离）
export interface SenderConfig {
  screeningMode: string
  interval: string
  text_list: string[]
  thanksEnabled: boolean
  thanksMode: string
  thanks_list: string[]
  replyEnabled: boolean
  replyMatchMode: 'contains' | 'exact' | 'regex'
  replyCooldown: number
  replyIgnoreSelf: boolean
  reply_list: ReplyRule[]
}

export interface FontItem {
  name: string
  value: string
}

// ---------------------------------------------------------------------------
// AI 弹幕聊天（存储于 settings/ai_config.json）
// ---------------------------------------------------------------------------
export interface AiTriggerConfig {
  prefixEnabled: boolean
  prefixes: string[]
  keywordEnabled: boolean
  keywords: string[]
  allEnabled: boolean
  probability: number
  userBlacklist: string[]  // 用户黑名单（昵称或 UID），命中不触发 AI
}

export interface AiThrottleConfig {
  globalCool: number
  userCool: number
  perMinute: number
}

export interface AiContextConfig {
  mode: 'A' | 'B' | 'C'
  window: number
  globalWindow: number
  expireMinutes: number
  withUsername: boolean
  // 上下文总字符预算（含用户名；超出从最早的条目丢弃，最后一条保留）
  budget: number
  // 单条弹幕/消息输入截断上限（防御超长输入）
  maxInputLen: number
  // 同用户相同弹幕去重窗口 s（0 = 关闭；命中不触发不计数）
  dedupeSeconds: number
}

// 调用与并发（本地小模型适配）
export interface AiCallConfig {
  concurrency: number  // 同时推理请求数（本地 CPU 模型建议 1）
  timeout: number      // 请求读超时 s
  retry: number        // 失败重试次数（仅网络错误/429/5xx）
  keep_alive: number   // Ollama keep_alive 分钟（0 = 不发送该字段，仅 ollama 生效）
  thinking: 'auto' | 'on' | 'off'  // 思考模式：auto=本地自动关闭/云端跟随默认；on=开启；off=关闭
}

export interface AiFilterConfig {
  triggerBlacklist: string[]
  replyBlacklist: string[]
}

export interface AiConfig {
  enabled: boolean
  provider: string
  base_url: string
  api_key: string
  model: string
  temperature: number
  max_tokens: number
  persona: string
  system_prompt: string
  target_words: number
  max_reply_len: number
  forbidden_check: boolean
  trigger: AiTriggerConfig
  throttle: AiThrottleConfig
  context: AiContextConfig
  call: AiCallConfig
  filter: AiFilterConfig
  persona_prompts?: Record<string, string>  // 按人设保存的自定义 System Prompt（persona key -> 文本）
  provider_name?: string
}

export interface AiStatus {
  ok: boolean
  checked: boolean
  last_check: number
  latency_ms: number
  enabled: boolean
}

export interface AiLogEntry {
  time: string
  username: string
  danmu: string
  reply: string
  trigger: string
  filtered: boolean
  sent_ok?: boolean
  note?: string
  usage?: string
}

export interface AiTestResult {
  ok: boolean
  provider?: string
  reply?: string
  latency_ms?: number
  error?: string
  filtered?: boolean
  note?: string
  sent_ok?: boolean
  usage?: string
}

export interface DanmuLog {
  // 弹幕产生时间（HH:MM:SS，本地）；持久化恢复与实时推送均有
  time?: string
  type: string
  username: string
  danmu: string
}
