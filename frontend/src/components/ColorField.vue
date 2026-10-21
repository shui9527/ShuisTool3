<script setup lang="ts">
defineProps<{
  modelValue: string
  label?: string
}>()
defineEmits<{
  (e: 'update:modelValue', value: string): void
}>()
</script>

<template>
  <div class="shui-form-row">
    <span v-if="label" class="shui-form-label">{{ label }}</span>
    <el-color-picker :model-value="modelValue" @update:model-value="$emit('update:modelValue', $event)" />
    <span class="hex-text">{{ modelValue }}</span>
  </div>
</template>

<style scoped>
.hex-text { font-size: 12px; color: var(--shui-text-dim); font-family: Consolas, monospace; }
</style>
