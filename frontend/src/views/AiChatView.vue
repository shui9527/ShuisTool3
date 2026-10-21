<script setup lang="ts">
// AiChatView：AI 弹幕聊天配置页（严格按 ai-chat-preview_v3 审批稿实现）
// 自动保存：store 内 deep watch + 800ms 防抖提交 /ai/config
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useAiStore, AI_PROVIDERS, AI_PERSONAS, TRIGGER_LIMIT_PRESETS } from '@/stores/ai'

const ai = useAiStore()

// ---------------- 自动保存反馈 ----------------
const saveTip = ref('')
let tipTimer: ReturnType<typeof setTimeout> | null = null
watch(() => ai.savedTick, () => {
  saveTip.value = '已自动保存'
  if (tipTimer) clearTimeout(tipTimer)
  tipTimer = setTimeout(() => { saveTip.value = '' }, 1600)
})
// 刷新/关闭页面前兜底提交 pending 修改（textarea 失焦不触发时也能保存）
function onBeforeUnloadSave() {
  ai.flushConfigSave()
}
onMounted(() => window.addEventListener('beforeunload', onBeforeUnloadSave))
onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', onBeforeUnloadSave)
  ai.flushConfigSave()
})

// ---------------- 总开关（master-card） ----------------
const masterEnabled = computed({
  get: () => ai.config.enabled,
  set: (v: boolean) => { ai.config.enabled = v },
})
const providerName = computed(() => AI_PROVIDERS.find((x) => x.key === ai.config.provider)?.name || ai.config.provider)
const activeTriggerNames = computed(() => {
  const list: string[] = []
  if (ai.config.trigger.prefixEnabled) list.push('@前缀')
  if (ai.config.trigger.keywordEnabled) list.push('关键词')
  if (ai.config.trigger.allEnabled) list.push('全量')
  return list
})
const masterSummary = computed(() => {
  if (!ai.config.enabled) return '关闭后所有触发规则失效'
  const trig = activeTriggerNames.value.length ? activeTriggerNames.value.join(' + ') : '未配置触发方式'
  return `${providerName.value} · ${trig} · 冷却 ${ai.config.throttle.globalCool}s`
})

// ---------------- 模型配置 ----------------
const selectedProvider = computed({
  get: () => ai.config.provider,
  set: (v: string) => {
    ai.config.provider = v
    const p = AI_PROVIDERS.find((x) => x.key === v)
    if (p) {
      // 切换预设时同步填充 Base URL / 模型（若当前是空或上次预设值则覆盖）
      ai.config.base_url = p.baseUrl
      ai.config.model = p.model
    }
  },
})
// 本地运行时（Ollama / llama.cpp / LM Studio）：显示"刷新模型列表"
const isLocalProvider = computed(() => AI_PROVIDERS.find((x) => x.key === ai.config.provider)?.local || false)
const isOllama = computed(() => ai.config.provider === 'ollama')
async function refreshModels() {
  const r = await ai.fetchModels()
  if (r.ok && r.models.length && !ai.config.model) {
    ai.config.model = r.models[0]
  }
}

// ---------------- 人设模板 ----------------
// 切换人设：先把当前编辑的 System Prompt 归入旧人设（切换不丢失），
// 再加载新人设的定制 Prompt（有则用之，无则填模板；custom 用自定义内容）
watch(
  () => ai.config.persona,
  (newP, oldP) => {
    if (!ai.loaded) return  // 初始化阶段（applyConfig 赋值）跳过
    if (oldP && oldP !== newP) {
      ai.config.persona_prompts = { ...(ai.config.persona_prompts || {}), [oldP]: ai.config.system_prompt }
    }
    const p = AI_PERSONAS.find((x) => x.key === newP)
    if (!p) return
    const pp = ai.config.persona_prompts || {}
    if (newP === 'custom') {
      ai.config.system_prompt = pp['custom'] || ''
    } else {
      const n = ai.config.target_words ?? 20
      ai.config.system_prompt = pp[newP] || p.prompt.replace(/\{n\}/g, String(n))
    }
  },
)

// 模型配置卡内嵌测试气泡：测试连通 / 发送测试消息
const connResult = ref<{ ok: boolean; msg: string; reply?: string } | null>(null)
async function testConnect() {
  connResult.value = null
  const r = await ai.testConnection()
  connResult.value = r.ok
    ? { ok: true, msg: `连接成功 · 耗时 ${r.latency_ms}ms` }
    : { ok: false, msg: r.error || '连接失败' }
}
async function quickTest() {
  connResult.value = null
  const r = await ai.testMessage('你好，介绍一下自己')
  connResult.value = r.ok
    ? { ok: true, msg: `生成成功 · 耗时 ${r.latency_ms}ms`, reply: r.reply || '' }
    : { ok: false, msg: r.error || '生成失败' }
}

// ---------------- 触发规则与限流 ----------------
// 打开任一触发方式时按推荐档位刷新限流（切换触发方式联动）
function refreshThrottle(kind: 'prefix' | 'keyword' | 'all') {
  const p = TRIGGER_LIMIT_PRESETS[kind]
  ai.config.throttle.globalCool = p.globalCool
  ai.config.throttle.userCool = p.userCool
  ai.config.throttle.perMinute = p.perMinute
}
function onToggleTrigger(kind: 'prefix' | 'keyword' | 'all', value: boolean) {
  if (value) refreshThrottle(kind)
}
function currentThrottleKind(): 'prefix' | 'keyword' | 'all' | '' {
  if (ai.config.trigger.prefixEnabled) return 'prefix'
  if (ai.config.trigger.keywordEnabled) return 'keyword'
  if (ai.config.trigger.allEnabled) return 'all'
  return ''
}
// ---------------- 触发规则/过滤输入（QueueView 排队命令式：直绑本地字符串，失焦写回数组） ----------------
// 禁 computed 双向绑定，避免中英文逗号无法输入；失焦/保存时按 中英文逗号或换行 split 并 trim 过滤空项
const prefixesText = ref('')
const keywordsText = ref('')
const userBlacklistText = ref('')
const triggerBlacklistText = ref('')
const replyBlacklistText = ref('')

