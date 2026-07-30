<template>
  <div class="flex flex-col h-full">
    <!-- 标题 -->
    <div class="flex items-center justify-between px-5 py-4 border-b border-gray-200 shrink-0">
      <div>
        <h2 class="text-lg font-bold text-gray-800">智能体决策可视化</h2>
        <p class="text-xs text-gray-400">推理链实时展示</p>
      </div>
      <div
        class="px-3 py-1 rounded-full text-xs font-bold border transition-all"
        :class="stateBadgeClass"
      >
        {{ stateBadgeText }}
      </div>
    </div>

    <!-- 内容区域 -->
    <div class="flex-1 overflow-y-auto p-4 space-y-4">
      <!-- 进度条 -->
      <div v-if="progress.total > 0">
        <ProgressBar
          :current="progress.current"
          :total="progress.total"
          :phase="progress.phase"
          :detail="progress.detail"
        />
      </div>

      <!-- 步骤列表 -->
      <div class="step-card">
        <div class="text-xs text-gray-500 font-medium mb-4 flex items-center gap-2">
          <LanIcon name="list" /> 执行步骤
          <span class="text-gray-400 text-xs font-mono ml-auto">
            {{ doneSteps }}/{{ steps.length }}
          </span>
        </div>
        <div class="space-y-0">
          <div
            v-for="(step, i) in steps"
            :key="step.id"
            class="flex gap-3"
          >
            <!-- 步骤指示线 -->
            <div class="flex flex-col items-center">
              <div
                class="lan-timeline-node"
                :class="getStepWrapperClass(step)"
                :style="step.status === 'active' ? { animation: 'pulse-scale 2s infinite' } : {}"
              ></div>
              <div
                v-if="!isLastStep(i)"
                class="lan-timeline-line flex-1 min-h-[20px] mt-1 mb-1 rounded"
                :class="step.status === 'done' ? 'is-complete' : ''"
              ></div>
            </div>

            <!-- 步骤内容 -->
            <div class="flex-1 pb-4" :class="isLastStep(i) ? 'pb-0' : ''">
              <div
                class="lan-step-label text-sm font-medium flex items-center gap-2"
                :class="getStepLabelClass(step)"
              >
                <LanIcon :name="getStepActionIcon(step)" class="lan-step-action-icon" />
                <span>{{ step.label }}</span>
              </div>

              <!-- 过滤步骤详情 -->
              <div
                v-if="step.key === 'filter' && step.status === 'done' && filterResult"
                class="mt-2 bg-gray-50 rounded-lg p-3 border border-gray-200 text-xs"
              >
                <div class="space-y-1.5">
                  <div class="flex gap-4 text-gray-700">
                    <span>总患者: <b class="text-blue-600">{{ filterResult.total }}</b></span>
                    <span>待发送: <b class="text-green-600">{{ filterResult.send_count }}</b></span>
                    <span>免随访: <b class="text-yellow-600">{{ filterResult.skip_count }}</b></span>
                  </div>
                  <template v-if="filterResult.skip_details && filterResult.skip_details.length > 0">
                    <div class="text-gray-500 mb-1">免随访明细:</div>
                    <div v-for="d in filterResult.skip_details.slice(0, 5)" :key="d.patient_id" class="flex justify-between text-gray-600 py-0.5">
                      <span>{{ d.name }} ({{ d.patient_id }})</span>
                      <span class="text-gray-400">{{ d.reason }}</span>
                    </div>
                    <div v-if="filterResult.skip_details.length > 5" class="text-gray-400 text-xs">
                      ...共 {{ filterResult.skip_details.length }} 人
                    </div>
                  </template>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 智能体思考展示 -->
      <Transition name="thinking-fade">
        <div
          v-if="agentState.name === 'GENERATING' && progress.phase === 'generating'"
          class="step-card border-purple-300 bg-purple-50"
        >
          <div class="flex items-center gap-2 mb-3">
            <span class="text-lg">🤔</span>
            <span class="text-sm font-medium text-purple-600">智能体正在思考...</span>
            <span class="ml-auto flex gap-1">
              <span class="w-1.5 h-1.5 bg-purple-400 rounded-full animate-pulse-dot" style="animation-delay: 0s"></span>
              <span class="w-1.5 h-1.5 bg-purple-400 rounded-full animate-pulse-dot" style="animation-delay: 0.3s"></span>
              <span class="w-1.5 h-1.5 bg-purple-400 rounded-full animate-pulse-dot" style="animation-delay: 0.6s"></span>
            </span>
          </div>
          <div class="text-xs text-gray-600 space-y-1">
            <div>· 读取患者历史数据，分析疼痛趋势</div>
            <div>· 根据趋势选择适当的沟通语气</div>
            <div>· 生成包含三个标准问题的个性化消息</div>
          </div>
        </div>
      </Transition>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import LanIcon from './LanIcon.vue'
