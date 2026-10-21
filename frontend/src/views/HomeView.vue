<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted, type CSSProperties } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useConnectionStore } from '@/stores/connection'
import { useQueueStore } from '@/stores/queue'
import { useMusicStore } from '@/stores/music'
import { useSenderStore } from '@/stores/sender'
import { useAiStore } from '@/stores/ai'
import { useSettingsStore } from '@/stores/settings'
import { useConsoleStore } from '@/stores/console'
import { formatSongTitle } from '@/utils/songTitle'
import { entryName, entrySinger, entryStatus, type SongEntry } from '@/types'
import FontSelect from '@/components/FontSelect.vue'
import OpacitySlider from '@/components/OpacitySlider.vue'

const router = useRouter()
const connectionStore = useConnectionStore()
const queueStore = useQueueStore()
const musicStore = useMusicStore()
const senderStore = useSenderStore()
const aiStore = useAiStore()
const settingsStore = useSettingsStore()
const consoleStore = useConsoleStore()

// 直播码：加载设置后回填为默认值，输入时防抖实时保存到 gallery.livecode，
// 避免每次打开页面重输；提交 /connect_services 仍用同一 livecode 字段。
// immediate 保证 SPA 路由切回本页（组件重建、loaded 已为 true 且不再变化）
// 时也会立即回填，修复"直播码已保存但重开页面未自动填充"
const livecode = ref('')
// 直播码掩码显示：未聚焦时前3后3明文、中间星号；聚焦/输入时明文
const livecodeFocused = ref(false)
const livecodeDisplay = computed(() => {
  const v = livecode.value
  if (livecodeFocused.value || !v) return v
  if (v.length <= 6) return '*'.repeat(v.length)
  return v.slice(0, 3) + '*'.repeat(v.length - 6) + v.slice(-3)
})
function onLivecodeInput(v: string | number) {
  livecode.value = String(v)
}
watch(
  () => settingsStore.loaded,
  (ok) => {
    if (ok && !livecode.value.trim()) {
      livecode.value = settingsStore.gallery.livecode || ''
    }
  },
  { immediate: true },
)
watch(livecode, (v) => {
  settingsStore.gallery.livecode = v
  settingsStore.scheduleAutoSave('gallery')
})
const openliveOpen = ref(false)
const queueInput = ref('')
const musicInput = ref('')

// 连接卡"自动答谢"开关与自动刷屏页共用同一 senderStore.config.thanksEnabled：
// 页面加载时若 config 未加载则补拉一次（正常由 App.vue 启动时全局加载）
// AI 聊天总开关：同样补拉一次 ai_config，避免未加载状态直接写库覆盖已有配置
onMounted(async () => {
  if (!senderStore.configLoaded) await senderStore.loadConfig()
  if (!aiStore.loaded) await aiStore.loadConfig()
})

// 实时弹幕：logs 数组按时间正序追加（新弹幕在末尾），展示全部日志
// （store 内部上限 300 条），由 .mini-obs 滚动容器承载；
// 新弹幕追加后自动滚动到底部，保证最新弹幕可见
const danmuBox = ref<HTMLElement | null>(null)
const danmuPreview = computed(() => consoleStore.logs)
watch(
  () => consoleStore.logs.length,
  async () => {
    await nextTick()
    if (danmuBox.value) danmuBox.value.scrollTop = danmuBox.value.scrollHeight
  },
)

async function handleConnect() {
  if (!livecode.value.trim()) {
    ElMessage.warning('请输入直播码')
    return
  }
  const res = await connectionStore.connect(livecode.value.trim())
  if (res.ok) ElMessage.success(res.message)
  else ElMessage.error(res.message)
}

async function handleDisconnect() {
  const res = await connectionStore.disconnect()
  if (res.ok) ElMessage.success(res.message)
  else ElMessage.error(res.message)
}

async function handleSaveOpenlive() {
  const ok = await settingsStore.saveSection('gallery')
  if (ok) {
    ElMessage.success('openlive 密钥已保存')
    openliveOpen.value = false
  } else {
    ElMessage.error('保存失败')
  }
}