function splitList(text: string): string[] {
  return text.split(/[,，\n]+/).map((s) => s.trim()).filter(Boolean)
}

// 加载完成后用后端数组回填输入框（初始为空，等 loadConfig 完成后同步一次）
function syncListInputs() {
  prefixesText.value = ai.config.trigger.prefixes.join('，')
  keywordsText.value = ai.config.trigger.keywords.join('，')
  userBlacklistText.value = ai.config.trigger.userBlacklist.join('，')
  triggerBlacklistText.value = ai.config.filter.triggerBlacklist.join('，')
  replyBlacklistText.value = ai.config.filter.replyBlacklist.join('，')
}

// 失焦：split 写回原数组字段（后端消费数组契约不变），并立即 flush 落盘
function onPrefixBlur() {
  ai.config.trigger.prefixes = splitList(prefixesText.value)
  ai.flushConfigSave()
}
function onKeywordsBlur() {
  ai.config.trigger.keywords = splitList(keywordsText.value)
  ai.flushConfigSave()
}
function onUserBlacklistBlur() {
  ai.config.trigger.userBlacklist = splitList(userBlacklistText.value)
  ai.flushConfigSave()
}
function onTriggerBlacklistBlur() {
  ai.config.filter.triggerBlacklist = splitList(triggerBlacklistText.value)
  ai.flushConfigSave()
}
function onReplyBlacklistBlur() {
  ai.config.filter.replyBlacklist = splitList(replyBlacklistText.value)
  ai.flushConfigSave()
}

// 限流提示等级化（l0 未启用 / l1 宽松-橙 / l3 严格-红，多开取最严档）
const TRIGGER_META: Record<'prefix' | 'keyword' | 'all', { name: string; cls: string; label: string; text: string }> = {
  prefix: { name: '@前缀', cls: 'l1', label: '宽松限流', text: '仅点名触发，弹幕量可控，冷却可设短、回复更跟手。' },
  keyword: { name: '关键词', cls: 'l1', label: '中等限流', text: '关键词触发频率中等，建议中等冷却，避免连发误伤。' },
  all: { name: '全量', cls: 'l3', label: '严格限流', text: '每条弹幕都会判定，必须严格限流，否则会被刷屏打爆。' },
}

// 配置推荐：按场景给出推荐配置（悬停「配置推荐」按钮查看）
// apply.path 指向 ai.config 下的字段（点路径），一键套用时写入并保存；
// 连接身份参数（服务商/base_url/model/api_key）不参与套用，避免误覆盖用户选择
interface RecoCard {
  title: string
  tag: string
  desc: string
  items: { k: string; v: string }[]
  apply?: { path: string; value: unknown }[]
}
const AI_RECOMMENDATIONS: RecoCard[] = [
  {
    title: '本地小模型',
    tag: 'llama.cpp / Ollama · 1-2B',
    desc: '显存 1.5G 内、离线可用；小模型先关思考模式，回复字数放宽',
    items: [
      { k: '服务商', v: 'llama.cpp（或 Ollama 本地）' },
      { k: 'Base URL', v: 'http://127.0.0.1:8082/v1（按实际端口）' },
      { k: '思考模式', v: '关（思考会占满 token 导致答非所问）' },
      { k: 'max_tokens', v: '80 ~ 200' },
      { k: '上下文模式', v: 'B · 同用户小对话' },
      { k: '字符预算', v: '1200 ~ 3000' },
      { k: '限流', v: '全局 5s / 用户 10s / 分钟 20 条' },
      { k: '超时', v: '60 ~ 90 秒' },
    ],
    apply: [
      { path: 'call.thinking', value: 'off' },
      { path: 'max_tokens', value: 200 },
      { path: 'target_words', value: 40 },
      { path: 'max_reply_len', value: 40 },
      { path: 'context.mode', value: 'B' },
      { path: 'context.window', value: 8 },
      { path: 'context.budget', value: 3000 },
      { path: 'context.maxInputLen', value: 100 },
      { path: 'throttle.globalCool', value: 5 },
      { path: 'throttle.userCool', value: 10 },
      { path: 'throttle.perMinute', value: 20 },
      { path: 'call.timeout', value: 90 },
      { path: 'call.concurrency', value: 1 },
      { path: 'call.retry', value: 2 },
    ],
  },
  {
    title: 'API 大模型',
    tag: 'DeepSeek / Qwen / Kimi / OpenAI',
    desc: '云端算力强，可开思考模式、加大上下文与回复上限',
    items: [
      { k: '服务商', v: 'deepseek / qwen / kimi / openai' },
      { k: '思考模式', v: '开（云端按服务商附加）' },
      { k: 'max_tokens', v: '200 ~ 400' },
      { k: '上下文模式', v: 'B · 同用户小对话' },
      { k: '字符预算', v: '3000 ~ 6000' },
      { k: '限流', v: '全局 3s / 用户 8s / 分钟 30 条' },
      { k: '超时', v: '30 ~ 60 秒' },
    ],
    apply: [
      { path: 'call.thinking', value: 'on' },
      { path: 'max_tokens', value: 400 },
      { path: 'target_words', value: 40 },
      { path: 'max_reply_len', value: 40 },
      { path: 'context.mode', value: 'B' },
      { path: 'context.window', value: 8 },
      { path: 'context.budget', value: 4000 },
      { path: 'context.maxInputLen', value: 100 },
      { path: 'throttle.globalCool', value: 3 },
      { path: 'throttle.userCool', value: 8 },
      { path: 'throttle.perMinute', value: 30 },
      { path: 'call.timeout', value: 60 },
      { path: 'call.concurrency', value: 1 },
      { path: 'call.retry', value: 2 },
    ],
  },
  {
    title: '同用户小对话',
    tag: '上下文模式 B',
    desc: 'AI 记住和每个观众聊过什么，适合一对一互动',
    items: [
      { k: '上下文模式', v: 'B · 同用户小对话' },
      { k: '会话条数', v: '6 ~ 8 条（2 条 ≈ 1 轮）' },
      { k: '过期时间', v: '5 ~ 10 分钟' },
      { k: '字符预算', v: '1200 ~ 3000' },
      { k: '携带用户名', v: '开（区分谁说的）' },
      { k: '重复去重', v: '60 秒' },
    ],
    apply: [
      { path: 'context.mode', value: 'B' },
      { path: 'context.window', value: 8 },
      { path: 'context.expireMinutes', value: 10 },
      { path: 'context.budget', value: 2000 },
      { path: 'context.withUsername', value: true },
      { path: 'context.dedupeSeconds', value: 60 },
    ],
  },
  {
    title: '全局浅上下文',
    tag: '上下文模式 C · 短窗',
    desc: '只接最近几十秒全直播间弹幕，话题跟得紧、回复快',
    items: [
      { k: '上下文模式', v: 'C · 全局浅上下文' },
      { k: '时间窗', v: '15 ~ 30 秒' },
      { k: '字符预算', v: '800 ~ 1500' },
    ],
    apply: [
      { path: 'context.mode', value: 'C' },
      { path: 'context.globalWindow', value: 30 },
      { path: 'context.budget', value: 1200 },
    ],
  },
  {
    title: '全局深上下文',
    tag: '上下文模式 C · 长窗',
    desc: '记住较长时间的全直播间话题，适合连续剧情 / 活动解说',
    items: [
      { k: '上下文模式', v: 'C · 全局深上下文' },
      { k: '时间窗', v: '60 ~ 120 秒' },
      { k: '字符预算', v: '3000 ~ 6000（小模型别超 4000）' },
    ],
    apply: [
      { path: 'context.mode', value: 'C' },
      { path: 'context.globalWindow', value: 120 },
      { path: 'context.budget', value: 4000 },
    ],
  },
  {
    title: '单条问答',
    tag: '上下文模式 A',
    desc: '每条弹幕独立回答，无记忆，最省 token、最快',
    items: [
      { k: '上下文模式', v: 'A · 单条' },
      { k: '字符预算', v: '不参与（无上下文）' },
    ],
    apply: [
      { path: 'context.mode', value: 'A' },
    ],
  },
  {
    title: '@前缀点名',
    tag: '触发方式',
    desc: '观众 @AI 才回复，最跟手、弹幕量可控',
    items: [
      { k: '触发方式', v: '@前缀触发（如 @AI）' },
      { k: '限流', v: '全局 5s / 用户 10s / 分钟 20 条' },
      { k: '触发概率', v: '100%' },
    ],
    apply: [
      { path: 'trigger.prefixEnabled', value: true },
      { path: 'trigger.keywordEnabled', value: false },
      { path: 'trigger.allEnabled', value: false },
      { path: 'trigger.probability', value: 100 },
      { path: 'throttle.globalCool', value: 5 },
      { path: 'throttle.userCool', value: 10 },
      { path: 'throttle.perMinute', value: 20 },
    ],
  },
  {
    title: '弹幕刷屏防护',
    tag: '触发方式',
    desc: '直播间人多弹幕密时，降低回复频率防止刷屏',
    items: [
      { k: '触发方式', v: '关键词触发（避免全量）' },
      { k: '限流', v: '全局 15~30s / 用户 30~60s / 分钟 10~12 条' },
      { k: '触发概率', v: '30% ~ 60%' },
    ],
    apply: [
      { path: 'trigger.prefixEnabled', value: false },
      { path: 'trigger.keywordEnabled', value: true },
      { path: 'trigger.allEnabled', value: false },
      { path: 'trigger.probability', value: 50 },
      { path: 'throttle.globalCool', value: 30 },
      { path: 'throttle.userCool', value: 60 },
      { path: 'throttle.perMinute', value: 10 },
    ],
  },
]

