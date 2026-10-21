<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useMusicStore } from '@/stores/music'
import { useSettingsStore } from '@/stores/settings'
import { alphaStyle } from '@/utils/style'
import { formatSongTitle } from '@/utils/songTitle'
import { entryName, type SongEntry } from '@/types'
import FontSelect from '@/components/FontSelect.vue'
import ColorField from '@/components/ColorField.vue'
import OpacitySlider from '@/components/OpacitySlider.vue'

const musicStore = useMusicStore()
const settingsStore = useSettingsStore()
const m = settingsStore.music

const musicInput = ref('')

// 实时保存：监听全部设置字段变化，防抖 400ms 后整组提交（saveSection + 后端点号键展开链路不变）
watch(m, () => settingsStore.scheduleAutoSave('music'), { deep: true })

async function toggleMusic(enable: boolean) {
  const ok = enable ? await musicStore.openMusic() : await musicStore.closeMusic()
  if (ok) ElMessage.success(enable ? '点歌已开启' : '点歌已关闭')
  else ElMessage.error('操作失败')
}

async function handleAdd() {
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

async function handleReset() {
  try {
    await ElMessageBox.confirm('确定恢复点歌设置的默认值？', '恢复默认', { type: 'warning', confirmButtonText: '恢复默认', cancelButtonText: '取消' })
  } catch {
    return
  }
  const ok = await settingsStore.resetSection('music')
  if (ok) ElMessage.success('已恢复默认并保存')
  else ElMessage.error('操作失败')
}

// 预览样式必须用 computed：依赖 m.* 字段，任一字段变化即重渲染（普通对象是 setup 一次性快照，改设置不刷新）
const previewTitleStyle = computed(() => ({
  fontFamily: m.musicTitleFontFamily,
  fontSize: Math.max(12, Math.round(Number(m.musicTitleFontSize) / 2)) + 'px',
  color: m.musicTitleFontColor,
}))
// 预览根容器背景 = 整体背景（与 MusicDisplay 根容器一致）
const previewRootStyle = computed(() => ({
  background: alphaStyle(m.musicBackgroundColor, m.musicBackgroundOpacity),
}))
// 预览列表容器：仅字体样式，背景透明（整体背景由 previewRootStyle 控制，项背景由每项 previewItemStyle 控制）
const previewListStyle = computed(() => ({
  fontFamily: m.musicFontFamily,
  fontSize: Math.max(11, Math.round(Number(m.musicFontSize) / 2)) + 'px',
  color: m.musicFontColor,
}))
// 每个队列项元素背景（队列项背景：开关 + 项背景颜色 + 项背景透明度，与 MusicDisplay.itemStyle 一致）
const previewItemStyle = computed(() => ({
  background: m.musicBackgroundColorCheckbox
    ? alphaStyle(m.musicQueueBackgroundColor, m.musicQueueOpacity)
    : 'transparent',
  borderRadius: '8px',
  padding: '3px 10px',
}))
</script>

<template>
  <div class="page-root">
    <div class="shui-page-head">
      <div>
        <h2>点歌队列</h2>
        <div class="sub">弹幕点歌 · 洛雪音乐联动 · OBS 待播展示</div>
      </div>
      <div class="shui-page-actions">
        <span class="shui-chip"><span class="dim">点歌开关</span><el-switch size="small" :model-value="musicStore.musicEnabled" @change="toggleMusic" /></span>
        <el-button size="small" @click="handleReset">恢复默认</el-button>
      </div>
    </div>

    <!-- 功能设置 -->
    <div class="shui-card func-card">
      <div class="shui-card-title">功能设置</div>
      <div class="shui-form-row">
        <span class="shui-form-label">点歌命令</span>
        <el-input v-model="m.musicCommands" placeholder="如：点歌" style="width: 280px" />
        <span class="dim">弹幕「点歌 + 歌名」入队</span>
      </div>
      <div class="shui-form-row">
        <span class="shui-form-label">切歌命令</span>
        <el-input v-model="m.skipCommands" placeholder="如：切歌" style="width: 280px" />
        <span class="dim">仅房管 / 主播可用</span>
      </div>
      <div class="shui-form-row last">
        <span class="shui-form-label">手动添加</span>
        <el-input v-model="musicInput" placeholder="输入歌曲名，如：晴天 - 周杰伦" style="width: 240px" @keyup.enter="handleAdd" />
        <el-button type="primary" size="small" @click="handleAdd">添加</el-button>
      </div>
      <div class="shui-hint">弹幕发送「点歌命令 + 歌曲名」会加入待播列表；切歌命令跳过当前播放的歌曲。洛雪需在本地运行（127.0.0.1:23330）。</div>
    </div>

    <!-- 左右两列等高 -->
    <div class="shui-col-2">
      <div class="shui-col">
        <div class="shui-card grow">
          <div class="shui-card-title">表头设置</div>
          <div class="shui-field-group">
            <div class="shui-field-group-title">标题</div>
            <div class="shui-form-row">
              <span class="shui-form-label">标题文本</span>
              <el-input v-model="m.musicTitleText" style="width: 240px" />
            </div>
            <div class="shui-form-row">
              <span class="shui-form-label">显示标题</span>
              <el-switch v-model="m.musicTitleCheckbox" />
            </div>
            <FontSelect v-model="m.musicTitleFontFamily" label="标题字体" />
            <OpacitySlider v-model="m.musicTitleFontSize" label="标题字号" :min="12" :max="80" />
            <ColorField v-model="m.musicTitleFontColor" label="标题颜色" />
          </div>
          <div class="shui-field-group last">
            <div class="shui-field-group-title">副标（下划线）</div>
            <div class="shui-form-row">
              <span class="shui-form-label">副标文本</span>
              <el-input v-model="m.musicTitleUnderlineText" style="width: 240px" />
            </div>
            <div class="shui-form-row">
              <span class="shui-form-label">显示副标</span>
              <el-switch v-model="m.musicTitleUnderlineCheckbox" />
            </div>
            <ColorField v-model="m.musicTitleUnderlineColor" label="副标颜色" />
          </div>
        </div>
        <div class="shui-card grow">
          <div class="shui-card-title">背景设置</div>
          <div class="shui-field-group">
            <div class="shui-form-row">
              <span class="shui-form-label">背景颜色</span>
              <ColorField v-model="m.musicBackgroundColor" />
            </div>
            <OpacitySlider v-model="m.musicBackgroundOpacity" label="背景透明度" :min="0" :max="100" />
          </div>
          <div class="shui-field-group last">
            <div class="shui-form-row">
              <span class="shui-form-label">队列项背景</span>
              <el-switch v-model="m.musicBackgroundColorCheckbox" />
            </div>
            <ColorField v-model="m.musicQueueBackgroundColor" label="项背景颜色" />
            <OpacitySlider v-model="m.musicQueueOpacity" label="项背景透明度" :min="0" :max="100" />
          </div>
          <div class="shui-hint">背景透明度为 0 表示完全透明，OBS 中仅显示文字与项背景。</div>
        </div>
      </div>

      <div class="shui-col">
        <div class="shui-card grow">
          <div class="shui-card-title">列表设置</div>
          <FontSelect v-model="m.musicFontFamily" label="列表字体" />
          <OpacitySlider v-model="m.musicFontSize" label="列表字号" :min="12" :max="80" />
          <ColorField v-model="m.musicFontColor" label="列表颜色" />
        </div>
        <div class="shui-card grow-more">
          <div class="shui-card-title">效果预览</div>
          <div class="shui-preview-box" :style="previewRootStyle">
            <div v-if="m.musicTitleCheckbox" class="shui-preview-title" :style="previewTitleStyle">{{ m.musicTitleText }}</div>
            <div v-if="m.musicTitleUnderlineCheckbox" class="shui-preview-underline" :style="{ color: m.musicTitleUnderlineColor }">{{ m.musicTitleUnderlineText }}</div>
            <div class="shui-preview-list" :style="previewListStyle">
              <div v-if="!musicStore.musicList.length" class="i" style="opacity: 0.5">暂无待播歌曲</div>
              <div v-for="(song, index) in musicStore.musicList.slice(0, 5)" :key="entryName(song) + index" class="i" :style="previewItemStyle">
                <span class="n">{{ index + 1 }}.</span><span :title="entryName(song)">{{ formatSongTitle(song) }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* ===== 一屏压缩：页根限制视口高度，卡片 / 表单 / 预览同步收紧留白 ===== */
.page-root {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 48px);
  overflow-y: auto;
}
.page-root .shui-page-head { margin-bottom: 8px; }
.page-root .shui-page-head h2 { font-size: 20px; }
.page-root { margin-bottom: 8px; padding: 6px 14px; font-size: 12px; }
.page-root .shui-card { padding: 10px 14px; }
.page-root .shui-card + .shui-card { margin-top: 10px; }
.page-root .shui-card-title { margin-bottom: 8px; }
.page-root .shui-form-row { margin-bottom: 4px; }
.page-root .shui-form-row.last { margin-bottom: 0; }
.page-root .shui-field-group { padding: 8px 10px; margin-bottom: 8px; }
.page-root .shui-field-group.last { margin-bottom: 0; }
.page-root .shui-field-group-title { margin-bottom: 6px; }
.page-root .shui-hint { padding: 4px 10px; margin-top: 2px; line-height: 1.5; }
.page-root .shui-col-2 { flex: 1 1 auto; grid-template-rows: minmax(min-content, 1fr); gap: 10px; margin-bottom: 0; }
.page-root .shui-col { gap: 10px; }
.page-root .shui-preview-box { flex: 1; min-height: 96px; padding: 10px 14px; gap: 4px; }
.page-root .shui-preview-list { gap: 3px; }
.func-card { flex-shrink: 0; margin-bottom: 10px; }
.sub { font-size: 13px; color: var(--shui-text-dim); margin-top: 4px; }
.dim { color: var(--shui-text-dim); font-size: 12px; }
</style>
