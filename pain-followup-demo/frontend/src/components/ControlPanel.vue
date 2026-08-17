<!-- frontend/src/components/ControlPanel.vue -->
<template>
  <div
    class="flex flex-col h-full"
    :class="inner ? 'bg-white rounded-2xl overflow-hidden shadow-lg' : ''"
  >
    <!-- 控制按钮组 -->
    <div class="flex-1 p-4 space-y-3">
      <!-- 开始随访 -->
      <button
        @click="$emit('startFollowup')"
        :disabled="!canStart"
        class="lan-btn lan-btn-primary w-full py-3 px-4 rounded-xl font-semibold text-sm transition-all flex items-center justify-center gap-2"
        :class="canStart
          ? 'bg-gradient-to-r from-blue-600 to-cyan-600 text-white shadow-md hover:shadow-lg hover:scale-[1.02] active:scale-[0.98]'
          : 'bg-gray-200 text-gray-400 cursor-not-allowed'"
      >
        <LanIcon name="play" />
        {{ canStart ? '开始今日随访' : isRunning ? '执行中...' : '等待重置' }}
      </button>

      <!-- 重置按钮 -->
      <button
        @click="$emit('reset')"
        class="lan-btn lan-btn-outline lan-control-reset w-full py-2.5 px-3 rounded-xl font-medium transition-all flex items-center justify-center gap-1
          bg-gray-100 hover:bg-gray-200 text-gray-500 border border-gray-200 hover:scale-[1.02] active:scale-[0.98]"
      >
        <LanIcon name="reload" /> 重置演示
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import LanIcon from './LanIcon.vue'

const props = defineProps({
  onStartFollowup: { type: Function, default: () => {} },
  onReset: { type: Function, default: () => {} },
  agentState: { type: Object, default: () => ({}) },
  isRunning: { type: Boolean, default: false },
  inner: { type: Boolean, default: false },
})

defineEmits(['startFollowup', 'reset'])

// 调度未在运行时允许启动（agentState 已从旧 IDLE 状态机改为调度状态）
const canStart = computed(() => !props.isRunning)
</script>
