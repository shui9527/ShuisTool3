<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted } from 'vue'
import { useQueueStore } from '@/stores/queue'
import { useSettingsStore } from '@/stores/settings'
import { alphaStyle } from '@/utils/style'
import GiftIcon from '@/components/GiftIcon.vue'

// 排队展示页用于 OBS 叠加：挂载时移除全局 body 深色背景，卸载时恢复
onMounted(() => document.body.classList.add('queue-display-mode'))
onBeforeUnmount(() => document.body.classList.remove('queue-display-mode'))

const queueStore = useQueueStore()
const settingsStore = useSettingsStore()

const q = computed(() => settingsStore.queue)
const list = computed(() => queueStore.queueList)

// 标题/副标样式
const panelStyle = computed(() => ({
  fontFamily: q.value.titleFontFamily,
  fontSize: q.value.titleFontSize + 'px',
  color: q.value.titleFontColor,
}))

// 整体容器背景（背景设置：背景颜色 + 背景透明度）
const rootStyle = computed(() => ({
  background: alphaStyle(q.value.BackgroundColor, q.value.BackgroundOpacity),
}))

// 外层列表容器：仅字体样式，背景透明（整体背景由 rootStyle 控制，项背景由每项 itemStyle 控制）
const listStyle = computed(() => ({
  fontFamily: q.value.queueFontFamily,
  fontSize: q.value.queueFontSize + 'px',
  color: q.value.queueFontColor,
}))

// 每个队列项元素背景（队列项背景：开关 + 项背景颜色 + 项背景透明度）
const itemStyle = computed(() => ({
  background: q.value.QueueBackgroundColorCheckbox
    ? alphaStyle(q.value.QueueBackgroundColor, q.value.QueueOpacity)
    : 'transparent',
  borderRadius: '8px',
  padding: '4px 10px',
}))

// B15：礼物排队模式展示送礼价值（未送礼的排队者不显示徽章）
const showGift = computed(() => String(q.value.giftPriority) === 'true')
function giftValue(name: string): string {
  const g = queueStore.giftRecords[name]
  if (!g || !(g.coin > 0)) return ''
  // 金瓜子 / 1000 = 元，保留 1 位小数
  return (g.coin / 1000).toFixed(1)
}
</script>

<template>
  <!-- 根即 .queue-wrap：absolute 定位基准为 App.vue .display-shell（position:relative）；max-width 720px 视口水平居中 -->
  <div class="queue-wrap" :style="rootStyle">
    <div class="display-info">
      <div v-if="q.titleCheckbox" class="title-line" :style="panelStyle">{{ q.titleText }}</div>
      <div v-if="q.titleUnderlineCheckbox" class="underline" :style="{ color: q.titleUnderlineColor }">{{ q.titleUnderlineText }}</div>
    </div>
    <div class="queue-list" :style="listStyle">
      <div v-if="!list.length" class="display-line" style="opacity: 0.5">等待观众排队…</div>
      <div v-for="(name, index) in list" :key="name + index" class="display-line" :style="itemStyle">
        <span class="display-name"><span class="display-n">{{ index + 1 }}.</span>{{ name }}</span>
        <span v-if="showGift && giftValue(name)" class="gift-val">
          <GiftIcon size="1em" />
          <span class="gift-num">{{ giftValue(name) }}</span>
        </span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.queue-wrap {
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
.queue-list {
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
.queue-list::-webkit-scrollbar { display: none; }

/* B15：队列项名称与送礼价值徽章布局 */
.display-line { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.display-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.display-n { opacity: 0.65; margin-right: 6px; }
.gift-val {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
  background: rgba(255, 255, 255, 0.14);
  border: 1px solid rgba(255, 255, 255, 0.28);
  border-radius: 999px;
  padding: 1px 10px;
  font-size: 0.78em;
  color: #ffe9b3;
  white-space: nowrap;
}
.gift-num { font-weight: 700; }
</style>