// 清空实时弹幕记录（当日）：后端删除当日日志 + 前端清空列表，一键同步
async function handleClearDanmu() {
  const ok = await consoleStore.clearLogs()
  if (ok) ElMessage.success('弹幕记录已清空')
  else ElMessage.error('清空失败')
}

async function toggleQueue(enable: boolean) {
  const ok = enable ? await queueStore.openQueue() : await queueStore.closeQueue()
  if (ok) ElMessage.success(enable ? '排队已开启' : '排队已关闭')
  else ElMessage.error('操作失败')
}

async function toggleMusic(enable: boolean) {
  const ok = enable ? await musicStore.openMusic() : await musicStore.closeMusic()
  if (ok) ElMessage.success(enable ? '点歌已开启' : '点歌已关闭')
  else ElMessage.error('操作失败')
}

async function addQueue() {
  const name = queueInput.value.trim()
  if (!name) {
    ElMessage.warning('请输入昵称 / 弹幕')
    return
  }
  const ok = await queueStore.updateQueue([...queueStore.queueList, name])
  if (ok) {
    queueInput.value = ''
    ElMessage.success('已添加')
  } else {
    ElMessage.error('添加失败')
  }
}

async function removeQueue(index: number) {
  const next = queueStore.queueList.filter((_: string, i: number) => i !== index)
  const ok = await queueStore.updateQueue(next)
  if (!ok) ElMessage.error('移除失败')
}

async function addMusic() {
  const name = musicInput.value.trim()
  if (!name) {
    ElMessage.warning('请输入歌曲名')
    return
  }
  // 手动添加：复用后端 add_music_list 状态机
  // （自动去重/上限10/空队列立即播放/非空 playLater/清 removed_keys）
  const ok = await musicStore.addMusic(name)
  if (ok) {
    musicInput.value = ''
    ElMessage.success('已添加')
  } else {
    ElMessage.error('添加失败')
  }
}

async function removeMusic(index: number) {
  // 记录被移除项：若为正在播放项，移除后洛雪侧仍会继续播放（playLater 预排队
  // 无法从洛雪队列删除该歌），按合理语义提示用户，而不是"点了没反应"
  const target = musicStore.musicList[index]
  const next = musicStore.musicList.filter((_, i) => i !== index)
  const ok = await musicStore.updateMusicList(next)
  if (!ok) ElMessage.error('移除失败')
  else if (target?.status === 'playing') {
    ElMessage.info('已从队列移除；该曲目正在播放，如需立即停止请点「切歌」')
  }
}

// 排队卡片右上：清空队列（与原 QueueView 清空队列按钮同逻辑 /clear-queue）
async function handleClearQueue() {
  const ok = await queueStore.clearQueue()
  if (ok) ElMessage.success('队列已清空')
  else ElMessage.error('清空失败')
}

// 点歌卡片右上：切歌 / 清空待播 / 拉取洛雪当前歌曲（对应 MusicView 原按钮逻辑）
async function handleNextMusic() {
  const ok = await musicStore.next()
  if (ok) ElMessage.success('已切歌')
  else ElMessage.error('切歌失败')
}

async function handleClearMusic() {
  const ok = await musicStore.updateMusicList([])
  if (ok) ElMessage.success('待播已清空')
  else ElMessage.error('清空失败')
}

async function handleCatchPlaying() {
  const ok = await musicStore.catchPlaying()
  if (ok) {
    ElMessage.success('已同步当前播放歌曲')
    // 拉取洛雪成功后立即重新拉取点歌队列，使新歌曲立即显示
    // （后端 /catch_playing 虽会 SSE 推送 music_changed，但不依赖 SSE 也能立即刷新，
    //   避免 SSE 断开/未连接时队列不更新）
    await musicStore.refreshList()
  } else ElMessage.error('同步失败，请确认洛雪正在播放')
}

// 原生 HTML5 拖拽排序（排队 / 点歌共用）
const dragSource = ref<{ kind: 'queue' | 'music'; index: number } | null>(null)

function onDragStart(kind: 'queue' | 'music', index: number) {
  dragSource.value = { kind, index }
}

function onDragOver(e: DragEvent) {
  e.preventDefault()
}

function onDragEnd() {
  dragSource.value = null
}

