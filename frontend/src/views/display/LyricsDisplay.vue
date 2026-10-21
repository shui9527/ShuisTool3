<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, type CSSProperties } from 'vue'
import { useMusicStore } from '@/stores/music'
import { useSettingsStore } from '@/stores/settings'
import { alphaStyle } from '@/utils/style'
import { formatSongTitle } from '@/utils/songTitle'

// 歌词展示页用于 OBS 叠加：挂载时移除全局 body 深色背景，卸载时恢复
onMounted(() => document.body.classList.add('lyrics-display-mode'))
onBeforeUnmount(() => document.body.classList.remove('lyrics-display-mode'))

const musicStore = useMusicStore()
const settingsStore = useSettingsStore()

const l = computed(() => settingsStore.lyrics)

const titleStyle = computed<CSSProperties>(() => ({
  fontFamily: l.value.lyricstitleFontFamily,
  fontSize: l.value.lyricstitleFontSize + 'px',
  color: l.value.lyricstitleFontColor,
  opacity: (Number(l.value.lyricstitleOpacity) || 0) / 100,
  textAlign: l.value.lyricstitleAlign === 'center' ? 'center' : l.value.lyricstitleAlign === 'right' ? 'right' : 'left',
}))

const artistStyle = computed<CSSProperties>(() => ({
  fontFamily: l.value.lyricsartistFontFamily,
  fontSize: l.value.lyricsartistFontSize + 'px',
  color: l.value.lyricsartistFontColor,
  opacity: (Number(l.value.lyricsartistOpacity) || 0) / 100,
  textAlign: l.value.lyricsartistAlign === 'center' ? 'center' : l.value.lyricsartistAlign === 'right' ? 'right' : 'left',
}))

const lineStyle = computed<CSSProperties>(() => ({
  fontFamily: l.value.lyricsFontFamily,
  fontSize: l.value.lyricsFontSize + 'px',
  color: l.value.lyricsFontColor,
  opacity: (Number(l.value.lyricsOpacity) || 0) / 100,
  textAlign: l.value.lyricsAlign === 'center' ? 'center' : l.value.lyricsAlign === 'right' ? 'right' : 'left',
  lineHeight: (Number(l.value.lyricsFontSize) + Number(l.value.lineSpacing || 8)) + 'px',
}))

const wrapStyle = computed<CSSProperties>(() => ({
  background: alphaStyle(l.value.lyricsbackgroundColor, l.value.lyricsbackgroundOpacity),
  borderRadius: '12px',
  padding: l.value.layoutMargin + 'px ' + Math.round(Number(l.value.layoutMargin || 20) * 1.6) + 'px',
  flexDirection: l.value.layoutposition === 'vertical' ? 'column' : 'row',
  gap: l.value.layoutGap + 'px',
  width: l.value.layoutWidth + 'px',
  maxWidth: '90vw',
}))

// 弹性布局：歌名列固定宽度（不可压缩），歌词列 flex:1 弹性填充剩余空间
const leftColStyle = computed<CSSProperties>(() =>
  l.value.layoutposition === 'horizontal' ? { width: l.value.titleBoxWidth + 'px', flexShrink: 0 } : {}
)
const rightColStyle = computed<CSSProperties>(() =>
  l.value.layoutposition === 'horizontal' ? { flex: '1 1 auto', minWidth: '0' } : {}
)
</script>

<template>
  <!-- 根即 .lyrics-wrap：absolute 定位基准为 App.vue .display-shell（position:relative） -->
  <div class="lyrics-wrap" :style="wrapStyle">
      <div class="ly-col" :style="leftColStyle">
        <div v-if="l.lyricstitleEnabled" class="ly-title" :style="titleStyle" :title="musicStore.playing?.name || '当前歌曲'">{{ formatSongTitle(musicStore.playing?.name || '当前歌曲') }}</div>
        <div v-if="l.lyricsartistEnabled" class="ly-artist" :style="artistStyle">{{ musicStore.playing?.singer || '' }}</div>
      </div>
      <div class="ly-col" :style="rightColStyle">
        <Transition :name="l.lyricsFadein ? 'lyric-rise' : ''" mode="out-in">
          <div class="ly-line" :key="musicStore.lyricLine || '　'" :style="lineStyle">{{ musicStore.lyricLine || '　' }}</div>
        </Transition>
      </div>
  </div>
</template>

<style scoped>
.lyrics-wrap {
  position: absolute;
  top: 0;
  left: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  max-width: 80vw;
  user-select: none;
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
