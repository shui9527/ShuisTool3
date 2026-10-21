<script setup lang="ts">
defineProps<{
  modelValue: string | number
  label?: string
  min?: number
  max?: number
}>()
defineEmits<{
  (e: 'update:modelValue', value: string): void
}>()
</script>

<template>
  <div class="shui-form-row">
    <span v-if="label" class="shui-form-label">{{ label }}</span>
    <el-slider
      :model-value="Number(modelValue) || 0"
      :min="min ?? 0"
      :max="max ?? 100"
      style="width: 180px"
      @update:model-value="$emit('update:modelValue', String($event))"
    />
    <span class="val">{{ modelValue }}</span>
  </div>
</template>

<style scoped>
.val { font-size: 12px; color: var(--shui-text-dim); width: 32px; }
</style>
