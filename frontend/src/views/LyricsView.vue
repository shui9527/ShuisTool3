<script setup lang="ts">
import { computed, watch, type CSSProperties } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useMusicStore } from '@/stores/music'
import { useSettingsStore } from '@/stores/settings'
import { alphaStyle } from '@/utils/style'
import { formatSongTitle } from '@/utils/songTitle'
import FontSelect from '@/components/FontSelect.vue'
import ColorField from '@/components/ColorField.vue'
import OpacitySlider from '@/components/OpacitySlider.vue'

const musicStore = useMusicStore()
const settingsStore = useSettingsStore()
const l = settingsStore.lyrics

// 实时保存：监听全部设置字段变化，防抖 400ms 后整组提交（saveSection + 后端点号键展开链路不变）
watch(l, () => settingsStore.scheduleAutoSave('lyrics'), { deep: true })

async function handleReset() {
  try {
    await ElMessageBox.confirm('确定恢复歌词样式的默认值？', '恢复默认', { type: 'warning', confirmButtonText: '恢复默认', cancelButtonText: '取消' })
  } catch {
    return
  }
  const ok = await settingsStore.resetSection('lyrics')
  if (ok) ElMessage.success('已恢复默认并保存')
  else ElMessage.error('操作失败')
}

// 预览数据源：SSE 已连接且有播放信息时实时跟随展示页（歌名/歌手/当前行歌词）；
// 无 SSE 推送时显示测试占位文案
const liveActive = computed(() => musicStore.lyricsConnected && (!!musicStore.playing?.name || !!musicStore.lyricLine || musicStore.lyricLines.length > 0))

const previewTitleFull = computed(() => {
  if (liveActive.value && musicStore.playing?.name) return musicStore.playing.name
  return '蓝天（测试歌名）'
})
// 展示层精简末尾括号，title 悬浮显示完整原名
const previewTitle = computed(() => formatSongTitle(previewTitleFull.value))
const previewSinger = computed(() => {
  if (liveActive.value && musicStore.playing?.singer) return musicStore.playing.singer
  return '黄子华（测试歌手）'
})
// 单行：优先当前行 lyricLine（与展示页一致），其次 LRC 首行，无推送时用测试占位
const previewLine = computed(() => {
  if (liveActive.value) {
    if (musicStore.lyricLine) return musicStore.lyricLine
    const lines = musicStore.lyricLines.map((x) => x.text).filter(Boolean)
    if (lines.length) return lines[0]
  }
  return '蓝蓝天空高挂 我的梦'
})

const previewWrapStyle = computed<CSSProperties>(() => ({
  flexDirection: l.layoutposition === 'vertical' ? 'column' : 'row',
  gap: l.layoutGap + 'px',
  padding: l.layoutMargin + 'px',
  background: alphaStyle(l.lyricsbackgroundColor, l.lyricsbackgroundOpacity),
  width: l.layoutWidth + 'px',
  maxWidth: '100%',
}))

// 歌名宽度：最小 100，最大 = 整体宽度 - 200（仅左右布局显示）
const maxTitleBoxWidth = computed(() => Math.max(100, Math.round(Number(l.layoutWidth || 900)) - 200))
watch(maxTitleBoxWidth, (mx) => {
  if (Number(l.titleBoxWidth) > mx) l.titleBoxWidth = String(mx)
})
// 弹性布局：歌名列固定宽度（不可压缩），歌词列 flex:1 弹性填充剩余空间
const leftColStyle = computed<CSSProperties>(() =>
  l.layoutposition === 'horizontal' ? { width: l.titleBoxWidth + 'px', flexShrink: 0 } : {}
)
const rightColStyle = computed<CSSProperties>(() =>
  l.layoutposition === 'horizontal' ? { flex: '1 1 auto', minWidth: '0' } : {}
)

const titleStyle = computed<CSSProperties>(() => ({
  fontFamily: l.lyricstitleFontFamily,
  fontSize: l.lyricstitleFontSize + 'px',
  color: l.lyricstitleFontColor,
  opacity: (Number(l.lyricstitleOpacity) || 0) / 100,
  textAlign: l.lyricstitleAlign === 'center' ? 'center' : l.lyricstitleAlign === 'right' ? 'right' : 'left',
}))