async function onDrop(kind: 'queue' | 'music', target: number) {
  const src = dragSource.value
  dragSource.value = null
  if (!src || src.kind !== kind || src.index === target) return
  // 方案 B：queue 为 string[]、music 为 SongEntry[]，分开处理避免联合类型推断报错
  let ok: boolean
  if (kind === 'queue') {
    const list: string[] = [...queueStore.queueList]
    const [item] = list.splice(src.index, 1)
    list.splice(target, 0, item)
    ok = await queueStore.updateQueue(list)
  } else {
    const list: SongEntry[] = [...musicStore.musicList]
    const [item] = list.splice(src.index, 1)
    list.splice(target, 0, item)
    ok = await musicStore.updateMusicList(list)
  }
  if (!ok) ElMessage.error('排序保存失败')
}

const lyricTitleFull = computed(() => musicStore.playing?.name || '当前歌曲')
// 展示层精简末尾括号（如 "我们（电影主题曲）" 显示为 "我们"），title 悬浮显示完整原名
const lyricTitle = computed(() => formatSongTitle(lyricTitleFull.value))
const lyricSinger = computed(() => musicStore.playing?.singer || '')
const lyricLine = computed(() => musicStore.lyricLine || '等待歌词推送…')
// 多行歌词窗口：以当前播放行为中心截取 lyricLines 片段（含行偏移），供总览页滚动式展示
const lyricWindow = computed(() => {
  const lines = musicStore.lyricLines
  if (!lines.length) return []
  const idx = musicStore.lyricIndex
  const start = Math.max(0, idx - 1)
  const end = Math.min(lines.length, start + 5)
  return lines.slice(start, end).map((l, i) => ({ time: l.time, text: l.text, offset: start + i }))
})

// 实时歌词样式设置（写入 settings.lyrics，与歌词样式页共享字段，防抖实时保存）
const l = settingsStore.lyrics
const lyricsStyleOpen = ref(false)
watch(l, () => settingsStore.scheduleAutoSave('lyrics'), { deep: true })

const homeTitleStyle = computed<CSSProperties>(() => ({
  fontFamily: l.lyricstitleFontFamily,
  fontSize: l.lyricstitleFontSize + 'px',
  color: l.lyricstitleFontColor,
}))
const homeArtistStyle = computed<CSSProperties>(() => ({
  fontFamily: l.lyricsartistFontFamily,
  fontSize: l.lyricsartistFontSize + 'px',
  color: l.lyricsartistFontColor,
}))
const homeLineStyle = computed<CSSProperties>(() => ({
  fontFamily: l.lyricsFontFamily,
  fontSize: l.lyricsFontSize + 'px',
  color: l.lyricsFontColor,
}))
const homeLineGap = computed(() => (Number(l.lineSpacing) || 0) + 'px')
</script>

