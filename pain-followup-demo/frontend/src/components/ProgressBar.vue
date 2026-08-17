<!-- frontend/src/components/ProgressBar.vue -->
<template>
  <div class="lan-progress" role="progressbar" :aria-valuenow="current" aria-valuemin="0" :aria-valuemax="total">
    <div class="flex items-center justify-between gap-3 text-xs text-gray-500 mb-2">
      <span>{{ phaseLabel }}</span>
      <span class="font-mono">{{ current }} / {{ total }}</span>
    </div>
    <div class="lan-progress-track">
      <div class="lan-progress-value" :style="{ width: `${percent}%` }"></div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  current: { type: Number, default: 0 },
  total: { type: Number, default: 0 },
  phase: { type: String, default: '' },
  detail: { type: Object, default: () => ({}) },
})

const percent = computed(() => props.total > 0 ? Math.min(100, Math.max(0, props.current / props.total * 100)) : 0)
const phaseLabel = computed(() => props.detail?.label || props.detail?.message || props.phase || '执行进度')
</script>