// 一键套用：写入 ai.config 对应字段并立即保存（连接身份参数不在 apply 内，不会被覆盖）
function applyRecommendation(card: RecoCard): void {
  if (!card.apply) return
  for (const a of card.apply) {
    const keys = a.path.split('.')
    let obj: Record<string, unknown> = ai.config as unknown as Record<string, unknown>
    for (let i = 0; i < keys.length - 1; i++) {
      const next = obj?.[keys[i]] as Record<string, unknown> | undefined
      if (!next) break
      obj = next
    }
    obj[keys[keys.length - 1]] = a.value
  }
  ai.flushConfigSave()
  ElMessage.success(`已套用「${card.title}」推荐配置`)
}
const throttleHint = computed(() => {
  const k = currentThrottleKind()
  if (!k) return { cls: 'l0', label: '未启用触发方式', text: '至少启用一种触发方式，AI 才会响应弹幕。' }
  const enabled = (['prefix', 'keyword', 'all'] as const).filter((t) => {
    if (t === 'prefix') return ai.config.trigger.prefixEnabled
    if (t === 'keyword') return ai.config.trigger.keywordEnabled
    return ai.config.trigger.allEnabled
  })
  const order = ['prefix', 'keyword', 'all']
  const maxT = enabled.reduce((a, b) => (order.indexOf(a) > order.indexOf(b) ? a : b))
  const meta = TRIGGER_META[maxT]
  const rec = TRIGGER_LIMIT_PRESETS[maxT]
  return {
    cls: meta.cls,
    label: meta.label,
    text: `当前启用：${enabled.map((t) => TRIGGER_META[t].name).join(' + ')}。推荐：全局 ${rec.globalCool}s · 同用户 ${rec.userCool}s · 每分钟 ${rec.perMinute} 条（已自动应用，可手动微调）。${meta.text}`,
  }
})

// ---------------- 上下文 ----------------
const contextMode = computed({
  get: () => ai.config.context.mode,
  set: (v: any) => { ai.config.context.mode = v },
})
const contextModeHint = computed(() => {
  const m = ai.config.context.mode
  if (m === 'A') return '每次触发只携带当前这条弹幕，上下文独立，成本最低'
  if (m === 'B') return '记录同一观众最近几条弹幕与 AI 回复组成小对话，过期自动清空'
  return '全局最近 15 秒内弹幕浅上下文，适合多人接话场景'
})

