import { defineStore } from 'pinia'
import { ref } from 'vue'
import { http } from '@/api/http'
import { sseBridge } from '@/api/sse'
import type { DanmuLog } from '@/types'

// 断开期间弹幕补齐轮询间隔（ms）：SSE 未连接时每 3s 拉取一次当日弹幕日志
// （后端实时落盘 + loadLogs 幂等重载）；SSE 恢复后立即停止，重连成功补拉作为双保险
const POLL_INTERVAL_MS = 3000

export const useConsoleStore = defineStore('console', () => {
  const logs = ref<DanmuLog[]>([])
  const maxLogs = 300
  const sseConnected = ref(false)
  // 轮询定时器句柄：同一时刻只允许存在一个，防止重复定时器/重复请求
  let pollTimer: number | null = null

  function push(log: DanmuLog): void {
    logs.value.push(log)
    if (logs.value.length > maxLogs) {
      logs.value.splice(0, logs.value.length - maxLogs)
    }
  }

  function clear(): void {
    logs.value = []
  }

  // 实时弹幕持久化：页面加载时从后端恢复当日记录（日志按写入顺序即时间正序）
  async function loadLogs(): Promise<void> {
    try {
      const data = await http.get<any>('/api/danmu_logs')
      if (data && Array.isArray(data.logs)) {
        logs.value = data.logs.slice(-maxLogs)
      }
    } catch (e) {
      console.error('加载弹幕记录失败', e)
    }
  }

  // 清空当日弹幕记录：后端删除当日日志文件 + 前端清空列表（一键同步）
  async function clearLogs(): Promise<boolean> {
    try {
      await http.post<any>('/api/danmu_logs/clear')
      logs.value = []
      return true
    } catch {
      return false
    }
  }

  // 断开期轮询兜底：SSE 未连接时每 POLL_INTERVAL_MS 拉取一次当日弹幕日志，
  // loadLogs 幂等（整体重载当日数据），不会重复追加
  function startPolling(): void {
    if (pollTimer !== null) return
    pollTimer = window.setInterval(() => {
      void loadLogs()
    }, POLL_INTERVAL_MS)
  }

  function stopPolling(): void {
    if (pollTimer !== null) {
      window.clearInterval(pollTimer)
      pollTimer = null
    }
  }

  function listenSse(): void {
    sseBridge.on('danmu', (payload: any) => {
      if (!payload || typeof payload !== 'object') return
      push({
        time: new Date().toTimeString().slice(0, 8),
        type: payload.type || 'chat',
        username: payload.username || '',
        danmu: payload.danmu || '',
      })
    })
    sseBridge.on('sse-status', (payload: any) => {
      const connected = Boolean(payload && payload.connected !== false)
      sseConnected.value = connected
      if (connected) {
        // 连接恢复：立即停止轮询（避免与重连补拉叠加成重复定时器/重复请求），
        // 再重拉当日日志补齐断开期间落盘但未推送的弹幕（双保险，重载幂等）
        stopPolling()
        void loadLogs()
      } else {
        // 断开期间：启动定时轮询兜底（内部保证只存在一个定时器）
        startPolling()
      }
    })
    // 初始兜底：页面加载时 SSE 可能处于 CONNECTING / 等待重连（尚未 OPEN），
    // 此时立即启动轮询，避免断开初期无任何补齐手段
    if (!sseBridge.connected) {
      startPolling()
    }
  }

  return { logs, sseConnected, push, clear, loadLogs, clearLogs, listenSse, startPolling, stopPolling }
})