import ProgressBar from './ProgressBar.vue'

const props = defineProps({
  agentState: { type: Object, default: () => ({ name: 'IDLE', label: '' }) },
  steps: { type: Array, default: () => [] },
  progress: { type: Object, default: () => ({ current: 0, total: 0, phase: '', detail: {} }) },
  logs: { type: Array, default: () => [] },
  filterResult: { type: Object, default: null },
  generatedMessages: { type: Array, default: () => [] },
})

const stateBadgeClass = computed(() => {
  const name = props.agentState.name
  return name === 'IDLE'
    ? 'bg-gray-100 text-gray-500 border-gray-300'
    : name === 'ERROR'
      ? 'bg-red-50 text-red-600 border-red-200'
      : 'bg-blue-50 text-blue-600 border-blue-200'
})

const stateBadgeText = computed(() => {
  const name = props.agentState.name
  return name === 'IDLE' ? '空闲'
    : name === 'ERROR' ? '异常'
    : (props.agentState.label || name)
})

const doneSteps = computed(() => props.steps.filter(s => s.status === 'done').length)

const STEP_STATUS_STYLES = {
  pending: 'is-pending',
  active: 'is-active',
  done: 'is-done',
  error: 'is-error',
  skipped: 'is-skipped',
}

function getStepWrapperClass(step) {
  return STEP_STATUS_STYLES[step.status] || STEP_STATUS_STYLES.pending
}

const STEP_ACTION_ICONS = {
  fetch: 'list',
  filter: 'search',
  generate: 'settings',
  send: 'upload',
  wait: 'time',
  parse: 'filePaste',
  score: 'list',
  route: 'right',
  alert: 'notification',
}

function getStepActionIcon(step) {
  return STEP_ACTION_ICONS[step.key] || 'list'
}

function getStepLabelClass(step) {
  if (step.status === 'active') return 'text-blue-700'
  if (step.status === 'done') return 'text-green-700'
  return 'text-gray-500'
}

function isLastStep(index) {
  return index === props.steps.length - 1
}
</script>

<style scoped>
.lan-timeline-node {
  width: 24px;
  height: 24px;
  flex: 0 0 auto;
  border: 3px solid #fff;
  border-radius: 50%;
  background: #a6a6a6;
  box-shadow: 0 0 0 1px #dcdcdc;
}
.lan-timeline-node.is-active,
.lan-timeline-node.is-done {
  border-color: #d7fffe;
  background: #079284;
  box-shadow: 0 0 0 1px #43d5c7;
}
.lan-timeline-node.is-error {
  border-color: #fff0ed;
  background: #d54941;
  box-shadow: 0 0 0 1px #f6685d;
}
.lan-timeline-node.is-skipped {
  opacity: 0.56;
}
.lan-timeline-line {
  width: 1px;
  background: #dcdcdc;
}
.lan-timeline-line.is-complete {
  background: #a9f2eb;
}
.lan-step-action-icon {
  width: 16px;
  height: 16px;
}
.thinking-fade-enter-active {
  transition: all 0.3s ease-out;
}
.thinking-fade-leave-active {
  transition: all 0.2s ease-in;
}
.thinking-fade-enter-from {
  opacity: 0;
  transform: translateY(20px);
}
.thinking-fade-leave-to {
  opacity: 0;
}
</style>
