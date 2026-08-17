<!-- frontend/src/components/AgentThinking.vue -->
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
        <div v-if="executionProgress.total > 0" class="mb-4 rounded-lg bg-slate-50 border border-slate-200 px-3 py-2 text-xs text-gray-600">
          <div class="flex items-center justify-between mb-1.5">
            <span>并行 Episode 进度</span>
            <span class="font-mono text-gray-500">{{ executionProgress.reported }}/{{ executionProgress.total }} 已出报告</span>
          </div>
          <div class="flex flex-wrap gap-x-4 gap-y-1 text-gray-500">
            <span>对话中 {{ Math.max(0, executionProgress.started - executionProgress.reported) }}</span>
            <span>待人工 {{ executionProgress.waiting }}</span>
            <span>已评分 {{ executionProgress.scored }}</span>
            <span>已审阅 {{ executionProgress.reviewed }}</span>
          </div>
        </div>
        <div class="space-y-0">
          <template v-for="(step, i) in steps" :key="step.id">
            <div v-if="isFirstInGroup(step, i)" class="pt-2 pb-2 text-[11px] font-semibold tracking-wide text-gray-400 uppercase">
              {{ groupTitle(step.group) }}
            </div>
            <div class="flex gap-3">
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
                    <div v-for="d in filterResult.skip_details.slice(0, 5)" :key="d.patient_id" class="flex justify-between gap-3 text-gray-600 py-0.5">
                      <div class="min-w-0">
                        <div>{{ d.name }}</div>
                        <div class="text-gray-400 truncate">· {{ d.diagnosis || '未填写诊断' }}</div>
                      </div>
                      <span class="text-gray-400 shrink-0">{{ d.reason }}</span>
                    </div>
                    <div v-if="filterResult.skip_details.length > 5" class="text-gray-400 text-xs">
                      ...共 {{ filterResult.skip_details.length }} 人
                    </div>
                  </template>
                </div>
              </div>
            </div>
            </div>
          </template>
        </div>
      </div>

      <!-- 智能体思考展示（调度进行中 / 多 Agent 并行随访时显示） -->
      <Transition name="thinking-fade">
        <div
          v-if="agentThinking"
          class="step-card border-purple-300 bg-purple-50"
        >
          <div class="flex items-center gap-2 mb-3">
            <span class="text-lg">🤔</span>
            <span class="text-sm font-medium text-purple-600">多智能体正在协同工作...</span>
            <span class="ml-auto flex gap-1">
              <span class="w-1.5 h-1.5 bg-purple-400 rounded-full animate-pulse-dot" style="animation-delay: 0s"></span>
              <span class="w-1.5 h-1.5 bg-purple-400 rounded-full animate-pulse-dot" style="animation-delay: 0.3s"></span>
              <span class="w-1.5 h-1.5 bg-purple-400 rounded-full animate-pulse-dot" style="animation-delay: 0.6s"></span>
            </span>
          </div>
          <div class="text-xs text-gray-600 space-y-1">
            <div>· Dispatcher 总调度并行扇出每位患者的随访 Episode</div>
            <div>· 每位患者 Agent 独立完成 消息发送 → 回复获取 → 风险评分 → 分流</div>
            <div>· 手动患者等待人工回复，自动患者即时推进</div>
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
  agentState: { type: Object, default: () => ({ name: 'idle', label: '' }) },
  steps: { type: Array, default: () => [] },
  progress: { type: Object, default: () => ({ current: 0, total: 0, phase: '', detail: {} }) },
  logs: { type: Array, default: () => [] },
  filterResult: { type: Object, default: null },
  executionProgress: { type: Object, default: () => ({ total: 0, started: 0, waiting: 0, scored: 0, reviewed: 0, reported: 0 }) },
})

// 调度进行中的状态显示"思考中"卡片
const agentThinking = computed(() => {
  const name = props.agentState.name
  return ['loading_inputs', 'calculating_base_roster', 'applying_callback_policy',
          'dispatching', 'following_up', 'waiting_patients'].includes(name)
})

const stateBadgeClass = computed(() => {
  const name = props.agentState.name
  return !name || name === 'idle' || name === 'created'
    ? 'bg-gray-100 text-gray-500 border-gray-300'
    : name === 'failed' || name === 'error'
      ? 'bg-red-50 text-red-600 border-red-200'
      : 'bg-blue-50 text-blue-600 border-blue-200'
})

const stateBadgeText = computed(() => {
  const name = props.agentState.name
  if (!name || name === 'idle' || name === 'created') return '空闲'
  if (name === 'failed' || name === 'error') return '异常'
  return (props.agentState.label || name)
})

const doneSteps = computed(() => props.steps.filter(s => s.status === 'done').length)

const GROUP_TITLES = {
  dispatch: '一、调度编排',
  episode: '二、患者 Episode 并行随访',
  finalize: '三、患者终态处理',
  aggregate: '四、批次汇总',
}

function groupTitle(group) {
  return GROUP_TITLES[group] || group || '执行流程'
}

function isFirstInGroup(step, index) {
  return index === 0 || props.steps[index - 1]?.group !== step.group
}

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
  load: 'list',
  roster: 'search',
  policy: 'notification',
  episodes: 'upload',
  followup: 'settings',
  risk: 'list',
  persist: 'save',
  review: 'filePaste',
  report: 'filePaste',
  aggregate: 'right',
  // 旧步骤键（过渡期兼容）
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