// ---------------- 测试区（气泡对话，流式打字机，走完整链路） ----------------
const testInput = ref('')
const testChat = ref<{ who: string; text: string; ai: boolean; meta: string; streaming?: boolean }[]>([])
const testBusy = ref(false)
let streamAbort: AbortController | null = null

async function runSend() {
  const msg = testInput.value.trim()
  if (!msg) {
    testChat.value = [{ who: '提示', text: '请先输入模拟弹幕', ai: false, meta: '' }]
    return
  }
  // 中止上一次未结束的流式生成
  streamAbort?.abort()
  const abort = new AbortController()
  streamAbort = abort
  testBusy.value = true
  const aiIdx = 1
  testChat.value = [
    { who: '测试用户', text: msg, ai: false, meta: '弹幕' },
    { who: providerName.value, text: '', ai: true, meta: '生成中…', streaming: true },
  ]
  try {
    await ai.streamTest(msg, '测试用户', {
      onDelta: (t) => {
        const c = testChat.value[aiIdx]
        if (c) { c.text += t; c.meta = '生成中…' }
      },
      onDone: (r) => {
        const metaParts: string[] = []
        if (r.latency_ms) metaParts.push(`${r.latency_ms}ms`)
        if (r.sent_ok) metaParts.push('已发送')
        else if (r.note) metaParts.push(r.note)
        if (r.usage) metaParts.push(r.usage)
        testChat.value[aiIdx] = {
          who: providerName.value,
          text: (r.filtered ? '（已过滤不发送）' : '') + (r.reply || '（无回复）'),
          ai: true,
          meta: metaParts.length ? metaParts.join(' · ') : (r.filtered ? '已过滤' : ''),
        }
      },
      onError: (msg) => {
        testChat.value[aiIdx] = { who: providerName.value, text: '', ai: true, meta: msg }
      },
    }, abort.signal)
  } finally {
    testBusy.value = false
  }
}

// ---------------- 交互日志（轮询） ----------------
let logTimer: number | null = null
onMounted(async () => {
  await ai.loadConfig()
  await ai.fetchStatus()
  syncListInputs()
  ai.loadLogs()
  // 本地运行时自动拉取已安装模型列表
  if (isLocalProvider.value) ai.fetchModels()
  logTimer = window.setInterval(() => { ai.loadLogs() }, 5000)
})
onBeforeUnmount(() => {
  // 离开页面立即提交 pending 防抖保存，避免 800ms 窗口内修改丢失（System Prompt 等字段被还原）
  ai.flushConfigSave()
  streamAbort?.abort()
  if (logTimer !== null) { clearInterval(logTimer); logTimer = null }
})

const logTriggerName = (t: string) => ({
  prefix: '@前缀', keyword: '关键词', all: '全量', manual: '手动测试', blacklist: '过滤', user_blacklist: '用户黑名单',
}[t] || t)
</script>

