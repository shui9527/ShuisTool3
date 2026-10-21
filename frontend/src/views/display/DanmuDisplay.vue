<script setup lang="ts">
import { computed } from 'vue'
import { useConsoleStore } from '@/stores/console'

const consoleStore = useConsoleStore()

// 实时弹幕：logs 数组按时间正序追加（新弹幕在末尾），直接取最后 12 条，
// 新弹幕保持在最后一行，旧弹幕在上方
const logs = computed(() => consoleStore.logs.slice(-12))
</script>

<template>
  <div class="display-root">
    <div class="danmu-panel">
      <div class="danmu-title">直播间弹幕</div>
      <div class="danmu-list">
        <div v-if="!logs.length" class="danmu-line" style="opacity: 0.5">等待弹幕…</div>
        <div v-for="(item, index) in logs" :key="index" class="danmu-line">
          <span class="danmu-user">{{ item.username }}:</span>
          <span class="danmu-text">{{ item.danmu }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.display-root {
  width: 100vw;
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif;
}
.danmu-panel {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  gap: 14px;
  align-items: flex-start;
  justify-content: center;
  padding: 20px;
  box-sizing: border-box;
}
.danmu-title {
  font-weight: 700;
  letter-spacing: 1px;
  color: #ffd76e;
  font-size: 28px;
}
.danmu-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  line-height: 1.5;
  font-size: 20px;
  color: #e8ecf4;
  max-width: 100%;
}
.danmu-line {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.danmu-user {
  color: #4d8dff;
  font-weight: 600;
  margin-right: 6px;
}
</style>
