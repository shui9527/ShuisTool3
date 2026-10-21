<script setup lang="ts">
import { computed, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useQueueStore } from '@/stores/queue'
import { useSettingsStore } from '@/stores/settings'
import { alphaStyle } from '@/utils/style'
import FontSelect from '@/components/FontSelect.vue'
import ColorField from '@/components/ColorField.vue'
import OpacitySlider from '@/components/OpacitySlider.vue'
import GiftIcon from '@/components/GiftIcon.vue'

const queueStore = useQueueStore()
const settingsStore = useSettingsStore()
const q = settingsStore.queue

// 实时保存：监听全部设置字段变化，防抖 400ms 后整组提交（saveSection + 后端点号键展开链路不变）
watch(q, () => settingsStore.scheduleAutoSave('queue'), { deep: true })

async function toggleQueue(enable: boolean) {
  const ok = enable ? await queueStore.openQueue() : await queueStore.closeQueue()
  if (ok) ElMessage.success(enable ? '排队已开启' : '排队已关闭')
  else ElMessage.error('操作失败')
}

async function handleReset() {
  try {
    await ElMessageBox.confirm('确定恢复排队设置的默认值？', '恢复默认', { type: 'warning', confirmButtonText: '恢复默认', cancelButtonText: '取消' })
  } catch {
    return
  }
  const ok = await settingsStore.resetSection('queue')
  if (ok) ElMessage.success('已恢复默认并保存')
  else ElMessage.error('操作失败')
}

// 预览样式必须用 computed：依赖 q.* 字段，任一字段变化即重渲染（普通对象是 setup 一次性快照，改设置不刷新）
const previewTitleStyle = computed(() => ({
  fontFamily: q.titleFontFamily,
  fontSize: Math.max(12, Math.round(Number(q.titleFontSize) / 2)) + 'px',
  color: q.titleFontColor,
}))
// 预览根容器背景 = 整体背景（与 QueueDisplay 根容器一致）
const previewRootStyle = computed(() => ({
  background: alphaStyle(q.BackgroundColor, q.BackgroundOpacity),
}))
// 预览列表容器：仅字体样式，背景透明（整体背景由 previewRootStyle 控制，项背景由每项 previewItemStyle 控制）
const previewListStyle = computed(() => ({
  fontFamily: q.queueFontFamily,
  fontSize: Math.max(11, Math.round(Number(q.queueFontSize) / 2)) + 'px',
  color: q.queueFontColor,
}))
// 每个队列项元素背景（队列项背景：开关 + 项背景颜色 + 项背景透明度，与 QueueDisplay.itemStyle 一致）
const previewItemStyle = computed(() => ({
  background: q.QueueBackgroundColorCheckbox
    ? alphaStyle(q.QueueBackgroundColor, q.QueueOpacity)
    : 'transparent',
  borderRadius: '8px',
  padding: '3px 10px',
}))

// B15：礼物排队模式展示送礼价值（与 OBS 展示页一致；未送礼的排队者不显示徽章）
const showGift = computed(() => String(q.giftPriority) === 'true')
function giftValue(name: string): string {
  const g = queueStore.giftRecords[name]
  if (!g || !(g.coin > 0)) return ''
  // 金瓜子 / 1000 = 元，保留 1 位小数
  return (g.coin / 1000).toFixed(1)
}
</script>

