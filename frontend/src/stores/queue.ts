import { defineStore } from 'pinia'
import { ref } from 'vue'
import { http } from '@/api/http'
import { sseBridge } from '@/api/sse'
import type { QueueData, SongEntry, GiftRecords } from '@/types'

// 归一化后端 music 列表（string[] 旧数据 或 SongEntry[]）为 SongEntry[]
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
      const name = String(item ?? '').trim()
      if (!name) continue
      out.push({ id: '', name, singer: '', username: '', source: '', songmid: '', status: 'pending', playLater: false, createdAt: 0 })
    }
  }
  return out
}

export const useQueueStore = defineStore('queue', () => {
  const queueList = ref<string[]>([])
  const musicList = ref<SongEntry[]>([])
  const giftRecords = ref<GiftRecords>({})
  const queueEnabled = ref(false)
  const loading = ref(false)

  function applyQueue(data: QueueData): void {
    if (!data) return
    if (Array.isArray(data.queue)) queueList.value = data.queue
    if (Array.isArray(data.music)) musicList.value = normalizeMusicList(data.music)
    // B15：礼物累计记录（username -> {coin, expire}），礼物排队模式展示送礼价值
    if (data.gift_records && typeof data.gift_records === 'object') giftRecords.value = data.gift_records
  }

  async function refresh(): Promise<void> {
    loading.value = true
    try {
      const data = await http.get<QueueData>('/get_queue')
      applyQueue(data)
      // 打开页面/刷新时恢复持久化的开关真实状态（不依赖 SSE 广播）
      if (data && typeof data === 'object' && 'queueEnabled' in data) {
        queueEnabled.value = Boolean(data.queueEnabled)
      }
    } catch (e) {
      console.error('获取队列失败', e)
    } finally {
      loading.value = false
    }
  }

  // 乐观更新：点击开关立即切换本地状态（不等网络往返），再异步同步后端；
  // 请求失败回滚到操作前状态。避免 SSE 广播回传前 UI 无响应（"点了没反应"）
  async function openQueue(): Promise<boolean> {
    const prev = queueEnabled.value
    queueEnabled.value = true
    try {
      await http.get<any>('/open-queue')
      return true
    } catch {
      queueEnabled.value = prev
      return false
    }
  }

  async function closeQueue(): Promise<boolean> {
    const prev = queueEnabled.value
    queueEnabled.value = false
    try {
      await http.get<any>('/close-queue')
      return true
    } catch {
      queueEnabled.value = prev
      return false
    }
  }

  async function clearQueue(): Promise<boolean> {
    try {
      await http.get<any>('/clear-queue')
      return true
    } catch {
      return false
    }
  }

  // 乐观更新：点击移除/添加/拖拽排序后本地立即生效（不等网络往返），
  // 再异步同步后端；请求失败回滚到操作前的列表，避免界面与后端不一致
  async function updateQueue(list: string[]): Promise<boolean> {
    const prev = queueList.value
    queueList.value = list
    try {
      await http.post<any>('/update_queue_list', { queue_list: list })
      return true
    } catch {
      queueList.value = prev
      return false
    }
  }

  function listenSse(): void {
    sseBridge.on('queue_changed', (payload: any) => {
      if (payload && Array.isArray(payload.queue)) {
        queueList.value = payload.queue
        if (Array.isArray(payload.music)) musicList.value = normalizeMusicList(payload.music)
        // B15：礼物累计记录随队列广播同步（礼物排队模式展示送礼价值）
        if (payload.gift_records && typeof payload.gift_records === 'object') giftRecords.value = payload.gift_records
      } else if (payload && typeof payload === 'object' && 'queueEnabled' in payload) {
        queueEnabled.value = Boolean(payload.queueEnabled)
      }
    })
    sseBridge.on('queue_status_changed', (payload: any) => {
      if (payload && 'queueEnabled' in payload) queueEnabled.value = Boolean(payload.queueEnabled)
    })
    sseBridge.on('connection_changed', (payload: any) => {
      if (payload && 'queueEnabled' in payload) queueEnabled.value = Boolean(payload.queueEnabled)
    })
  }

  return { queueList, musicList, giftRecords, queueEnabled, loading, refresh, openQueue, closeQueue, clearQueue, updateQueue, listenSse, applyQueue }
})
