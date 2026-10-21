<script setup lang="ts">
// 礼盒图标：徽章展示送礼价值用（暖金礼盒 + 奶油缎带蝴蝶结）
defineProps<{ size?: number | string }>()
</script>

<template>
  <svg :width="size ?? '1em'" :height="size ?? '1em'" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
    <defs>
      <linearGradient id="giftBoxBody" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="#ffc36b" />
        <stop offset="1" stop-color="#e8923a" />
      </linearGradient>
      <linearGradient id="giftBoxLid" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="#ffd089" />
        <stop offset="1" stop-color="#ef9f4d" />
      </linearGradient>
    </defs>
    <!-- 竖向缎带（穿盖） -->
    <rect x="29.5" y="16" width="5" height="46" rx="2.5" fill="#ffe9bd" />
    <!-- 盒盖 -->
    <rect x="8" y="21" width="48" height="10" rx="4" fill="url(#giftBoxLid)" stroke="#d9822b" stroke-width="1.5" />
    <!-- 盒体 -->
    <rect x="10" y="30" width="44" height="28" rx="5" fill="url(#giftBoxBody)" stroke="#d9822b" stroke-width="1.5" />
    <!-- 横向缎带（盒体上） -->
    <rect x="10" y="34" width="44" height="6" rx="3" fill="#ffe9bd" opacity="0.95" />
    <!-- 蝴蝶结：左右瓣 + 中心结 -->
    <path d="M32 20 L21 14 L19 24 L31 26 Z" fill="#ffe9bd" stroke="#d9822b" stroke-width="1.5" stroke-linejoin="round" />
    <path d="M32 20 L43 14 L45 24 L33 26 Z" fill="#ffe9bd" stroke="#d9822b" stroke-width="1.5" stroke-linejoin="round" />
    <circle cx="32" cy="22" r="4" fill="#ffd9a0" stroke="#d9822b" stroke-width="1.5" />
  </svg>
</template>
