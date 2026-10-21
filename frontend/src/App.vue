<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useSettingsStore } from '@/stores/settings'
import { useQueueStore } from '@/stores/queue'
import { useMusicStore } from '@/stores/music'
import { useConnectionStore } from '@/stores/connection'
import { useSenderStore } from '@/stores/sender'
import { useConsoleStore } from '@/stores/console'
import { sseBridge } from '@/api/sse'
import { http } from '@/api/http'

const route = useRoute()
const settingsStore = useSettingsStore()
const queueStore = useQueueStore()
const musicStore = useMusicStore()
const connectionStore = useConnectionStore()
const senderStore = useSenderStore()
const consoleStore = useConsoleStore()

// 后端进程可达性监视（sidebar-footer 状态点）：每 3 秒轮询 /sender_status
const backendOnline = ref(false)
let backendTimer: number | null = null

async function checkBackendStatus(): Promise<void> {
  try {
    const data = await http.get<any>('/sender_status')
    backendOnline.value = Boolean(data)
  } catch {
    backendOnline.value = false
  }
}

// AI 弹幕聊天状态监视（sidebar-footer）：每 10 秒轮询 /ai/status
const aiStatus = ref({ enabled: false, ok: false, checked: false, model: '' })
let aiTimer: number | null = null
const aiStatusText = computed(() => {
  if (!aiStatus.value.enabled) return 'AI 聊天：已关闭'
  if (aiStatus.value.ok) return `AI 已连接${aiStatus.value.model ? '（' + aiStatus.value.model + '）' : ''}`
  if (aiStatus.value.checked) return 'AI 未连接'
  return 'AI 状态检测中…'
})
const aiStatusClass = computed(() => {
  if (!aiStatus.value.enabled) return 'dot-off'
  if (aiStatus.value.ok) return 'dot-ok'
  return 'dot-warn'
})

async function checkAiStatus(): Promise<void> {
  try {
    const data = await http.get<any>('/ai/status')
    if (data) {
      aiStatus.value = {
        enabled: Boolean(data.enabled),
        ok: Boolean(data.ok),
        checked: Boolean(data.checked),
        model: (data.provider || data.model || '') as string,
      }
    }
  } catch {
    // 后端不可达时不更新，保留上次状态
  }
}

const isDisplay = computed(() => route.path.startsWith('/display/'))

const navItems = [
  { path: '/', label: '总览', icon: 'House' },
  { path: '/queue', label: '弹幕排队', icon: 'List' },
  { path: '/music', label: '点歌队列', icon: 'Headset' },
  { path: '/lyrics', label: '歌词样式', icon: 'Mic' },
  { path: '/sender', label: '自动刷屏', icon: 'Promotion' },
  { path: '/ai-chat', label: 'AI 聊天', icon: 'ChatDotRound' },
]

onMounted(async () => {
  // 后端连接状态监视：挂载后立即检查一次，并每 3 秒轮询
  checkBackendStatus()
  backendTimer = window.setInterval(checkBackendStatus, 3000)

  // AI 聊天状态监视：挂载后立即检查一次，并每 10 秒轮询 /ai/status
  checkAiStatus()
  aiTimer = window.setInterval(checkAiStatus, 10000)

  sseBridge.connect()
  settingsStore.listenSse()
  queueStore.listenSse()
  musicStore.listenSse()
  connectionStore.listenSse()
  senderStore.listenSse()
  consoleStore.listenSse()
  // 歌词 SSE：直连洛雪本地服务（localhost:23330/subscribe-player-status）
  musicStore.connectLyricsSse()

  await Promise.allSettled([
    settingsStore.load(),
    queueStore.refresh(),
    // 点歌队列独立 store：刷新后必须重新拉取，否则 musicStore.musicList 保持空，
    // 总览/点歌展示页刷新后点歌队列丢失（排队队列由 queueStore.refresh 恢复）
    musicStore.refreshList(),
    connectionStore.fetchStatus(),
    senderStore.loadCookies(),
    senderStore.loadConfig(),
    senderStore.fetchStatus(),
    // 实时弹幕持久化：页面加载时恢复当日弹幕记录
    consoleStore.loadLogs(),
  ])
})

