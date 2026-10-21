// SSE 统一事件桥：Flask 推送替代轮询（前端缺陷 F6/F7 修复）
// 事件通道与后端 /events 对齐

type SseHandler = (payload: any) => void

// 状态判定改为"最近收到推送"（替代原 onopen 即已连接）：
// 收到任意 SSE 数据（心跳/事件均算）即视为活跃并置已连接；
// 持续 SSE_ACTIVITY_TIMEOUT 毫秒未收到任何推送则置未连接。
// 后端 /events 心跳已改为 data 型（间隔 1.5s），保证 2s 窗口内必有推送，
// 避免临界抖动。
const SSE_ACTIVITY_TIMEOUT = 2000
// 活动检查间隔（ms）：每 500ms 检查一次最后推送时间
const SSE_ACTIVITY_CHECK_MS = 500
// 固定短重连间隔（ms）：后端已改为按活跃时间淘汰死连接、不误踢活跃订阅者，
// 不再需要指数退避放大中断时间；固定 1.5s 快速恢复
const SSE_RECONNECT_DELAY = 1500

class SseBridge {
  private es: EventSource | null = null
  private handlers = new Map<string, Set<SseHandler>>()
  private retryTimer: number | null = null
  private manualClosed = false
  private reconnectAttempts = 0
  // 最近一次收到推送的时间戳（0 = 尚未收到任何推送）
  private lastActivity = 0
  // 活动检查定时器：每 SSE_ACTIVITY_CHECK_MS 检查一次最后推送时间
  private activityTimer: number | null = null
  // 内部已知状态：仅状态变化时 dispatch sse-status，避免定时器高频广播
  private statusConnected = false

  connect(): void {
    if (this.es) return
    this.manualClosed = false
    this.es = new EventSource('/events')
    // 活动检查定时器：全局仅一个，随首次 connect 启动，close 时清理
    if (this.activityTimer === null) {
      this.activityTimer = window.setInterval(() => this.activityTick(), SSE_ACTIVITY_CHECK_MS)
    }
    this.es.onopen = () => {
      this.reconnectAttempts = 0
      // 不再"onopen 即已连接"：连接建立不等同于收到推送，
      // 已连接状态由首个推送（markActivity）驱动，后续由 activityTick 维持/回收
    }
    this.es.onmessage = (event) => {
      // 先判定是否错误事件：event 为 error、或 data 含 error / lx_disconnected /
      // disconnected 等错误标记。错误推送不代表业务活跃——不刷新 lastActivity、
      // 不置已连接（防止洛雪未启动时 /api/lx/subscribe 或主通道错误推送
      // 被误当有效推送刷新活跃时间戳）。
      let parsed: any = null
      try {
        parsed = JSON.parse(event.data)
      } catch {
        // 非 JSON：按普通推送处理（兜底其它格式）
      }
      const evt = (parsed && parsed.event) || 'message'
      const payload = parsed ? parsed.data : event.data
      const rawText = typeof event.data === 'string' ? event.data : ''
      const isErrorEvent =
        evt === 'error' ||
        (parsed && typeof parsed === 'object' && (
          parsed.error !== undefined ||
          (parsed.data && typeof parsed.data === 'object' && parsed.data.error !== undefined)
        )) ||
        // 字符串兜底：仅匹配"明确错误标记"——JSON 键形态的 "error":
        // （后随冒号）与断连关键词。不使用裸 "error" 子串匹配，
        // 避免正常业务数据（如弹幕文本/歌曲名含 "error" 字样）被误判
        // 为错误推送而丢弃、且不刷新活跃时间导致主芯片误判
        /(lx_disconnected|"disconnected"|"error"\s*:)/i.test(rawText)
      if (isErrorEvent) {
        // 错误事件：不刷新活跃时间、不置已连接，静默忽略
        return
      }
      // 正常数据（心跳 heartbeat 及业务事件）才刷新活跃时间并立即置已连接
      this.markActivity()
      this.dispatch(evt, payload)
      this.dispatch('*', payload)
    }
    this.es.onerror = () => {
      this.es?.close()
      this.es = null
      if (this.manualClosed) return
      // 不在此处立即广播"未连接"：es 已关闭且不再有推送，
      // activityTick 会在 SSE_ACTIVITY_TIMEOUT 内统一置未连接，
      // 与"收到推送才已连接"的新语义保持一致
      // 固定短重连（多次 onerror 只保留最后一个定时器，防重复排队）
      if (this.retryTimer) window.clearTimeout(this.retryTimer)
      this.reconnectAttempts += 1
      this.retryTimer = window.setTimeout(() => this.connect(), SSE_RECONNECT_DELAY)
    }
  }

  close(): void {
    this.manualClosed = true
    if (this.retryTimer) window.clearTimeout(this.retryTimer)
    if (this.activityTimer) window.clearInterval(this.activityTimer)
    this.retryTimer = null
    this.activityTimer = null
    this.lastActivity = 0
    this.statusConnected = false
    this.es?.close()
    this.es = null
  }

  // ------------------------------------------------------------------
  // 活动状态判定（"最近收到推送"语义）
  // ------------------------------------------------------------------
  private markActivity(): void {
    this.lastActivity = Date.now()
    // 收到推送即活跃：若当前通道 OPEN 则立即置已连接（不等 500ms 检查周期）
    if (this.es && this.es.readyState === EventSource.OPEN) {
      this.setConnected(true)
    }
  }

  private activityTick(): void {
    // 目标状态 = 通道 OPEN 且距最后推送未超时；尚未收到过推送（lastActivity==0）视为未连接
    let target = false
    if (this.es && this.es.readyState === EventSource.OPEN && this.lastActivity > 0) {
      target = Date.now() - this.lastActivity <= SSE_ACTIVITY_TIMEOUT
    }
    this.setConnected(target)
  }

  private setConnected(connected: boolean): void {
    if (this.statusConnected === connected) return
    this.statusConnected = connected
    if (connected) {
      this.dispatch('sse-status', { connected: true })
    } else {
      this.dispatch('sse-status', { connected: false, retryIn: SSE_RECONNECT_DELAY })
    }
  }

  // 当前 SSE 是否处于 OPEN：CONNECTING / CLOSED 均视为未连接，
  // 供断开期间弹幕轮询兜底做初始判定
  get connected(): boolean {
    return this.es !== null && this.es.readyState === EventSource.OPEN
  }

  on(event: string, handler: SseHandler): () => void {
    if (!this.handlers.has(event)) this.handlers.set(event, new Set())
    this.handlers.get(event)!.add(handler)
    return () => this.off(event, handler)
  }

  off(event: string, handler: SseHandler): void {
    this.handlers.get(event)?.delete(handler)
  }

  private dispatch(event: string, payload: any): void {
    this.handlers.get(event)?.forEach((fn) => {
      try {
        fn(payload)
      } catch (e) {
        console.error(`SSE handler [${event}] error`, e)
      }
    })
  }
}

export const sseBridge = new SseBridge()
