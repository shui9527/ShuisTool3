import { defineStore } from 'pinia'
import { ref } from 'vue'
import { http } from '@/api/http'
import { sseBridge } from '@/api/sse'

export interface ConnectionStatus {
  connected: boolean
  roomId: string | null
  queueEnabled: boolean
  musicEnabled: boolean
  error: string | null
}

export const useConnectionStore = defineStore('connection', () => {
  const status = ref<ConnectionStatus>({
    connected: false,
    roomId: null,
    queueEnabled: false,
    musicEnabled: false,
    error: null,
  })
  const connecting = ref(false)
  // 断开冷却倒计时：断开成功后启动，期间连接按钮禁用并显示"等待收尾 Ns"
  const cooldown = ref(0)
  let cooldownTimer: number | null = null

  function clearCooldown(): void {
    if (cooldownTimer !== null) {
      clearInterval(cooldownTimer)
      cooldownTimer = null
    }
    cooldown.value = 0
  }

  function startCooldown(seconds: number): void {
    clearCooldown()
    cooldown.value = seconds
    cooldownTimer = window.setInterval(() => {
      cooldown.value -= 1
      if (cooldown.value <= 0) clearCooldown()
    }, 1000)
  }

  async function fetchStatus(): Promise<void> {
    try {
      const data = await http.get<any>('/connection_status')
      if (data) {
        status.value = {
          connected: Boolean(data.connected ?? data.biliclientRunning),
          roomId: data.roomId ?? data.livecode ?? null,
          queueEnabled: Boolean(data.queueEnabled),
          musicEnabled: Boolean(data.musicEnabled),
          error: data.error ?? null,
        }
      }
    } catch (e) {
      console.error('获取连接状态失败', e)
    }
  }

  async function connect(livecode?: string): Promise<{ ok: boolean; message: string }> {
    connecting.value = true
    try {
      const params: Record<string, string> = {}
      if (livecode) params.livecode = livecode
      const res = await http.postForm<any>('/connect_services', params)
      if (res) {
        status.value.connected = true
        clearCooldown() // 连接成功后清除冷却状态
        return { ok: true, message: res.message || '已连接' }
      }
      return { ok: false, message: '连接失败' }
    } catch (e: any) {
      return { ok: false, message: e?.message || '连接请求异常' }
    } finally {
      connecting.value = false
    }
  }

  async function disconnect(): Promise<{ ok: boolean; message: string }> {
    try {
      const res = await http.get<any>('/disconnect_services')
      if (res) {
        status.value.connected = false
        return { ok: true, message: res.message || '已断开' }
      }
      return { ok: false, message: '断开失败' }
    } catch (e: any) {
      return { ok: false, message: e?.message || '断开请求异常' }
    }
  }

  function listenSse(): void {
    sseBridge.on('connection_changed', (payload: any) => {
      if (!payload) return
      status.value.connected = Boolean(payload.connected ?? payload.biliclientRunning ?? status.value.connected)
      if ('roomId' in payload) status.value.roomId = payload.roomId
      if ('queueEnabled' in payload) status.value.queueEnabled = Boolean(payload.queueEnabled)
      if ('musicEnabled' in payload) status.value.musicEnabled = Boolean(payload.musicEnabled)
      if ('error' in payload) status.value.error = payload.error
    })
  }

  return { status, connecting, cooldown, fetchStatus, connect, disconnect, listenSse }
})
