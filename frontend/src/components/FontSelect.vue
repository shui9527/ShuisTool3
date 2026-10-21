<script setup lang="ts">
import { ref, onMounted } from 'vue'

interface FontOption {
  name: string
  value: string
}

// 内置回退字体（接口请求失败时使用，不阻塞使用）
const FALLBACK_FONTS: FontOption[] = [
  { name: '微软雅黑', value: 'Microsoft YaHei, sans-serif' },
  { name: '黑体', value: 'SimHei, sans-serif' },
  { name: '宋体', value: 'SimSun, serif' },
  { name: '楷体', value: 'KaiTi, serif' },
  { name: '仿宋', value: 'FangSong, serif' },
]

defineProps<{
  modelValue: string
  label?: string
}>()
defineEmits<{
  (e: 'update:modelValue', value: string): void
}>()

const fonts = ref<FontOption[]>(FALLBACK_FONTS)

onMounted(async () => {
  try {
    const resp = await fetch('/setting/fontdict')
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const data = await resp.json()
    const list = data?.font_dict
    if (Array.isArray(list) && list.length) {
      fonts.value = list
    }
  } catch {
    // 请求失败回退到内置 FALLBACK_FONTS，不阻塞使用
  }
})
</script>

<template>
  <div class="shui-form-row">
    <span v-if="label" class="shui-form-label">{{ label }}</span>
    <el-select
      :model-value="modelValue"
      filterable
      style="width: 220px"
      @update:model-value="$emit('update:modelValue', $event)"
    >
      <el-option v-for="f in fonts" :key="f.value" :label="f.name" :value="f.value" />
    </el-select>
  </div>
</template>