<template>
  <div class="home-page">
    <!-- 直播间连接条 -->
    <div class="shui-card conn-card">
      <div class="conn-bar">
        <div class="conn-left">
          <span class="shui-card-title conn-title">直播码</span>
          <el-input :model-value="livecodeDisplay" placeholder="直播码" class="conn-input" @update:model-value="onLivecodeInput" @focus="livecodeFocused = true" @blur="livecodeFocused = false" @keyup.enter="handleConnect" />
          <el-button v-if="!connectionStore.status.connected" type="primary" :loading="connectionStore.connecting" :disabled="connectionStore.cooldown > 0" @click="handleConnect">{{ connectionStore.cooldown > 0 ? `等待收尾 ${connectionStore.cooldown}s` : '连接' }}</el-button>
          <el-button v-else type="danger" plain :loading="connectionStore.connecting" :disabled="connectionStore.cooldown > 0" @click="handleDisconnect">断开</el-button>
        </div>
        <div class="conn-left chips">
          <span class="shui-chip"><span class="dim">弹幕</span><span class="v" :class="connectionStore.status.connected ? 'ok' : 'off'">● {{ connectionStore.status.connected ? '已连接' : '未连接' }}</span></span>
          <span class="shui-chip"><span class="dim">洛雪</span><span class="v" :class="musicStore.lxConnected ? 'ok' : 'off'">● {{ musicStore.lxConnected ? '已连接' : '未连接' }}</span></span>
          <span class="shui-chip"><span class="dim">排队</span><el-switch size="small" :model-value="queueStore.queueEnabled" @change="toggleQueue" /></span>
          <span class="shui-chip"><span class="dim">点歌</span><el-switch size="small" :model-value="musicStore.musicEnabled" @change="toggleMusic" /></span>
          <span class="shui-chip"><span class="dim">排队人数</span><span class="v" :class="queueStore.queueList.length > 0 ? 'ok' : 'zero'">{{ queueStore.queueList.length }}</span></span>
          <span class="shui-chip"><span class="dim">待播歌曲</span><span class="v" :class="musicStore.musicList.length > 0 ? 'ok' : 'zero'">{{ musicStore.musicList.length }}</span></span>
          <span class="shui-chip"><span class="dim">自动刷屏</span><span class="v" :class="senderStore.running ? 'ok' : 'off'">{{ senderStore.running ? '执行中' : '已停止' }}</span></span>
          <span class="shui-chip"><span class="dim">自动答谢</span><el-switch size="small" v-model="senderStore.config.thanksEnabled" /></span>
          <span class="shui-chip"><span class="dim">自动回复</span><el-switch size="small" v-model="senderStore.config.replyEnabled" /></span>
          <span class="shui-chip"><span class="dim">AI 聊天</span><el-switch size="small" v-model="aiStore.config.enabled" /></span>
        </div>
        <el-button class="conn-openlive" @click="openliveOpen = !openliveOpen">⚙ openlive 设置</el-button>
      </div>
    </div>

    <!-- openlive 设置（默认隐藏，点击滑出） -->
    <el-collapse-transition>
      <div v-show="openliveOpen" class="toggle-wrap">
        <div class="shui-card openlive-card">
          <div class="shui-card-title">B站 openlive 设置</div>
          <div class="shui-form-row">
            <span class="shui-form-label">AppID</span>
            <el-input v-model="settingsStore.gallery.openlive.appId" placeholder="开放平台应用 AppID" style="width: 260px" />
          </div>
          <div class="shui-form-row">
            <span class="shui-form-label">AccessKey ID</span>
            <el-input v-model="settingsStore.gallery.openlive.accessKeyId" type="password" show-password placeholder="AKID..." style="width: 320px" />
          </div>
          <div class="shui-form-row">
            <span class="shui-form-label">AccessKey Secret</span>
            <el-input v-model="settingsStore.gallery.openlive.accessKeySecret" type="password" show-password placeholder="SECRET..." style="width: 320px" />
          </div>
          <div class="shui-form-row">
            <span class="shui-form-label" />
            <el-button type="primary" :loading="settingsStore.saving" @click="handleSaveOpenlive">保存密钥</el-button>
            <span class="dim">保存后自动收起，仅存本地 settings.json</span>
          </div>
        </div>
      </div>
    </el-collapse-transition>

    <!-- 2x2 展示网格：整体填满剩余高度 -->
    <div class="home-grid">
      <!-- 排队队列 -->
      <div class="shui-card home-card">
        <div class="card-head">
          <div class="shui-card-title">排队队列 <span class="drag-hint">☰ 拖动可排序</span></div>
          <el-button size="small" type="danger" plain @click="handleClearQueue">清空队列</el-button>
        </div>
        <div class="fill">
          <div class="queue-scroll">
            <table class="list">
              <thead>
                <tr><th style="width: 32px;" /><th>排队对象</th><th style="text-align: right; width: 88px;">操作</th></tr>
              </thead>
              <tbody>
                <tr v-if="!queueStore.queueList.length">
                  <td colspan="3"><span class="dim">等待观众排队…</span></td>
                </tr>
                <tr
                  v-for="(name, index) in queueStore.queueList"
                  :key="name + index"
                  draggable="true"
                  @dragstart="onDragStart('queue', index)"
                  @dragover="onDragOver"
                  @drop="onDrop('queue', index)"
                  @dragend="onDragEnd"
                >
                  <td><span class="shui-grip">☰</span></td>
                  <td>{{ name }}</td>
                  <td>
                    <div class="ops">
                      <el-button size="small" type="danger" plain @click="removeQueue(index)">移除</el-button>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
        <div class="row foot-row">
          <span class="shui-form-label">手动添加</span>
          <el-input v-model="queueInput" placeholder="输入昵称 / 弹幕" class="foot-input" @keyup.enter="addQueue" />
          <el-button type="primary" size="small" @click="addQueue">添加</el-button>
          <el-button size="small" class="foot-go" @click="router.push('/queue')">设置 →</el-button>
        </div>
        <div class="obs-import">
          <span class="oi-badge">OBS</span>
          <span class="oi-text">添加「浏览器源」，URL 填</span>
          <code class="oi-url">http://127.0.0.1:5000/#/display/queue</code>
          <span class="oi-sub">主机端口按实际运行地址填写</span>
        </div>
      </div>

      <!-- 点歌队列 -->
      <div class="shui-card home-card">
        <div class="card-head">
          <div class="shui-card-title">点歌队列 <span class="drag-hint">☰ 拖动可排序</span></div>
          <div class="card-ops">
            <el-button size="small" type="primary" plain @click="handleNextMusic">切歌</el-button>
            <el-button size="small" type="danger" plain @click="handleClearMusic">清空待播</el-button>
            <el-button size="small" @click="handleCatchPlaying">拉取洛雪</el-button>
          </div>
        </div>
        <div class="fill">
          <div class="queue-scroll">
            <table class="list">
              <thead>
                <tr><th style="width: 32px;" /><th>待播歌曲</th><th style="text-align: right; width: 88px;">操作</th></tr>
              </thead>
              <tbody>
                <tr v-if="!musicStore.musicList.length">
                  <td colspan="3"><span class="dim">暂无待播歌曲</span></td>
                </tr>
                <tr
                  v-for="(song, index) in musicStore.musicList"
                  :key="entryName(song) + index"
                  draggable="true"
                  @dragstart="onDragStart('music', index)"
                  @dragover="onDragOver"
                  @drop="onDrop('music', index)"
                  @dragend="onDragEnd"
                >
                  <td><span class="shui-grip">☰</span></td>
                  <td>
                    <span :title="entryName(song)">{{ formatSongTitle(song) }}</span>
                    <span v-if="entrySinger(song) && entryStatus(song) !== 'playing'" class="song-singer-cell">- {{ entrySinger(song) }}</span>
                  </td>
                  <td>
                    <div class="ops">
                      <el-button size="small" type="danger" plain @click="removeMusic(index)">移除</el-button>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
        <div class="row foot-row">
          <span class="shui-form-label">手动添加</span>
          <el-input v-model="musicInput" placeholder="输入歌曲名，如：晴天 - 周杰伦" class="foot-input" @keyup.enter="addMusic" />
          <el-button type="primary" size="small" @click="addMusic">添加</el-button>
          <span class="dim now-playing" :title="lyricTitleFull">当前播放：{{ lyricTitle }}</span>
          <el-button size="small" class="foot-go" @click="router.push('/music')">设置 →</el-button>
        </div>
        <div class="obs-import">
          <span class="oi-badge">OBS</span>
          <span class="oi-text">添加「浏览器源」，URL 填</span>
          <code class="oi-url">http://127.0.0.1:5000/#/display/music</code>
          <span class="oi-sub">主机端口按实际运行地址填写</span>
        </div>
      </div>

      <!-- 实时弹幕 -->
      <div class="shui-card home-card danmu-card">
        <div class="card-head">
          <div class="shui-card-title">实时弹幕</div>
          <el-button size="small" type="danger" plain @click="handleClearDanmu">清空记录</el-button>
        </div>
        <div class="fill">
          <div class="mini-obs danmu-box" ref="danmuBox">
            <div v-if="!danmuPreview.length" class="dim">等待弹幕…</div>
            <div v-for="(item, index) in danmuPreview" :key="index" class="mini-line">
              <span class="dm-time">{{ item.time }}</span>
              <span class="dm-user">{{ item.username }}:</span>
              <span class="dm-text">{{ item.danmu }}</span>
            </div>
          </div>
        </div>
        <div class="obs-hint">
          <span class="src">实时推送 · 填满剩余高度</span>
          <el-button size="small" @click="router.push('/sender')">刷弹幕 →</el-button>
        </div>
      </div>

      <!-- 实时歌词 -->
      <div class="shui-card home-card">
        <div class="card-head">
          <div class="shui-card-title">实时歌词</div>
          <el-button size="small" @click="lyricsStyleOpen = !lyricsStyleOpen">样式设置</el-button>
        </div>
        <el-collapse-transition>
          <div v-show="lyricsStyleOpen" class="ly-style-panel">
            <FontSelect v-model="l.lyricstitleFontFamily" label="歌名字体" />
            <OpacitySlider v-model="l.lyricstitleFontSize" label="歌名字号" :min="12" :max="80" />
            <FontSelect v-model="l.lyricsartistFontFamily" label="歌手字体" />
            <OpacitySlider v-model="l.lyricsartistFontSize" label="歌手字号" :min="12" :max="60" />
            <FontSelect v-model="l.lyricsFontFamily" label="歌词字体" />
            <OpacitySlider v-model="l.lyricsFontSize" label="歌词字号" :min="12" :max="100" />
            <OpacitySlider v-model="l.lineSpacing" label="歌词行间距" :min="0" :max="60" />
            <span class="dim ly-style-tip">实时保存 · 与「歌词样式」页共享同一组设置</span>
          </div>
        </el-collapse-transition>
        <div class="fill">
          <div class="mini-obs ly-mini">
            <div class="ly-title" :style="homeTitleStyle">{{ lyricTitle }}</div>
            <div class="ly-singer" :style="homeArtistStyle">{{ lyricSinger }}</div>
            <div class="ly-multi" :style="{ gap: homeLineGap }">
              <div v-if="!lyricWindow.length" class="ly-line ly-fallback" :style="homeLineStyle">{{ lyricLine }}</div>
              <div
                v-for="l in lyricWindow"
                :key="l.time + '-' + l.text"
                class="ly-line"
                :class="{ 'ly-active': l.offset === musicStore.lyricIndex }"
                :style="homeLineStyle"
              >{{ l.text }}</div>
            </div>
          </div>
        </div>
        <div class="obs-import">
          <span class="oi-badge">OBS</span>
          <span class="oi-text">添加「浏览器源」，URL 填</span>
          <code class="oi-url">http://127.0.0.1:5000/#/display/lyrics</code>
          <span class="oi-sub">主机端口按实际运行地址填写</span>
        </div>
        <div class="obs-hint">
          <span class="src">实时推送 · 逐句歌词</span>
          <el-button size="small" @click="router.push('/lyrics')">歌词样式 →</el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.home-page {
  display: flex;
  flex-direction: column;
  /* 必须用 height 锁定（而非只设 min-height）：flex column 容器高度为
     auto 时，flex:1 子项会按内容撑高——弹幕全量展示后 .home-grid 被连带
     撑高，grid 轨道（即使 minmax(0,1fr)）随容器一起变高，弹幕卡片整体
     拉伸、overflow-y:auto 失效。锁定后高度由视口决定，.home-grid 拿到
     确定高度，弹幕卡片固定、超量弹幕走内部滚动条 */
  height: calc(100vh - 48px);
  min-height: calc(100vh - 48px);
}
.conn-card { flex-shrink: 0; margin-bottom: 14px; }
.conn-bar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.conn-left { display: flex; align-items: center; gap: 12px; }
.conn-left.chips { flex-wrap: wrap; row-gap: 8px; }
.conn-title { margin-bottom: 0; }
.conn-input { width: 150px; }
.conn-openlive { margin-left: auto; }
.toggle-wrap { margin-bottom: 14px; flex-shrink: 0; }
.openlive-card { margin-top: 0; }
.home-grid {
  flex: 1;
  display: grid;
  /* 1fr 实际是 minmax(auto, 1fr)：轨道最小尺寸带 auto 下限，
     会被 grid item 的 min-content 贡献撑大（弹幕全量展示后内容极高，
     会把整行轨道连同弹幕卡片一起撑高，overflow-y:auto 因此失效）。
     改用 minmax(0, 1fr) 取消 auto 下限，轨道高度固定，
     由 .mini-obs 内部滚动承载超量弹幕 */
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  grid-template-rows: minmax(0, 1fr) minmax(0, 1fr);
  gap: 14px;
  min-height: 0;
}
@media (max-width: 1100px) {
  /* 竖屏单列：连接条高度不变，剩余高度四卡均分（1fr 1fr 1fr 1fr）。
     minmax(0,..) 取消轨道 auto 下限，内容超高时由卡内滚动承载、不撑高页面；
     四卡填满剩余空间，整页一屏展示 */
  .home-grid {
    grid-template-columns: 1fr;
    grid-template-rows: minmax(0, 1fr) minmax(0, 1fr) minmax(0, 1fr) minmax(0, 1fr);
  }
}
.home-card {
  margin-top: 0;
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}
.home-card .shui-card-title { flex-shrink: 0; }
.card-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-shrink: 0; }
.card-head .shui-card-title { margin-bottom: 0; }
.card-ops { display: flex; gap: 6px; flex-shrink: 0; }
.home-card .fill { flex: 1; min-height: 0; display: flex; flex-direction: column; overflow: hidden; }
.drag-hint { font-size: 11px; color: var(--shui-text-dim); margin-left: 4px; font-weight: 400; }
.list { width: 100%; border-collapse: collapse; font-size: 13px; }
.list th { text-align: left; color: var(--shui-text-dim); font-weight: 500; padding: 6px 10px; border-bottom: 1px solid var(--shui-border); }
.list td { padding: 7px 10px; border-bottom: 1px solid rgba(38, 49, 74, 0.5); font-size: 20px; }
.list tr:last-child td { border-bottom: none; }
.ops { display: flex; gap: 6px; justify-content: flex-end; }
.song-singer-cell { opacity: 0.72; font-size: 14px; margin-left: 2px; }
/* 排队/点歌卡片滚动容器：与 .danmu-box 同范式（flex:1 1 0 + min-height:0 +
   box-sizing），超高时内部滚动；滚动条样式与弹幕容器一致（6px 暗轨蓝滑块） */