const artistStyle = computed<CSSProperties>(() => ({
  fontFamily: l.lyricsartistFontFamily,
  fontSize: l.lyricsartistFontSize + 'px',
  color: l.lyricsartistFontColor,
  opacity: (Number(l.lyricsartistOpacity) || 0) / 100,
  textAlign: l.lyricsartistAlign === 'center' ? 'center' : l.lyricsartistAlign === 'right' ? 'right' : 'left',
}))

const lineStyle = computed<CSSProperties>(() => ({
  fontFamily: l.lyricsFontFamily,
  fontSize: l.lyricsFontSize + 'px',
  color: l.lyricsFontColor,
  opacity: (Number(l.lyricsOpacity) || 0) / 100,
  textAlign: l.lyricsAlign === 'center' ? 'center' : l.lyricsAlign === 'right' ? 'right' : 'left',
  lineHeight: (Number(l.lyricsFontSize) + Number(l.lineSpacing || 8)) + 'px',
}))
</script>

<template>
  <div>
    <div class="shui-page-head">
      <div>
        <h2>歌词样式</h2>
        <div class="sub">洛雪歌词实时推送 · OBS 歌词展示页样式</div>
      </div>
      <div class="shui-page-actions">
        <el-button size="small" @click="handleReset">恢复默认</el-button>
      </div>
    </div>

    <!-- 效果预览 -->
    <div class="shui-card" style="margin-bottom: 14px;">
      <div class="shui-card-title">效果预览</div>
      <div class="preview-stage">
        <div class="ly-preview" :style="previewWrapStyle">
          <div class="ly-col" :style="leftColStyle">
            <div v-if="l.lyricstitleEnabled" class="ly-title" :style="titleStyle" :title="previewTitleFull">{{ previewTitle }}</div>
            <div v-if="l.lyricsartistEnabled" class="ly-artist" :style="artistStyle">{{ previewSinger }}</div>
          </div>
          <div class="ly-col" :style="rightColStyle">
            <Transition :name="l.lyricsFadein ? 'lyric-rise' : ''" mode="out-in">
              <div class="ly-line" :key="previewLine" :style="lineStyle">{{ previewLine }}</div>
            </Transition>
          </div>
        </div>
      </div>
    </div>

    <!-- 左右两列等高 -->
    <div class="shui-col-2">
      <div class="shui-col">
        <div class="shui-card grow">
          <div class="shui-card-title">歌名设置</div>
          <div class="shui-form-row">
            <span class="shui-form-label">显示歌名</span>
            <el-switch v-model="l.lyricstitleEnabled" />
          </div>
          <FontSelect v-model="l.lyricstitleFontFamily" label="歌名字体" />
          <ColorField v-model="l.lyricstitleFontColor" label="歌名颜色" />
          <OpacitySlider v-model="l.lyricstitleFontSize" label="歌名字号" :min="12" :max="80" />
          <OpacitySlider v-model="l.lyricstitleOpacity" label="歌名透明度" :min="0" :max="100" />
        </div>
        <div class="shui-card grow">
          <div class="shui-card-title">歌词设置</div>
          <FontSelect v-model="l.lyricsFontFamily" label="歌词字体" />
          <ColorField v-model="l.lyricsFontColor" label="歌词颜色" />
          <OpacitySlider v-model="l.lyricsFontSize" label="歌词字号" :min="12" :max="100" />
          <OpacitySlider v-model="l.lyricsOpacity" label="歌词透明度" :min="0" :max="100" />
          <div class="shui-form-row">
            <span class="shui-form-label">背景颜色</span>
            <ColorField v-model="l.lyricsbackgroundColor" />
          </div>
          <OpacitySlider v-model="l.lyricsbackgroundOpacity" label="背景透明度" :min="0" :max="100" />
          <div class="shui-form-row" style="margin-bottom: 0;">
            <span class="shui-form-label">歌词渐显</span>
            <el-switch v-model="l.lyricsFadein" />
            <span class="dim">切换歌词时淡入</span>
          </div>
          <div class="shui-hint">透明度为 0 表示完全透明，OBS 中仅显示文字。</div>
        </div>
      </div>

      <div class="shui-col">
        <div class="shui-card grow">
          <div class="shui-card-title">歌手设置</div>
          <div class="shui-form-row">
            <span class="shui-form-label">显示歌手</span>
            <el-switch v-model="l.lyricsartistEnabled" />
          </div>
          <FontSelect v-model="l.lyricsartistFontFamily" label="歌手字体" />
          <ColorField v-model="l.lyricsartistFontColor" label="歌手颜色" />
          <OpacitySlider v-model="l.lyricsartistFontSize" label="歌手字号" :min="12" :max="60" />
          <OpacitySlider v-model="l.lyricsartistOpacity" label="歌手透明度" :min="0" :max="100" />
        </div>
        <div class="shui-card grow-more">
          <div class="shui-card-title">布局设置</div>
          <div class="shui-form-row">
            <span class="shui-form-label">布局方式</span>
            <el-radio-group v-model="l.layoutposition">
              <el-radio value="horizontal">左右（歌名 | 歌词）</el-radio>
              <el-radio value="vertical">上下（歌名在上）</el-radio>
            </el-radio-group>
          </div>
          <div class="shui-form-row">
            <span class="shui-form-label">歌名对齐</span>
            <el-radio-group v-model="l.lyricstitleAlign">
              <el-radio value="left">左对齐</el-radio>
              <el-radio value="center">居中</el-radio>
              <el-radio value="right">右对齐</el-radio>
            </el-radio-group>
          </div>
          <div class="shui-form-row">
            <span class="shui-form-label">歌手对齐</span>
            <el-radio-group v-model="l.lyricsartistAlign">
              <el-radio value="left">左对齐</el-radio>
              <el-radio value="center">居中</el-radio>
              <el-radio value="right">右对齐</el-radio>
            </el-radio-group>
          </div>
          <div class="shui-form-row">
            <span class="shui-form-label">歌词对齐</span>
            <el-radio-group v-model="l.lyricsAlign">
              <el-radio value="left">左对齐</el-radio>
              <el-radio value="center">居中</el-radio>
              <el-radio value="right">右对齐</el-radio>
            </el-radio-group>
          </div>
          <OpacitySlider v-model="l.layoutWidth" label="整体宽度" :min="300" :max="1920" />
          <OpacitySlider
            v-if="l.layoutposition === 'horizontal'"
            v-model="l.titleBoxWidth"
            label="歌名宽度"
            :min="100"
            :max="maxTitleBoxWidth"
          />
          <OpacitySlider v-model="l.lineSpacing" label="歌词行距" :min="0" :max="40" />
          <OpacitySlider v-model="l.layoutMargin" label="整体边距" :min="0" :max="80" />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.sub { font-size: 13px; color: var(--shui-text-dim); margin-top: 4px; }
.dim { color: var(--shui-text-dim); font-size: 12px; }
.preview-stage {
  background: #0d1220;
  border: 1px solid var(--shui-border);
  border-radius: 12px;
  min-height: 170px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}
.ly-preview {
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 12px;
  max-width: 90%;
}
.ly-col { display: flex; flex-direction: column; justify-content: center; gap: 4px; min-width: 0; }
.ly-title { font-weight: 700; white-space: nowrap; }
.ly-artist { white-space: nowrap; }
.ly-line { white-space: nowrap; letter-spacing: 1px; }
/* 歌词渐显：柔和上浮 + 模糊渐入（下方 8px 上浮、6px 模糊渐清晰、0.5s 缓动；关闭时无动画） */
.lyric-rise-enter-active { transition: opacity 0.3s ease-out, transform 0.3s ease-out, filter 0.3s ease-out; }
.lyric-rise-enter-from { opacity: 0; transform: translateY(8px); filter: blur(6px); }
.lyric-rise-leave-active { transition: opacity 0.18s ease; }
.lyric-rise-leave-to { opacity: 0; }
</style>