<template>
  <div class="page-root">
    <div class="shui-page-head">
      <div>
        <h2>弹幕排队</h2>
        <div class="sub">弹幕命令排队 · 样式与 OBS 展示页同步</div>
      </div>
      <div class="shui-page-actions">
        <span class="shui-chip"><span class="dim">排队开关</span><el-switch size="small" :model-value="queueStore.queueEnabled" @change="toggleQueue" /></span>
        <el-button size="small" @click="handleReset">恢复默认</el-button>
      </div>
    </div>

    <!-- 功能设置 -->
    <div class="shui-card func-card">
      <div class="shui-card-title">功能设置</div>
      <div class="shui-form-row">
        <span class="shui-form-label">排队方式</span>
        <el-radio-group v-model="q.giftPriority">
          <el-radio value="false">顺序排队（先到先得）</el-radio>
          <el-radio value="true">礼物排队（按礼物优先级）</el-radio>
        </el-radio-group>
      </div>
      <div class="shui-form-row">
        <span class="shui-form-label">人数限制</span>
        <el-input v-model="q.queueLimit" placeholder="如 10" style="width: 90px" />
        <span class="dim">人 · 留空不限制</span>
      </div>
      <div class="shui-form-row">
        <span class="shui-form-label">排队命令</span>
        <el-input v-model="q.queueCommands" placeholder="如：排队，排队新深渊" style="width: 280px" />
        <span class="dim">多个命令用英文逗号分隔</span>
      </div>
      <div class="shui-form-row last">
        <span class="shui-form-label">取消命令</span>
        <el-input v-model="q.cancelCommands" placeholder="如：取消排队" style="width: 280px" />
        <span class="dim">发送后自动移出队列</span>
      </div>
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
              <el-input v-model="q.titleText" style="width: 240px" />
            </div>
            <div class="shui-form-row">
              <span class="shui-form-label">显示标题</span>
              <el-switch v-model="q.titleCheckbox" />
            </div>
            <FontSelect v-model="q.titleFontFamily" label="标题字体" />
            <OpacitySlider v-model="q.titleFontSize" label="标题字号" :min="12" :max="80" />
            <ColorField v-model="q.titleFontColor" label="标题颜色" />
          </div>
          <div class="shui-field-group last">
            <div class="shui-field-group-title">副标（下划线）</div>
            <div class="shui-form-row">
              <span class="shui-form-label">副标文本</span>
              <el-input v-model="q.titleUnderlineText" style="width: 240px" />
            </div>
            <div class="shui-form-row">
              <span class="shui-form-label">显示副标</span>
              <el-switch v-model="q.titleUnderlineCheckbox" />
            </div>
            <ColorField v-model="q.titleUnderlineColor" label="副标颜色" />
          </div>
        </div>
        <div class="shui-card grow">
          <div class="shui-card-title">背景设置</div>
          <div class="shui-field-group">
            <div class="shui-form-row">
              <span class="shui-form-label">背景颜色</span>
              <ColorField v-model="q.BackgroundColor" />
            </div>
            <OpacitySlider v-model="q.BackgroundOpacity" label="背景透明度" :min="0" :max="100" />
          </div>
          <div class="shui-field-group last">
            <div class="shui-form-row">
              <span class="shui-form-label">队列项背景</span>
              <el-switch v-model="q.QueueBackgroundColorCheckbox" />
            </div>
            <ColorField v-model="q.QueueBackgroundColor" label="项背景颜色" />
            <OpacitySlider v-model="q.QueueOpacity" label="项背景透明度" :min="0" :max="100" />
          </div>
          <div class="shui-hint">背景透明度为 0 表示完全透明，OBS 中仅显示文字与项背景。</div>
        </div>
      </div>

      <div class="shui-col">
        <div class="shui-card grow">
          <div class="shui-card-title">列表设置</div>
          <FontSelect v-model="q.queueFontFamily" label="列表字体" />
          <OpacitySlider v-model="q.queueFontSize" label="列表字号" :min="12" :max="80" />
          <ColorField v-model="q.queueFontColor" label="列表颜色" />
          <div class="shui-hint">排队列表按入队顺序展示序号 + 昵称，支持拖动排序。</div>
        </div>
        <div class="shui-card grow-more">
          <div class="shui-card-title">效果预览</div>
          <div class="shui-preview-box" :style="previewRootStyle">
            <div v-if="q.titleCheckbox" class="shui-preview-title" :style="previewTitleStyle">{{ q.titleText }}</div>
            <div v-if="q.titleUnderlineCheckbox" class="shui-preview-underline" :style="{ color: q.titleUnderlineColor }">{{ q.titleUnderlineText }}</div>
            <div class="shui-preview-list" :style="previewListStyle">
              <div v-if="!queueStore.queueList.length" class="i" style="opacity: 0.5">等待观众排队…</div>
              <div v-for="(name, index) in queueStore.queueList.slice(0, 5)" :key="name + index" class="i" :style="previewItemStyle">
                <span class="preview-name"><span class="n">{{ index + 1 }}.</span>{{ name }}</span>
                <span v-if="showGift && giftValue(name)" class="preview-gift">
                  <GiftIcon size="0.9em" />
                  <span>{{ giftValue(name) }}</span>
                </span>
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

/* B15：预览区队列项名称与送礼价值徽章布局 */
.i { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.preview-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.preview-gift {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  flex-shrink: 0;
  background: rgba(255, 255, 255, 0.14);
  border: 1px solid rgba(255, 255, 255, 0.28);
  border-radius: 999px;
  padding: 0 8px;
  font-size: 11px;
  color: #ffe9b3;
  white-space: nowrap;
}
</style>