onBeforeUnmount(() => {
  if (backendTimer !== null) {
    clearInterval(backendTimer)
    backendTimer = null
  }
  if (aiTimer !== null) {
    clearInterval(aiTimer)
    aiTimer = null
  }
})
</script>

<template>
  <div v-if="isDisplay" class="display-shell">
    <router-view />
  </div>
  <div v-else class="app-shell">
    <aside class="sidebar">
      <div class="logo">
        <div class="logo-badge">水</div>
        <div class="logo-text">
          <div class="logo-title">直播工具箱</div>
          <div class="logo-sub">shuitool v3</div>
        </div>
      </div>
      <nav class="nav">
        <router-link
          v-for="item in navItems"
          :key="item.path"
          :to="item.path"
          class="nav-item"
          :class="{ active: route.path === item.path }"
        >
          <el-icon><component :is="item.icon" /></el-icon>
          <span>{{ item.label }}</span>
        </router-link>
      </nav>
      <div class="sidebar-footer">
        <div class="status-row">
          <span class="dot" :class="backendOnline ? 'dot-ok' : 'dot-off'" />
          <span>{{ backendOnline ? '服务器已连接' : '未连接' }}</span>
        </div>
        <div class="status-row">
          <span class="dot" :class="aiStatusClass" />
          <span>{{ aiStatusText }}</span>
        </div>
        <div class="status-row dim">服务端：Flask 端口：5000</div>
      </div>
    </aside>
    <main class="content">
      <router-view />
    </main>
  </div>
</template>

<style scoped>
.app-shell {
  display: flex;
  height: 100vh;
  overflow: hidden;
}
.sidebar {
  width: 200px;
  flex-shrink: 0;
  background: rgba(18, 24, 40, 0.9);
  border-right: 1px solid var(--shui-border);
  display: flex;
  flex-direction: column;
  padding: 18px 12px;
}
.logo {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 6px 18px;
  border-bottom: 1px solid var(--shui-border);
}
.logo-badge {
  width: 38px;
  height: 38px;
  border-radius: 10px;
  background: linear-gradient(135deg, #4d8dff, #7a5cff);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  font-weight: 700;
  box-shadow: 0 0 16px var(--shui-accent-glow);
}
.logo-title { font-size: 15px; font-weight: 600; }
.logo-sub { font-size: 11px; color: var(--shui-text-dim); }
.nav {
  flex: 1;
  padding-top: 14px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border-radius: 8px;
  color: var(--shui-text-dim);
  text-decoration: none;
  font-size: 14px;
  transition: all 0.2s;
}
.nav-item:hover { background: rgba(77, 141, 255, 0.12); color: var(--shui-text); }
.nav-item.active {
  background: linear-gradient(90deg, rgba(77, 141, 255, 0.22), rgba(77, 141, 255, 0.05));
  color: #fff;
  box-shadow: inset 2px 0 0 var(--shui-accent);
}
.sidebar-footer {
  padding: 12px 8px 4px;
  border-top: 1px solid var(--shui-border);
  font-size: 12px;
  color: var(--shui-text-dim);
}
.status-row { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; }
.status-row.dim { opacity: 0.6; }
.dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.dot-ok { background: #22c55e; box-shadow: 0 0 8px #22c55e; }
.dot-off { background: #ef4444; box-shadow: 0 0 8px #ef4444; }
.dot-warn { background: #f59e0b; box-shadow: 0 0 8px #f59e0b; }
.content {
  flex: 1;
  overflow-y: auto;
  padding: 24px 28px;
}
/* 展示页宿主：撑满视口、透明、隐藏溢出；position:relative 作为歌词页 .lyrics-wrap 绝对定位基准 */
.display-shell { height: 100vh; width: 100vw; overflow: hidden; background: transparent; position: relative; }
</style>
