<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted } from 'vue'
import { useMusicStore } from '@/stores/music'
import { useSettingsStore } from '@/stores/settings'
import { alphaStyle } from '@/utils/style'
import { formatSongTitle } from '@/utils/songTitle'
import { entryName, entrySinger, entryStatus } from '@/types'

// 点歌展示页用于 OBS 叠加：挂载时移除全局 body 深色背景，卸载时恢复
onMounted(() => document.body.classList.add('music-display-mode'))
onBeforeUnmount(() => document.body.classList.remove('music-display-mode'))

const musicStore = useMusicStore()
const settingsStore = useSettingsStore()

const m = computed(() => settingsStore.music)
const list = computed(() => musicStore.musicList)

const titleStyle = computed(() => ({
  fontFamily: m.value.musicTitleFontFamily,
  fontSize: m.value.musicTitleFontSize + 'px',
  color: m.value.musicTitleFontColor,
}))

// 整体容器背景（背景颜色 + 背景透明度）
const rootStyle = computed(() => ({
  background: alphaStyle(m.value.musicBackgroundColor, m.value.musicBackgroundOpacity),
}))

// 外层列表容器：仅字体样式，背景透明（整体背景由 rootStyle 控制，项背景由每项 itemStyle 控制）
const listStyle = computed(() => ({
  fontFamily: m.value.musicFontFamily,
  fontSize: m.value.musicFontSize + 'px',
  color: m.value.musicFontColor,
}))

// 每个队列项元素背景（开关 + 项背景颜色 + 项背景透明度）
const itemStyle = computed(() => ({
  background: m.value.musicBackgroundColorCheckbox
    ? alphaStyle(m.value.musicQueueBackgroundColor, m.value.musicQueueOpacity)
    : 'transparent',
  borderRadius: '8px',
  padding: '4px 10px',
}))
</script>

<template>
  <!-- 根即 .music-wrap：absolute 定位基准为 App.vue .display-shell（position:relative）；max-width 720px 视口水平居中 -->
  <div class="music-wrap" :style="rootStyle">
    <div class="display-info">
      <div v-if="m.musicTitleCheckbox" class="title-line" :style="titleStyle">{{ m.musicTitleText }}</div>
      <div v-if="m.musicTitleUnderlineCheckbox" class="underline" :style="{ color: m.musicTitleUnderlineColor }">{{ m.musicTitleUnderlineText }}</div>
    </div>
    <div class="music-list" :style="listStyle">
      <div v-if="!list.length" class="display-line" style="opacity: 0.5">暂无待播歌曲</div>
      <div v-for="(song, index) in list" :key="entryName(song) + index" class="display-line" :style="itemStyle">
        <span class="seq">{{ index + 1 }}.</span>
        <span class="song-name" :title="entryName(song)">{{ formatSongTitle(song) }}</span>
        <span v-if="entrySinger(song) && entryStatus(song) !== 'playing'" class="song-singer">- {{ entrySinger(song) }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.music-wrap {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  margin: 0 auto;
  width: 100%;
  height: 100%;
  max-width: 720px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  user-select: none;
}
.title-line { white-space: pre; }
.underline {
  white-space: pre;
  letter-spacing: 2px;
}
.music-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  scrollbar-width: none;
  border-radius: 12px;
  padding: 14px 20px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.music-list::-webkit-scrollbar { display: none; }
.display-line {
  display: flex;
  align-items: baseline;
  gap: 6px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.song-name { overflow: hidden; text-overflow: ellipsis; }
.song-singer { opacity: 0.78; flex-shrink: 0; }
</style>