<template>
  <div class="ai-page">
    <!-- master-card：大总开关 -->
    <div class="shui-card master-card">
      <el-switch v-model="masterEnabled" size="large" class="master-toggle" />
      <div class="mc-text">
        <div class="mc-title">AI 自动回复</div>
        <div class="mc-sub">弹幕触发 AI 生成回复并发送到直播间 · 关闭后所有触发规则失效</div>
      </div>
      <div class="mc-status">
        <div class="mc-st" :class="ai.config.enabled ? 'on' : 'off'">{{ ai.config.enabled ? '已启用' : '已关闭' }}</div>
        <div class="mc-summary">{{ masterSummary }}</div>
      </div>
      <!-- 配置推荐：悬停查看各场景推荐配置 -->
      <div class="reco-btn-wrap">
        <el-popover trigger="hover" placement="bottom-end" :width="820" popper-class="ai-reco-popper">
          <template #reference>
            <el-button size="small" plain class="reco-btn">
              <span class="reco-q">?</span><span>配置推荐</span>
            </el-button>
          </template>
          <div class="reco-panel">
            <div class="reco-title">配置推荐</div>
            <div class="reco-sub">鼠标悬停此按钮时展示 · 卡片下方「套用此配置」一键填充并保存；连接参数（服务商 / 地址 / 模型）不会被套用，请自行选好后再套用</div>
            <div class="reco-grid">
              <div v-for="r in AI_RECOMMENDATIONS" :key="r.title" class="reco-card">
                <div class="reco-card-head">
                  <span class="reco-card-title">{{ r.title }}</span>
                  <span class="reco-tag">{{ r.tag }}</span>
                </div>
                <div class="reco-card-desc">{{ r.desc }}</div>
                <div class="reco-items">
                  <div v-for="it in r.items" :key="it.k" class="reco-item">
                    <span class="reco-k">{{ it.k }}</span>
                    <span class="reco-v">{{ it.v }}</span>
                  </div>
                </div>
                <div v-if="r.apply" class="reco-apply">
                  <el-button size="small" type="primary" plain @click="applyRecommendation(r)">套用此配置</el-button>
                </div>
              </div>
            </div>
          </div>
        </el-popover>
      </div>
    </div>

    <div class="ai-grid">
      <div class="ai-col">
        <!-- 模型配置 -->
        <div class="shui-card ai-card">
          <div class="ai-card-title">模型配置</div>
          <div class="ai-form-grid">
            <div class="ai-form-row">
              <span class="ai-label">服务商</span>
              <el-select v-model="selectedProvider" style="width: 200px">
                <el-option v-for="p in AI_PROVIDERS" :key="p.key" :label="p.name" :value="p.key" />
              </el-select>
              <span class="ai-hint dim">切换预设自动填充地址与模型，可手动修改</span>
            </div>
            <div class="ai-form-row">
              <span class="ai-label">Base URL</span>
              <el-input v-model="ai.config.base_url" placeholder="https://api.openai.com/v1" style="width: 380px" />
            </div>
            <div class="ai-form-row">
              <span class="ai-label">API Key</span>
              <el-input v-model="ai.config.api_key" type="password" show-password placeholder="sk-..." style="width: 380px" />
              <span class="ai-hint dim">仅存本地，显示为掩码，不参与自动保存回写</span>
            </div>
            <div class="ai-form-row">
              <span class="ai-label">模型</span>
              <el-input v-model="ai.config.model" placeholder="gpt-4o-mini" style="width: 220px" />
              <el-button v-if="isLocalProvider" plain size="small" :loading="ai.testing" @click="refreshModels">刷新模型</el-button>
              <span class="ai-hint dim">OpenAI 兼容模型名</span>
            </div>
            <div v-if="isLocalProvider && ai.models.length" class="ai-form-row">
              <span class="ai-label" />
              <el-select v-model="ai.config.model" filterable allow-create style="width: 300px">
                <el-option v-for="m in ai.models" :key="m" :label="m" :value="m" />
              </el-select>
              <span class="ai-hint dim" :class="{ fail: ai.modelsError }">{{ ai.modelsError || '本机已安装模型，可直接选择' }}</span>
            </div>
            <div class="ai-form-row">
              <span class="ai-label">温度</span>
              <el-input-number v-model="ai.config.temperature" :min="0" :max="2" :step="0.1" />
              <span class="ai-hint dim">数值越大回复越发散</span>
            </div>
            <div class="ai-form-row">
              <span class="ai-label">最大 Token</span>
              <el-input-number v-model="ai.config.max_tokens" :min="16" :max="4096" />
              <span class="ai-hint dim">输出 token 上限，建议 ≥ 目标字数 × 3</span>
            </div>
            <div class="ai-form-row call-sep">
              <span class="ai-label">并发数</span>
              <el-input-number v-model="ai.config.call.concurrency" :min="1" :max="8" />
              <span class="ai-hint dim">同时推理请求数，本地模型建议 1（弹幕洪峰自动排队/丢弃）</span>
            </div>
            <div class="ai-form-row">
              <span class="ai-label">请求超时</span>
              <el-input-number v-model="ai.config.call.timeout" :min="10" :max="600" /><span class="dim"> s</span>
              <span class="ai-hint dim">本地 CPU 推理慢，可调大</span>
            </div>
            <div class="ai-form-row">
              <span class="ai-label">失败重试</span>
              <el-input-number v-model="ai.config.call.retry" :min="0" :max="3" /><span class="dim"> 次</span>
              <span class="ai-hint dim">仅网络错误 / 限流 / 5xx 重试</span>
            </div>
            <div v-if="isOllama" class="ai-form-row">
              <span class="ai-label">Ollama 常驻</span>
              <el-input-number v-model="ai.config.call.keep_alive" :min="0" :max="1440" /><span class="dim"> 分钟</span>
              <span class="ai-hint dim">0=不发送 keep_alive（服务端默认 5 分钟卸载模型）</span>
            </div>
            <div class="ai-form-row">
              <span class="ai-label">思考模式</span>
              <el-select v-model="ai.config.call.thinking" style="width: 132px">
                <el-option label="自动" value="auto" />
                <el-option label="开启" value="on" />
                <el-option label="关闭" value="off" />
              </el-select>
              <span class="ai-hint dim">
                Qwen3 等模型默认思考会占满字数上限导致回复为空：本地模型保持「自动」（自动关闭）即可；
                接入支持思考的云端 API（OpenAI/DeepSeek/千问/Kimi）时选「开启」可启用深度推理，不支持的端点会自动忽略该参数
              </span>
            </div>
            <div class="ai-form-row mc-actions">
              <span class="ai-label" />
              <el-button plain :loading="ai.testing" @click="testConnect">测试连通</el-button>
              <el-button type="primary" :loading="ai.testing" @click="quickTest">发送测试消息</el-button>
            </div>
            <div v-if="connResult" class="test-area" :class="connResult.ok ? 'ok' : 'fail'">
              <div class="test-line">{{ connResult.msg }}</div>
              <div v-if="connResult.reply" class="ai-bubble">{{ connResult.reply }}</div>
            </div>
          </div>
        </div>

        <!-- AI 行为 -->
        <div class="shui-card ai-card">
          <div class="ai-card-title">AI 行为 <span v-if="saveTip" class="save-tip">{{ saveTip }}</span></div>
          <div class="ai-form-grid">
            <div class="ai-form-row">
              <span class="ai-label">人设模板</span>
              <el-select v-model="ai.config.persona" style="width: 200px">
                <el-option v-for="p in AI_PERSONAS" :key="p.key" :label="p.name" :value="p.key" />
              </el-select>
              <span class="ai-hint dim">{{ AI_PERSONAS.find((p) => p.key === ai.config.persona)?.hint }}</span>
            </div>
            <div class="ai-form-row">
              <span class="ai-label">System Prompt</span>
              <el-input
                v-model="ai.config.system_prompt"
                type="textarea"
                :rows="3"
                placeholder="留空则使用所选人设模板的默认提示词；填写后作为自定义人设 Prompt 优先生效"
                class="prompt-area"
                @blur="ai.flushConfigSave()"
              />
            </div>
            <div class="ai-form-row">
              <span class="ai-label">回复目标字数</span>
              <el-input-number v-model="ai.config.target_words" :min="5" :max="60" />
              <span class="ai-hint dim">写入人设 Prompt，提示 AI 控制回复长度</span>
            </div>
            <div class="ai-form-row">
              <span class="ai-label">长度上限</span>
              <el-input-number v-model="ai.config.max_reply_len" :min="1" :max="50" />
              <span class="ai-hint dim">最终发送上限（B站弹幕约 20 字）；max_tokens 控制模型侧生成量</span>
            </div>
          </div>
        </div>

        <!-- 测试区 -->
        <div class="shui-card ai-card">
          <div class="ai-card-title">测试区</div>
          <div class="ai-form-row">
            <span class="ai-label">模拟弹幕</span>
            <el-input v-model="testInput" placeholder="@AI 今天天气怎么样？" style="width: 300px" @keyup.enter="runSend" />
            <el-button type="primary" :loading="testBusy || ai.sending" @click="runSend">发送</el-button>
          </div>
          <div class="test-area chat">
            <template v-if="testChat.length === 0">
              <div class="test-empty dim">输入模拟弹幕并发送，走完整链路（含真实发送）验证效果</div>
            </template>
            <template v-for="(c, i) in testChat" :key="i">
              <div class="chat-row" :class="c.ai ? 'ai' : 'user'">
                <div class="chat-bubble" :class="c.ai ? 'ai' : 'user'">
                  <div class="chat-who">{{ c.who }}</div>
                  <div class="chat-text" :class="{ streaming: c.streaming }">{{ c.text }}</div>
                  <div v-if="c.meta" class="chat-meta dim">{{ c.meta }}</div>
                </div>
              </div>
            </template>
          </div>
        </div>
      </div>

      <div class="ai-col">
        <!-- 触发规则 + 限流 -->
        <div class="shui-card ai-card">
          <div class="ai-card-title">触发规则 + 限流 <span class="dim">（可多选，任一命中即触发）</span></div>
          <div class="trigger-list">
            <!-- @前缀 -->
            <div class="trigger-row" :class="{ on: ai.config.trigger.prefixEnabled }">
              <div class="trigger-head">
                <el-switch v-model="ai.config.trigger.prefixEnabled" @change="(v: boolean) => onToggleTrigger('prefix', v)" />
                <span class="trigger-name">@前缀触发</span>
                <span class="trigger-desc dim">弹幕以指定前缀开头时触发</span>
              </div>
              <div v-if="ai.config.trigger.prefixEnabled" class="trigger-config">
                <el-input v-model="prefixesText" placeholder="如：@AI，@小助手" style="width: 260px" @blur="onPrefixBlur" />
                <span class="dim">多个前缀用英文逗号分隔</span>
              </div>
              <div class="trigger-example dim">例：@AI 今天天气怎么样？</div>
            </div>
            <!-- 关键词 -->
            <div class="trigger-row" :class="{ on: ai.config.trigger.keywordEnabled }">
              <div class="trigger-head">
                <el-switch v-model="ai.config.trigger.keywordEnabled" @change="(v: boolean) => onToggleTrigger('keyword', v)" />
                <span class="trigger-name">关键词触发</span>
                <span class="trigger-desc dim">弹幕命中任一关键词时触发</span>
              </div>
              <div v-if="ai.config.trigger.keywordEnabled" class="trigger-config">
                <el-input v-model="keywordsText" placeholder="如：唱首歌，讲个笑话" style="width: 260px" @blur="onKeywordsBlur" />
                <span class="dim">多个关键词用英文逗号分隔</span>
              </div>
              <div class="trigger-example dim">例：唱首歌 / 讲个笑话</div>
            </div>
            <!-- 全量 -->
            <div class="trigger-row" :class="{ on: ai.config.trigger.allEnabled }">
              <div class="trigger-head">
                <el-switch v-model="ai.config.trigger.allEnabled" @change="(v: boolean) => onToggleTrigger('all', v)" />
                <span class="trigger-name">全量触发</span>
                <span class="trigger-desc dim">直播间每条弹幕都判定（概率触发）</span>
              </div>
              <div class="trigger-example dim">例：任何弹幕按概率回复</div>
            </div>
          </div>

          <div class="ai-form-row trigger-input">
            <span class="ai-label">触发概率</span>
            <el-slider v-model="ai.config.trigger.probability" :min="0" :max="100" style="width: 220px" />
            <span class="ai-hint dim">{{ ai.config.trigger.probability }}%</span>
          </div>

          <!-- 用户黑名单 -->
          <div class="ai-form-row trigger-input">
            <span class="ai-label">用户黑名单</span>
            <el-input v-model="userBlacklistText" placeholder="如：用户名A，用户B（昵称或 UID）" style="width: 260px" @blur="onUserBlacklistBlur" />
            <span class="ai-hint dim">名单内的用户弹幕不触发 AI（多个用英文逗号分隔）</span>
          </div>

          <el-divider />
          <div class="throttle-row">
            <div class="throttle-item">
              <span class="ai-label">全局冷却</span>
              <el-input-number v-model="ai.config.throttle.globalCool" :min="0" :max="600" /><span class="dim"> s</span>
            </div>
            <div class="throttle-item">
              <span class="ai-label">同用户冷却</span>
              <el-input-number v-model="ai.config.throttle.userCool" :min="0" :max="600" /><span class="dim"> s</span>
            </div>
            <div class="throttle-item">
              <span class="ai-label">每分钟上限</span>
              <el-input-number v-model="ai.config.throttle.perMinute" :min="1" :max="120" /><span class="dim"> 条</span>
            </div>
          </div>
          <div class="throttle-hint" :class="throttleHint.cls">
            <span class="th-hint-label">{{ throttleHint.label }}</span>
            <span class="th-hint-text">{{ throttleHint.text }}</span>
          </div>
        </div>

        <!-- 上下文 -->
        <div class="shui-card ai-card">
          <div class="ai-card-title">上下文</div>
          <div class="ai-form-grid">
            <div class="ai-form-row">
              <span class="ai-label">模式</span>
              <el-select v-model="contextMode" style="width: 220px">
                <el-option label="A · 单条" value="A" />
                <el-option label="B · 同用户小对话" value="B" />
                <el-option label="C · 全局浅上下文" value="C" />
              </el-select>
              <span class="ai-hint dim">{{ contextModeHint }}</span>
            </div>
            <template v-if="ai.config.context.mode === 'B'">
              <div class="ai-form-row">
                <span class="ai-label">会话条数</span>
                <el-input-number v-model="ai.config.context.window" :min="1" :max="10" /><span class="dim"> 条</span>
                <span class="ai-hint dim">消息条数（含 AI 回复，2 条 ≈ 1 轮对话）</span>
              </div>
              <div class="ai-form-row">
                <span class="ai-label">过期时间</span>
                <el-input-number v-model="ai.config.context.expireMinutes" :min="1" :max="60" /><span class="dim"> 分钟</span>
              </div>
            </template>
            <template v-if="ai.config.context.mode === 'C'">
              <div class="ai-form-row">
                <span class="ai-label">时间窗</span>
                <el-input-number v-model="ai.config.context.globalWindow" :min="5" :max="120" /><span class="dim"> 秒</span>
              </div>
            </template>
            <div class="ai-form-row">
              <span class="ai-label">字符预算</span>
              <el-input-number v-model="ai.config.context.budget" :min="200" :max="8000" :step="100" /><span class="dim"> 字</span>
              <span class="ai-hint dim">上下文总长度上限，超出自动丢弃最早的弹幕</span>
            </div>
            <div class="ai-form-row">
              <span class="ai-label">重复去重</span>
              <el-input-number v-model="ai.config.context.dedupeSeconds" :min="0" :max="600" /><span class="dim"> s</span>
              <span class="ai-hint dim">同用户相同弹幕在窗口内只触发一次（0=关闭）</span>
            </div>
            <div class="ai-form-row">
              <span class="ai-label">携带用户名</span>
              <el-switch v-model="ai.config.context.withUsername" />
              <span class="ai-hint dim">上下文条目以「昵称说：内容」形式组织</span>
            </div>
          </div>
        </div>

        <!-- 内容过滤 -->
        <div class="shui-card ai-card">
          <div class="ai-card-title">内容过滤</div>
          <div class="ai-form-grid">
            <div class="ai-form-row">
              <span class="ai-label">触发黑名单</span>
              <el-input
                v-model="triggerBlacklistText"
                placeholder="如：加微信，广告，抽奖"
                style="width: 340px"
                @blur="onTriggerBlacklistBlur"
              />
              <span class="ai-hint dim">多个词用英文逗号分隔 · 命中这些词的弹幕不触发 AI</span>
            </div>
            <div class="ai-form-row">
              <span class="ai-label">回复过滤</span>
              <el-input
                v-model="replyBlacklistText"
                placeholder="如：微信，私聊，加群"
                style="width: 340px"
                @blur="onReplyBlacklistBlur"
              />
              <span class="ai-hint dim">多个词用英文逗号分隔 · AI 回复命中这些词则不发送</span>
            </div>
            <div class="ai-form-row">
              <span class="ai-label">敏感过滤</span>
              <el-switch v-model="ai.config.forbidden_check" />
              <span class="ai-hint dim">内置词库（联系方式/广告/赌博等）+ 手机号/QQ 格式，命中则不发送</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 交互日志 -->
    <div class="shui-card ai-card ai-card-log">
      <div class="ai-card-title">交互日志 <span class="dim">（最近 {{ ai.logs.length }} 条 · 5s 刷新）</span></div>
      <div v-if="ai.logs.length === 0" class="log-empty">暂无日志：开启 AI 总开关并配置触发方式后，命中弹幕将在此展示</div>
      <div v-else class="log-list">
        <div v-for="(log, i) in ai.logs.slice().reverse()" :key="i" class="log-item">
          <span class="log-time dim">{{ log.time }}</span>
          <span class="log-user">{{ log.username }}</span>
          <span class="log-danmu">「{{ log.danmu }}」</span>
          <span class="log-trigger tag">{{ logTriggerName(log.trigger) }}</span>
          <template v-if="log.filtered">
            <span class="log-reply filtered">{{ log.note || '已过滤' }}</span>
          </template>
          <template v-else>
            <span class="log-reply">{{ log.reply }}</span>
            <span v-if="log.sent_ok !== undefined" class="log-sent" :class="log.sent_ok ? 'ok' : 'fail'">
              {{ log.sent_ok ? '已发送' : '未发送' }}
            </span>
            <span v-if="log.usage" class="log-usage dim">{{ log.usage }}</span>
            <span v-if="!log.sent_ok && log.note" class="log-note dim">{{ log.note }}</span>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ai-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
