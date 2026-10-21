import { defineStore } from 'pinia'
import { ref, reactive, watch } from 'vue'
import { http } from '@/api/http'
import type { AiConfig, AiLogEntry, AiStatus, AiTestResult } from '@/types'

// 服务商预设（与后端 PROVIDER_PRESETS 对齐；needKey=false 表示可不填 Key；
// local=true 表示本地运行时，显示"刷新模型列表"）
export interface AiProviderPreset {
  key: string
  name: string
  baseUrl: string
  model: string
  needKey: boolean
  local?: boolean
}

export const AI_PROVIDERS: AiProviderPreset[] = [
  { key: 'openai', name: 'OpenAI', baseUrl: 'https://api.openai.com/v1', model: 'gpt-4o-mini', needKey: true },
  { key: 'deepseek', name: 'DeepSeek', baseUrl: 'https://api.deepseek.com/v1', model: 'deepseek-chat', needKey: true },
  { key: 'qwen', name: '通义千问', baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen-plus', needKey: true },
  { key: 'kimi', name: 'Kimi', baseUrl: 'https://api.moonshot.cn/v1', model: 'moonshot-v1-8k', needKey: true },
  { key: 'ollama', name: 'Ollama 本地', baseUrl: 'http://127.0.0.1:11434/v1', model: 'qwen2.5:1.5b', needKey: false, local: true },
  { key: 'llamacpp', name: 'llama.cpp', baseUrl: 'http://127.0.0.1:8080/v1', model: '', needKey: false, local: true },
  { key: 'lmstudio', name: 'LM Studio', baseUrl: 'http://127.0.0.1:1234/v1', model: '', needKey: false, local: true },
]

// 人设模板（与后端 PERSONA_TEMPLATES 对齐）
export const AI_PERSONAS: { key: string; name: string; hint: string; prompt: string }[] = [
  { key: 'gentle', name: '温柔陪伴', hint: '温和体贴，像朋友一样关心观众', prompt: '你是直播间温柔陪伴型 AI 助手，语气温和体贴，像朋友一样关心观众。回复控制在 {n} 字以内，简洁口语化，不要长篇大论。' },
  { key: 'geek', name: '高冷技术宅', hint: '话不多但专业，偶尔技术梗', prompt: '你是直播间高冷技术宅 AI 助手，话不多但专业，偶尔冒出技术梗。回复控制在 {n} 字以内，简洁口语化，不要长篇大论。' },
  { key: 'funny', name: '沙雕搞笑', hint: '幽默爱玩梗，能接住弹幕的梗', prompt: '你是直播间沙雕搞笑 AI 助手，说话幽默爱玩梗，能自然接住弹幕的梗。回复控制在 {n} 字以内，简洁口语化，不要长篇大论。' },
  { key: 'host', name: '专业主持人', hint: '热情引导互动，活跃气氛', prompt: '你是直播间专业主持人 AI 助手，热情引导互动，回复观众并活跃气氛。回复控制在 {n} 字以内，简洁口语化，不要长篇大论。' },
  { key: 'custom', name: '自定义', hint: '使用下方自定义人设 Prompt', prompt: '' },
]

// 触发方式 -> 限流推荐档位（切换触发方式时联动推荐值）
export const TRIGGER_LIMIT_PRESETS: Record<string, { globalCool: number; userCool: number; perMinute: number }> = {
  prefix: { globalCool: 5, userCool: 10, perMinute: 20 },   // @前缀：用户主动召唤，高频率
  keyword: { globalCool: 15, userCool: 30, perMinute: 12 }, // 关键词：半主动，中频率
  all: { globalCool: 30, userCool: 60, perMinute: 10 },     // 全量：每条都可能触发，严格限流
}

function defaultConfig(): AiConfig {
  return {
    enabled: false,
    provider: 'openai',
    base_url: 'https://api.openai.com/v1',
    api_key: '',
    model: 'gpt-4o-mini',
    temperature: 0.7,
    max_tokens: 80,
    persona: 'host',
    system_prompt: '',
    target_words: 20,
    max_reply_len: 20,
    forbidden_check: true,
    trigger: {
      prefixEnabled: false,
      prefixes: [],
      keywordEnabled: false,
      keywords: [],
      allEnabled: false,
      probability: 100,
      userBlacklist: [],
    },
    throttle: { globalCool: 30, userCool: 60, perMinute: 10 },
    context: { mode: 'B', window: 4, globalWindow: 15, expireMinutes: 5, withUsername: true, budget: 1200, maxInputLen: 100, dedupeSeconds: 60 },
    call: { concurrency: 1, timeout: 90, retry: 1, keep_alive: 30, thinking: 'auto' },
    filter: { triggerBlacklist: [], replyBlacklist: [] },
    persona_prompts: {},
  }
}

// 掩码形态判断：后端 get_config 已脱敏，含 *** 的值为占位掩码，禁止回写覆盖真实 Key
function isMasked(value: string): boolean {
  return typeof value === 'string' && value.includes('***')
}

export const useAiStore = defineStore('ai', () => {
  const config = reactive<AiConfig>(defaultConfig())
  const loaded = ref(false)
  const saving = ref(false)
  const status = reactive<AiStatus>({ ok: false, checked: false, last_check: 0, latency_ms: 0, enabled: false })
  const logs = ref<AiLogEntry[]>([])
  const testing = ref(false)
  const sending = ref(false)
  const savedTick = ref(0)  // 每次保存成功 +1，供页面显示"已自动保存"反馈

  let configTimer: ReturnType<typeof setTimeout> | null = null

  function scheduleConfigSave() {
    if (configTimer) clearTimeout(configTimer)
    configTimer = setTimeout(() => {
      configTimer = null
      saveConfig()
    }, 800)
  }

  // 立即提交 pending 的防抖保存（失焦/离开页面前调用，避免 800ms 窗口内刷新丢失修改）
  function flushConfigSave(): void {
    if (configTimer) {
      clearTimeout(configTimer)
      configTimer = null
    }
    if (loaded.value) {
      saveConfig()
    }
  }

  // 800ms 防抖自动保存：任一字段变更后延迟提交（deep watch，loaded 前置闸门）
  watch(config, () => {
    if (loaded.value) scheduleConfigSave()
  }, { deep: true })

  function applyConfig(data: Partial<AiConfig>) {
    const d = data || {}
    config.enabled = Boolean(d.enabled)
    config.provider = d.provider || 'openai'
    config.base_url = d.base_url || ''
    config.api_key = d.api_key || ''
    config.model = d.model || ''
    config.temperature = Number(d.temperature ?? 0.7)
    config.max_tokens = Number(d.max_tokens ?? 120)
    config.persona = d.persona || 'host'
    config.persona_prompts =
      d.persona_prompts && typeof d.persona_prompts === 'object' ? { ...d.persona_prompts } : {}
    // System Prompt 显示值：当前人设定制优先；无则兼容旧全局字段；再无则填模板（与切换行为一致）
    config.system_prompt = config.persona_prompts[config.persona] || d.system_prompt || ''
    if (!config.system_prompt && config.persona !== 'custom') {
      const tpl = AI_PERSONAS.find((x) => x.key === config.persona)?.prompt || ''
      config.system_prompt = tpl.replace(/\{n\}/g, String(config.target_words ?? 20))
    }
    config.target_words = Number(d.target_words ?? 20)
    config.max_reply_len = Number(d.max_reply_len ?? 20)
    config.forbidden_check = d.forbidden_check !== false
    Object.assign(config.trigger, {
      prefixEnabled: Boolean(d.trigger?.prefixEnabled),
      prefixes: Array.isArray(d.trigger?.prefixes) ? d.trigger.prefixes : [],
      keywordEnabled: Boolean(d.trigger?.keywordEnabled),
      keywords: Array.isArray(d.trigger?.keywords) ? d.trigger.keywords : [],
      allEnabled: Boolean(d.trigger?.allEnabled),
      probability: Number(d.trigger?.probability ?? 100),
      userBlacklist: Array.isArray(d.trigger?.userBlacklist) ? d.trigger.userBlacklist : [],
    })
    Object.assign(config.throttle, {
      globalCool: Number(d.throttle?.globalCool ?? 30),
      userCool: Number(d.throttle?.userCool ?? 60),
      perMinute: Number(d.throttle?.perMinute ?? 10),
    })
    Object.assign(config.context, {
      mode: (['A', 'B', 'C'].includes(d.context?.mode as string) ? d.context!.mode : 'B') as 'A' | 'B' | 'C',
      window: Number(d.context?.window ?? 4),
      globalWindow: Number(d.context?.globalWindow ?? 15),
      expireMinutes: Number(d.context?.expireMinutes ?? 5),
      withUsername: d.context?.withUsername !== false,
      budget: Number(d.context?.budget ?? 1200),
      maxInputLen: Number(d.context?.maxInputLen ?? 100),
      dedupeSeconds: Number(d.context?.dedupeSeconds ?? 60),
    })
    Object.assign(config.call, {
      concurrency: Number(d.call?.concurrency ?? 1),
      timeout: Number(d.call?.timeout ?? 90),
      retry: Number(d.call?.retry ?? 1),
      keep_alive: Number(d.call?.keep_alive ?? 30),
      thinking: ['on', 'off'].includes(String(d.call?.thinking)) ? String(d.call?.thinking) as 'on' | 'off' : 'auto',
    })
    Object.assign(config.filter, {
      triggerBlacklist: Array.isArray(d.filter?.triggerBlacklist) ? d.filter.triggerBlacklist : [],
      replyBlacklist: Array.isArray(d.filter?.replyBlacklist) ? d.filter.replyBlacklist : [],
    })
  }

  async function loadConfig(): Promise<void> {
    try {
      const data = await http.get<AiConfig>('/ai/config')
      if (data) {
        applyConfig(data)
        loaded.value = true
      }
    } catch (e) {
      console.error('加载 AI 配置失败', e)
    }
  }

  async function saveConfig(): Promise<boolean> {
    saving.value = true
    try {
      // 掩码 Key 跳过回写；空串也不回写（防止误清空真实 Key）
      const payload: Partial<AiConfig> = {
        enabled: config.enabled,
        provider: config.provider,
        base_url: config.base_url,
        model: config.model,
        temperature: config.temperature,
        max_tokens: config.max_tokens,
        persona: config.persona,
        system_prompt: config.system_prompt,
        target_words: config.target_words,
        max_reply_len: config.max_reply_len,
        forbidden_check: config.forbidden_check,
        trigger: {
          prefixEnabled: config.trigger.prefixEnabled,
          prefixes: config.trigger.prefixes.filter((s) => String(s).trim() !== ''),
          keywordEnabled: config.trigger.keywordEnabled,
          keywords: config.trigger.keywords.filter((s) => String(s).trim() !== ''),
          allEnabled: config.trigger.allEnabled,
          probability: config.trigger.probability,
          userBlacklist: config.trigger.userBlacklist.filter((s) => String(s).trim() !== ''),
        },
        throttle: { ...config.throttle },
        context: { ...config.context },
        call: { ...config.call },
        filter: {
          triggerBlacklist: config.filter.triggerBlacklist.filter((s) => String(s).trim() !== ''),
          replyBlacklist: config.filter.replyBlacklist.filter((s) => String(s).trim() !== ''),
        },
        // 按人设持久化：当前编辑的 System Prompt 归入当前人设，切换后不丢失
        persona_prompts: { ...(config.persona_prompts || {}), [config.persona]: config.system_prompt },
      }
      if (!isMasked(config.api_key) && config.api_key !== '') payload.api_key = config.api_key
      const res = await http.post<any>('/ai/config', payload)
      if (res?.config) {
        // 回填后端规范化结果（掩码 Key 等），保持本地与服务端一致
        const masked = res.config.api_key
        if (masked && isMasked(masked)) config.api_key = masked
      }
      if (res) savedTick.value++
      return Boolean(res)
    } catch (e) {
      console.error('保存 AI 配置失败', e)
      return false
    } finally {
      saving.value = false
    }
  }

  async function fetchStatus(force = false): Promise<void> {
    try {
      const data = await http.get<AiStatus>('/ai/status')
      if (data) {
        status.ok = Boolean(data.ok)
        status.checked = Boolean(data.checked)
        status.last_check = Number(data.last_check) || 0
        status.latency_ms = Number(data.latency_ms) || 0
        status.enabled = Boolean(data.enabled)
        if (force) {
          // 强制即时探测：重新拉一次（后端 /ai/status 无 force 参数，用 /ai/test ping 兜底）
          try {
            const t = await http.post<AiTestResult>('/ai/test', { mode: 'ping' })
            if (t) {
              status.ok = Boolean(t.ok)
              status.latency_ms = Number(t.latency_ms) || 0
            }
          } catch { /* 忽略 ping 失败，保留原状态 */ }
        }
      }
    } catch (e) {
      console.error('获取 AI 状态失败', e)
    }
  }

  async function testConnection(): Promise<AiTestResult> {
    testing.value = true
    try {
      return await http.post<AiTestResult>('/ai/test', { mode: 'ping' }) || { ok: false, error: '无响应' }
    } catch (e: any) {
      return { ok: false, error: e?.message || '测试请求异常' }
    } finally {
      testing.value = false
    }
  }

  async function testMessage(message: string): Promise<AiTestResult> {
    testing.value = true
    try {
      return await http.post<AiTestResult>('/ai/test', { mode: 'chat', message }) || { ok: false, error: '无响应' }
    } catch (e: any) {
      return { ok: false, error: e?.message || '测试请求异常' }
    } finally {
      testing.value = false
    }
  }

  // 手动触发（测试区）：走完整链路（含真实发送弹幕）
  async function sendTest(message: string, username = '测试用户'): Promise<AiTestResult> {
    sending.value = true
    try {
      return await http.post<AiTestResult>('/ai/send', { message, username }) || { ok: false, error: '无响应' }
    } catch (e: any) {
      return { ok: false, error: e?.message || '发送请求异常' }
    } finally {
      sending.value = false
    }
  }

  // 流式测试（测试区打字机）：逐段回调 onDelta；done/error 时结束
  async function streamTest(
    message: string,
    username: string,
    handlers: { onDelta: (t: string) => void; onDone: (r: AiTestResult) => void; onError: (msg: string) => void },
    signal?: AbortSignal,
  ): Promise<void> {
    sending.value = true
    try {
      const resp = await fetch('/ai/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message, username }),
        signal,
      })
      if (!resp.ok || !resp.body) {
        const text = await resp.text()
        let msg = text
        try { const j = JSON.parse(text); msg = j?.error || msg } catch { /* 非 JSON 保留原文 */ }
        handlers.onError(msg || `HTTP ${resp.status}`)
        return
      }
      const reader = resp.body.getReader()
      const decoder = new TextDecoder('utf-8')
      let buf = ''
      let finished = false
      while (!finished) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        let idx: number
        while ((idx = buf.indexOf('\n\n')) >= 0) {
          const frame = buf.slice(0, idx)
          buf = buf.slice(idx + 2)
          for (const line of frame.split('\n')) {
            if (!line.startsWith('data:')) continue
            const data = line.slice(5).trim()
            if (!data) continue
            try {
              const ev = JSON.parse(data)
              if (ev.event === 'delta' && typeof ev.text === 'string') {
                handlers.onDelta(ev.text)
              } else if (ev.event === 'done') {
                finished = true
                handlers.onDone(ev as AiTestResult)
              } else if (ev.event === 'error') {
                finished = true
                handlers.onError(ev.message || '生成失败')
              }
            } catch { /* 忽略非 JSON 行 */ }
          }
          if (finished) break
        }
      }
      // 流正常结束但未收到 done：视为异常
      if (!finished) handlers.onError('连接已断开，未收到完整结果')
    } catch (e: any) {
      if (e?.name === 'AbortError') return // 主动中止不提示
      handlers.onError(e?.message || '流式请求异常')
    } finally {
      sending.value = false
    }
  }

  async function loadLogs(): Promise<void> {
    try {
      const data = await http.get<{ logs: AiLogEntry[] }>('/ai/logs')
      if (data && Array.isArray(data.logs)) logs.value = data.logs
    } catch (e) {
      console.error('加载 AI 日志失败', e)
    }
  }

  // 本地运行时（Ollama / llama.cpp / LM Studio）已安装模型列表
  const models = ref<string[]>([])
  const modelsError = ref('')

  async function fetchModels(): Promise<{ ok: boolean; models: string[]; error?: string }> {
    modelsError.value = ''
    try {
      const data = await http.get<{ ok: boolean; models?: string[]; error?: string }>('/ai/models')
      if (data && Array.isArray(data.models)) {
        models.value = data.models
        if (!data.ok && data.error) modelsError.value = data.error
        return { ok: Boolean(data.ok), models: data.models, error: data.error }
      }
      modelsError.value = data?.error || '拉取模型列表失败'
      return { ok: false, models: [], error: modelsError.value }
    } catch (e: any) {
      modelsError.value = e?.message || '拉取模型列表异常'
      return { ok: false, models: [], error: modelsError.value }
    }
  }

  return {
    config, loaded, saving, status, logs, testing, sending, models, modelsError, savedTick,
    loadConfig, saveConfig, flushConfigSave, fetchStatus,
    testConnection, testMessage, sendTest, streamTest, loadLogs, fetchModels,
  }
})
