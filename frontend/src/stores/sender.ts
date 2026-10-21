import { defineStore } from 'pinia'
import { ref, reactive, watch } from 'vue'
import { http } from '@/api/http'
import { sseBridge } from '@/api/sse'
import type { CookiesData, ReplyRule, SenderConfig } from '@/types'

export const useSenderStore = defineStore('sender', () => {
  // 账号凭据：仅五字段，与功能配置分离
  const cookies = reactive<CookiesData>({
    room_id: '', SESSDATA: '', bili_jct: '', buvid3: '', dedeuserid: '',
  })
  // 功能配置：刷屏内容 + 自动答谢 + 自动回复（持久化到 sender_config.json）
  const config = reactive<SenderConfig>({
    screeningMode: 'loop', interval: '5', text_list: [],
    thanksEnabled: false, thanksMode: 'loop', thanks_list: [],
    replyEnabled: false, replyMatchMode: 'contains', replyCooldown: 0, replyIgnoreSelf: true, reply_list: [],
  })
  const cookiesLoaded = ref(false)
  const configLoaded = ref(false)
  const running = ref(false)
  const saving = ref(false)

  // 800ms 防抖自动保存：任一字段变更后延迟提交整组（deep watch）
  let cookiesTimer: ReturnType<typeof setTimeout> | null = null
  let configTimer: ReturnType<typeof setTimeout> | null = null

  function scheduleCookiesSave() {
    if (cookiesTimer) clearTimeout(cookiesTimer)
    cookiesTimer = setTimeout(() => {
      saveCookies()
    }, 800)
  }

  function scheduleConfigSave() {
    if (configTimer) clearTimeout(configTimer)
    configTimer = setTimeout(() => {
      saveConfig()
    }, 800)
  }

  watch(cookies, () => {
    // 加载赋值阶段（cookiesLoaded 尚未置 true）不触发保存
    if (cookiesLoaded.value) scheduleCookiesSave()
  }, { deep: true })

  watch(config, () => {
    if (configLoaded.value) scheduleConfigSave()
  }, { deep: true })

  async function loadCookies(): Promise<void> {
    try {
      const data = await http.get<CookiesData>('/get_cookies')
      if (data) {
        Object.assign(cookies, {
          room_id: data.room_id || '', SESSDATA: data.SESSDATA || '', bili_jct: data.bili_jct || '',
          buvid3: data.buvid3 || '', dedeuserid: data.dedeuserid || '',
        })
        cookiesLoaded.value = true
      }
    } catch (e) {
      console.error('加载 cookies 失败', e)
    }
  }

  // B3：掩码形态判断——后端 get_cookies 已脱敏，含 *** 的值为占位掩码，禁止回写覆盖真实凭据
  function isMasked(value: string): boolean {
    return typeof value === 'string' && value.includes('***')
  }

  async function saveCookies(): Promise<boolean> {
    saving.value = true
    try {
      // B3：跳过掩码形态字段，仅提交 room_id 与用户实际重新输入的完整值
      const payload: Record<string, string> = { room_id: cookies.room_id }
      if (!isMasked(cookies.SESSDATA)) payload.SESSDATA = cookies.SESSDATA
      if (!isMasked(cookies.bili_jct)) payload.bili_jct = cookies.bili_jct
      if (!isMasked(cookies.buvid3)) payload.buvid3 = cookies.buvid3
      if (!isMasked(cookies.dedeuserid)) payload.dedeuserid = cookies.dedeuserid
      const res = await http.post<any>('/savecookies', payload)
      return Boolean(res)
    } catch (e) {
      console.error('保存 cookies 失败', e)
      return false
    } finally {
      saving.value = false
    }
  }

  async function loadConfig(): Promise<void> {
    try {
      const data = await http.get<SenderConfig>('/get_sender_config')
      if (data) {
        Object.assign(config, {
          screeningMode: data.screeningMode || 'loop',
          interval: data.interval || '5',
          text_list: Array.isArray(data.text_list) ? data.text_list : [],
          thanksEnabled: Boolean(data.thanksEnabled),
          thanksMode: data.thanksMode || 'loop',
          thanks_list: Array.isArray(data.thanks_list) ? data.thanks_list : [],
          replyEnabled: Boolean(data.replyEnabled),
          replyMatchMode: (data.replyMatchMode === 'exact' || data.replyMatchMode === 'regex') ? data.replyMatchMode : 'contains',
          replyCooldown: Number(data.replyCooldown) || 0,
          replyIgnoreSelf: data.replyIgnoreSelf !== false,
          reply_list: Array.isArray(data.reply_list) ? data.reply_list : [],
        })
        configLoaded.value = true
      }
    } catch (e) {
      console.error('加载 sender_config 失败', e)
    }
  }

  async function saveConfig(): Promise<boolean> {
    saving.value = true
    try {
      const reply_list: ReplyRule[] = config.reply_list
        .map((rule) => ({
          keywords: Array.isArray(rule.keywords) ? rule.keywords.map((k) => String(k).trim()).filter(Boolean) : [],
          replies: Array.isArray(rule.replies) ? rule.replies.map((r) => String(r).trim()).filter(Boolean) : [],
        }))
        .filter((rule) => rule.keywords.length > 0 && rule.replies.length > 0)
      const payload = {
        screeningMode: config.screeningMode,
        interval: config.interval,
        text_list: config.text_list.filter((t) => String(t).trim() !== ''),
        thanksEnabled: config.thanksEnabled,
        thanksMode: config.thanksMode,
        thanks_list: config.thanks_list.filter((t) => String(t).trim() !== ''),
        replyEnabled: config.replyEnabled,
        replyMatchMode: config.replyMatchMode,
        replyCooldown: config.replyCooldown,
        replyIgnoreSelf: config.replyIgnoreSelf,
        reply_list,
      }
      const res = await http.post<any>('/save_sender_config', payload)
      return Boolean(res)
    } catch (e) {
      console.error('保存 sender_config 失败', e)
      return false
    } finally {
      saving.value = false
    }
  }

  async function start(): Promise<{ ok: boolean; message: string }> {
    try {
      const res = await http.get<any>('/start_sender')
      if (res) {
        running.value = true
        return { ok: true, message: res.message || '刷屏已启动' }
      }
      return { ok: false, message: '启动失败' }
    } catch (e: any) {
      return { ok: false, message: e?.message || '启动请求异常' }
    }
  }

  async function stop(): Promise<{ ok: boolean; message: string }> {
    try {
      const res = await http.get<any>('/stop_sender')
      if (res) {
        running.value = false
        return { ok: true, message: res.message || '刷屏已停止' }
      }
      return { ok: false, message: '停止失败' }
    } catch (e: any) {
      return { ok: false, message: e?.message || '停止请求异常' }
    }
  }

  async function fetchStatus(): Promise<void> {
    try {
      const data = await http.get<any>('/sender_status')
      if (data) {
        running.value = Boolean(data.running ?? data.biliclientRunning)
      }
    } catch (e) {
      console.error('获取刷屏状态失败', e)
    }
  }

  function listenSse(): void {
    sseBridge.on('sender_changed', (payload: any) => {
      if (payload && 'running' in payload) running.value = Boolean(payload.running)
    })
  }

  return {
    cookies, config, cookiesLoaded, configLoaded, running, saving,
    scheduleCookiesSave, scheduleConfigSave,
    loadCookies, saveCookies, loadConfig, saveConfig,
    start, stop, fetchStatus, listenSse,
  }
})