/* 横屏两列：参考 SenderView 的 sender-grid 布局 */
.ai-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  align-items: start;
}
.ai-col {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
@media (max-width: 1099px) {
  .ai-grid { grid-template-columns: 1fr; }
}
.ai-card { border-radius: 12px; }
.ai-card-title { font-size: 15px; font-weight: 600; margin-bottom: 14px; }
.ai-card-log { border-radius: 12px; }
.ai-form-grid { display: flex; flex-direction: column; gap: 14px; }
.ai-form-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.call-sep { margin-top: 8px; padding-top: 12px; border-top: 1px dashed var(--shui-border); }
.ai-hint.fail { color: #ef4444; }
.ai-label { width: 110px; flex-shrink: 0; font-size: 13px; color: var(--shui-text); }
.ai-hint { font-size: 12px; margin-left: 4px; }
.dim { color: var(--shui-text-dim); }
.prompt-area { flex: 1; min-width: 380px; }
.save-tip { margin-left: 8px; font-size: 12px; color: #16a34a; font-weight: 400; }

/* master-card：大总开关（渐变卡片） */
.master-card {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 20px 24px;
  border-radius: 14px;
  background: linear-gradient(135deg, rgba(77, 141, 255, 0.20), rgba(122, 92, 255, 0.14));
  border: 1px solid rgba(77, 141, 255, 0.38);
}
.master-toggle { flex-shrink: 0; }
.mc-text { flex: 1; min-width: 0; }
.mc-title { font-size: 18px; font-weight: 700; }
.mc-sub { font-size: 12px; color: var(--shui-text-dim); margin-top: 4px; }
.mc-status { text-align: right; flex-shrink: 0; }
.mc-st { font-size: 14px; font-weight: 700; }
.mc-st.on { color: #22c55e; }
.mc-st.off { color: var(--shui-text-dim); }
.mc-summary { font-size: 12px; color: var(--shui-text-dim); margin-top: 4px; }

/* 配置推荐按钮（弹窗内容样式见下方非 scoped 块，popover 挂 body） */
.reco-btn-wrap { flex-shrink: 0; margin-left: 4px; }
.reco-btn { gap: 4px; }
.reco-q {
  display: inline-flex; align-items: center; justify-content: center;
  width: 16px; height: 16px; border-radius: 50%;
  background: rgba(77, 141, 255, 0.16); color: #4d8dff;
  font-size: 12px; font-weight: 700; line-height: 1;
}

/* 模型配置卡内嵌测试区 */
.mc-actions { margin-top: 2px; }
.test-area { margin-top: 6px; padding: 10px 14px; border-radius: 10px; font-size: 13px; }
.test-area.ok { background: rgba(34, 197, 94, 0.08); border: 1px solid rgba(34, 197, 94, 0.3); }
.test-area.fail { background: rgba(239, 68, 68, 0.08); border: 1px solid rgba(239, 68, 68, 0.3); }
.test-line { font-size: 12px; }
.test-area.ok .test-line { color: #22c55e; }
.test-area.fail .test-line { color: #ef4444; }
.ai-bubble {
  margin-top: 8px;
  padding: 10px 14px;
  border-radius: 10px;
  background: rgba(77, 141, 255, 0.12);
  border: 1px solid rgba(77, 141, 255, 0.28);
  font-size: 13px;
}

/* 触发规则：开关行 */
.trigger-list { display: flex; flex-direction: column; gap: 10px; }
.trigger-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  padding: 10px 14px;
  border-radius: 10px;
  border: 1px solid var(--shui-border);
  background: rgba(255, 255, 255, 0.02);
  transition: all 0.2s;
}
.trigger-row.on { border-color: rgba(77, 141, 255, 0.45); background: rgba(77, 141, 255, 0.07); }
.trigger-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.trigger-name { font-size: 13px; font-weight: 600; }
.trigger-desc { font-size: 12px; }
.trigger-config { flex: 1; min-width: 240px; }
.trigger-example { font-size: 12px; flex-shrink: 0; }
.trigger-input { margin: 10px 0 0; }

/* 限流 */
.throttle-row { display: flex; gap: 28px; flex-wrap: wrap; align-items: center; }
.throttle-item { display: flex; align-items: center; gap: 6px; }
.throttle-hint {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin-top: 12px;
  padding: 10px 14px;
  border-radius: 10px;
  font-size: 12px;
}
.throttle-hint.l0 { background: rgba(255, 255, 255, 0.04); border: 1px solid var(--shui-border); color: var(--shui-text-dim); }
.throttle-hint.l1 { background: rgba(245, 158, 11, 0.10); border: 1px solid rgba(245, 158, 11, 0.4); color: #f59e0b; }
.throttle-hint.l3 { background: rgba(239, 68, 68, 0.10); border: 1px solid rgba(239, 68, 68, 0.45); color: #ef4444; }
.th-hint-label { font-weight: 700; flex-shrink: 0; }
.th-hint-text { color: var(--shui-text); line-height: 1.5; }

/* 测试区气泡 */
.test-area.chat { display: flex; flex-direction: column; gap: 10px; margin-top: 12px; padding: 0; border: none; background: none; }
.test-empty { font-size: 12px; padding: 6px 2px; }
.chat-row { display: flex; }
.chat-row.user { justify-content: flex-end; }
.chat-row.ai { justify-content: flex-start; }
.chat-bubble { max-width: 78%; padding: 8px 12px; border-radius: 10px; font-size: 13px; }
.chat-bubble.user { background: rgba(77, 141, 255, 0.15); border: 1px solid rgba(77, 141, 255, 0.35); }
.chat-bubble.ai { background: rgba(255, 255, 255, 0.05); border: 1px solid var(--shui-border); }
.chat-who { font-size: 11px; color: var(--shui-text-dim); margin-bottom: 2px; }
.chat-text { line-height: 1.5; }
.chat-text.streaming::after { content: '▍'; color: #7eb3ff; animation: ai-blink 1s steps(1) infinite; margin-left: 1px; }
@keyframes ai-blink { 50% { opacity: 0; } }
.chat-meta { font-size: 11px; margin-top: 4px; }

/* 日志 */
.log-empty { font-size: 13px; color: var(--shui-text-dim); padding: 12px 0; }
.log-list { display: flex; flex-direction: column; gap: 8px; max-height: 360px; overflow-y: auto; }
.log-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.03);
  font-size: 13px;
  flex-wrap: wrap;
}
.log-time { font-size: 12px; flex-shrink: 0; }
.log-user { color: #7eb3ff; flex-shrink: 0; }
.log-danmu { color: var(--shui-text-dim); flex-shrink: 0; max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.log-trigger { font-size: 11px; flex-shrink: 0; }
.log-usage { font-size: 11px; color: #93c5fd; flex-shrink: 0; }
.tag {
  padding: 2px 8px;
  border-radius: 6px;
  background: rgba(77, 141, 255, 0.15);
  color: #7eb3ff;
  font-size: 11px;
}
.log-reply { color: var(--shui-text); }
.log-reply.filtered { color: #f59e0b; }
.log-sent { font-size: 12px; }
.log-sent.ok { color: #22c55e; }
.log-sent.fail { color: #ef4444; }
.log-note { font-size: 12px; }
</style>

<!-- 配置推荐弹窗：el-popover 渲染到 body，样式需全局（非 scoped） -->
<style>
.ai-reco-popper.el-popper { padding: 0; border-radius: 14px; }
.reco-panel { padding: 18px 20px 20px; }
.reco-title { font-size: 16px; font-weight: 700; }
.reco-sub { font-size: 12px; color: var(--shui-text-dim); margin: 6px 0 14px; }
.reco-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
@media (max-width: 760px) {
  .reco-grid { grid-template-columns: 1fr; }
}
.reco-card {
  border: 1px solid var(--shui-border);
  border-radius: 10px;
  padding: 12px 14px;
  background: rgba(255, 255, 255, 0.02);
}
.reco-card-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.reco-card-title { font-size: 14px; font-weight: 600; }
.reco-tag {
  padding: 1px 7px; border-radius: 6px;
  background: rgba(77, 141, 255, 0.14); color: #7eb3ff;
  font-size: 11px;
}
.reco-card-desc { font-size: 12px; color: var(--shui-text-dim); margin: 5px 0 8px; }
.reco-items { display: flex; flex-direction: column; gap: 3px; }
.reco-item { display: flex; gap: 8px; font-size: 12px; }
.reco-k { width: 88px; flex-shrink: 0; color: var(--shui-text-dim); }
.reco-v { flex: 1; min-width: 0; word-break: break-all; }
.reco-apply { margin-top: 10px; }
</style>