.queue-scroll {
  flex: 1 1 0;
  min-height: 0;
  box-sizing: border-box;
  overflow-y: auto;
  overflow-x: hidden;
}
.queue-scroll::-webkit-scrollbar {
  width: 6px;
}
.queue-scroll::-webkit-scrollbar-track {
  background: rgba(13, 18, 32, 0.6);
  border-radius: 3px;
}
.queue-scroll::-webkit-scrollbar-thumb {
  background: linear-gradient(180deg, #2b5bbf, #4d8dff);
  border-radius: 3px;
}
.queue-scroll::-webkit-scrollbar-thumb:hover {
  background: linear-gradient(180deg, #4d8dff, #7fb0ff);
}
.queue-scroll::-webkit-scrollbar-corner {
  background: transparent;
}
/* 表头吸顶：滚动时表头固定可见。border-collapse:collapse 下 sticky 在部分
   WebKit 失效，改 separate 无间距布局；th 背景用卡片底色实色近似
   （rgba(26,34,52,.82) 半透明会透出滚动内容） */
.queue-scroll .list {
  border-collapse: separate;
  border-spacing: 0;
}
.queue-scroll .list thead th {
  position: sticky;
  top: 0;
  background: #1a2234;
  z-index: 1;
}
.row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.foot-row { margin: 12px 0 0; flex-shrink: 0; }
.foot-input { width: 170px; }
.foot-go { margin-left: auto; }
.now-playing { max-width: 60%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mini-obs {
  flex: 1;
  min-height: 0;
  border-radius: 10px;
  padding: 12px 14px;
  background: #0d1220;
  border: 1px solid var(--shui-border);
  display: flex;
  flex-direction: column;
  gap: 5px;
  font-size: 15px;
  overflow-y: auto;
  overflow-x: hidden;
}
/* 实时弹幕容器字体加大到 20px（歌词模块 ly-mini 保持 15px 基线，不连带放大） */
.mini-obs:not(.ly-mini) { font-size: 20px; }

/* 弹幕容器显式固定高度组合：flex:1 吃满剩余高度 + height:0 取消内容贡献下限 +
   min-height:0，即使父级高度链断裂也不会被内容撑大；超量弹幕由 overflow-y:auto
   必然产生滚动条。display:block 让弹幕行按块级流式排列（不参与 flex 压缩），
   box-sizing:border-box 保证 padding 不参与高度外扩 */
.danmu-box {
  flex: 1 1 0;
  height: 0;
  min-height: 0;
  box-sizing: border-box;
  display: block;
  overflow-y: auto;
  overflow-x: hidden;
}

/* 弹幕容器自定义滚动条：细条 6px、圆角、暗色轨道 + 蓝色渐变滑块，hover 变亮；
   选择器限定 .danmu-box 内，不污染全局滚动条 */
.danmu-box::-webkit-scrollbar {
  width: 6px;
}
.danmu-box::-webkit-scrollbar-track {
  background: rgba(13, 18, 32, 0.6);
  border-radius: 3px;
}
.danmu-box::-webkit-scrollbar-thumb {
  background: linear-gradient(180deg, #2b5bbf, #4d8dff);
  border-radius: 3px;
}
.danmu-box::-webkit-scrollbar-thumb:hover {
  background: linear-gradient(180deg, #4d8dff, #7fb0ff);
}
.danmu-box::-webkit-scrollbar-corner {
  background: transparent;
}

/* 每条弹幕独占一行：块级流式排列、正常换行（不再 nowrap+ellipsis 截断）、
   line-height 保证行距清晰不重叠 */
.mini-line {
  display: block;
  line-height: 1.5;
  padding: 2px 0;
  white-space: normal;
  word-break: break-all;
  opacity: 0.92;
}

.dm-time { color: var(--shui-text-dim); font-size: 12px; margin-right: 8px; font-family: Consolas, "Courier New", monospace; }
.dm-user { color: #4d8dff; margin-right: 6px; }
.dm-text { color: var(--shui-text); word-break: break-all; }
.ly-mini { align-items: center; justify-content: center; text-align: center; }
.ly-title { font-weight: 700; font-size: 20px; letter-spacing: 1px; white-space: nowrap; }
.ly-singer { font-size: 13px; color: #ffaaff; white-space: nowrap; }
.ly-multi {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
  width: 100%;
  overflow: hidden;
}
.ly-multi .ly-line {
  letter-spacing: 1px;
  white-space: nowrap;
  opacity: 0.45;
  transition: opacity 0.35s ease, font-weight 0.35s ease;
}
.ly-multi .ly-line.ly-fallback { opacity: 0.9; }
.ly-multi .ly-line.ly-active { opacity: 1; font-weight: 700; }
.ly-style-panel {
  flex-shrink: 0;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 16px;
  margin-top: 10px;
  padding: 10px 12px;
  background: #0d1220;
  border: 1px solid var(--shui-border);
  border-radius: 10px;
}
.ly-style-tip { width: 100%; }
.obs-hint { display: flex; align-items: center; justify-content: space-between; margin-top: 10px; gap: 8px; flex-shrink: 0; }
.obs-hint .src {
  font-family: Consolas, monospace;
  font-size: 11px;
  color: #7fb0ff;
  background: rgba(77, 141, 255, 0.1);
  border-radius: 5px;
  padding: 2px 8px;
}
.obs-import {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed rgba(38, 49, 74, 0.65);
  flex-shrink: 0;
  font-size: 12px;
  color: var(--shui-text-dim);
}
.oi-badge {
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.5px;
  color: #7fb0ff;
  background: rgba(77, 141, 255, 0.12);
  border: 1px solid rgba(77, 141, 255, 0.35);
  border-radius: 4px;
  padding: 1px 5px;
}
.oi-text { color: var(--shui-text-dim); }
.oi-url {
  font-family: Consolas, monospace;
  font-size: 11px;
  color: #7fb0ff;
  background: rgba(77, 141, 255, 0.1);
  border-radius: 5px;
  padding: 2px 8px;
}
.oi-sub { font-size: 11px; color: rgba(139, 153, 182, 0.8); }
.dim { color: var(--shui-text-dim); font-size: 12px; }
</style>
