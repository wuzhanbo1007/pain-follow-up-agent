<template>
  <!-- 加载状态 -->
  <div v-if="loading" class="lan-app w-screen h-screen flex items-center justify-center bg-white">
    <div class="text-center">
      <div class="text-5xl mb-4 animate-pulse">🏥</div>
      <div class="text-gray-500 text-lg">正在连接后端服务...</div>
      <div class="text-gray-400 text-sm mt-2">确保已启动 python app.py</div>
    </div>
  </div>

  <!-- 主界面 -->
  <div v-else class="lan-app w-screen h-screen flex flex-col bg-gray-50 text-gray-800 overflow-hidden">
    <div
      v-if="activeDoctorAlert"
      class="fixed right-6 top-20 z-50 w-[360px] rounded-xl border-2 border-red-300 bg-red-50 p-4 shadow-2xl"
      role="alert"
    >
      <div class="flex items-start gap-3">
        <span class="text-2xl">⚠️</span>
        <div class="min-w-0 flex-1">
          <div class="font-bold text-red-800">即时人工预警</div>
          <div class="mt-1 text-sm font-semibold text-red-900">
            {{ activeDoctorAlert.message || `${activeDoctorAlert.patient_name || '患者'}情绪不稳定，需要人工介入` }}
          </div>
          <div v-if="activeDoctorAlert.reason" class="mt-2 text-xs leading-5 text-red-700">
            触发原因：{{ activeDoctorAlert.reason }}
          </div>
          <button
            class="mt-3 rounded-md bg-red-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-red-700"
            @click="store.dismissDoctorAlert()"
          >
            我已知晓
          </button>
        </div>
      </div>
    </div>
    <!-- 顶部标题栏 -->
    <header class="lan-header bg-white border-b border-gray-200 flex items-center justify-between shrink-0">
      <div class="flex items-center gap-3 min-w-0">
        <img :src="lanLogo" class="lan-brand-logo" alt="LANShing" />
        <div class="lan-header-title-group min-w-0">
          <h1 class="lan-header-title text-gray-800">LANShing疼痛智能随访系统</h1>
          <span class="lan-header-version">V1.0</span>
        </div>
      </div>
      <div class="lan-header-meta flex items-center gap-4 text-xs text-gray-500">
        <div class="flex items-center gap-1.5">
          <span class="w-2 h-2 rounded-full" :class="wsConnected ? 'bg-green-500' : 'bg-red-400'"></span>
          <span>{{ wsConnected ? 'WebSocket 已连接' : 'WebSocket 断开' }}</span>
        </div>
        <span v-if="followupStarted">患者: {{ allPatients.length }} 人</span>
        <span v-if="!followupStarted">⏳ 待拉取随访名单</span>
        <span>{{ currentDate }}</span>
      </div>
    </header>

    <!-- 顶部 Tab 切换（v3.0 四：三种工作模式 — 按业务流程顺序） -->
    <nav class="lan-main-tabs shrink-0 bg-white border-b border-gray-200 flex items-center gap-0">
      <button
        v-for="t in tabs" :key="t.key" @click="mode = t.key"
        class="lan-main-tab text-sm border-b-2 -mb-px transition-colors"
        :class="mode === t.key
          ? 'border-blue-500 text-blue-600'
          : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'"
      >{{ t.label }}</button>
    </nav>

    <!-- 模式一：随访计划审阅（PlanReviewPanel，需求一 — 业务流程第一步） -->
    <PlanReviewPanel
      v-show="mode === 'plan'"
      class="flex-1 min-h-0"
      :patients="allPatients"
      :api-base="API_BASE"
      :can-review="!!finalStats"
    />

    <!-- 模式二：随访执行（沿用 v2.0 布局 — 业务流程第二步） -->
    <div v-show="mode === 'execution'" class="flex-1 flex overflow-hidden">
      <!-- 左侧：PainSmart 面板（3/4） -->
      <div class="flex-[3] p-4 min-w-0">
        <DonePhase
          :stats="store.stats"
          :logs="store.logs"
          :final-stats="finalStats"
          :filter-result="filterResult"
          :alerts="store.alerts"
          :is-running="isRunning"
          :all-patients="allPatients"
        />
      </div>

      <!-- 右侧：决策可视化 + 随访控制 -->
      <div class="flex-[1] p-4 pl-0 min-w-0">
        <div class="glass-panel h-full overflow-hidden flex flex-col">
          <div class="flex-1 overflow-hidden">
            <AgentThinking
              :agent-state="store.agentState"
              :steps="store.steps"
              :progress="store.progress"
              :logs="store.logs"
              :filter-result="filterResult"
              :execution-progress="store.executionProgress"
              :generated-messages="generatedMessages"
            />
          </div>
          <div class="shrink-0 border-t border-gray-200">
            <div class="px-5 py-3 border-b border-gray-100 flex items-center justify-between bg-gray-50">
              <div class="flex items-center gap-2">
                <LanIcon name="settings" />
                <span class="text-sm font-semibold text-gray-700">随访控制</span>
              </div>
              <div class="w-2 h-2 rounded-full" :class="isRunning ? 'bg-green-500 animate-pulse' : 'bg-gray-300'"></div>
            </div>
            <ControlPanel
              :agent-state="store.agentState"
              :is-running="isRunning"
              @start-followup="handleStartFollowup"
              @reset="handleReset"
            />
          </div>
        </div>
      </div>
    </div>

    <!-- 模式三：随访过程 Review（FollowUpReviewPanel，需求二 — 业务流程第三步） -->
    <FollowUpReviewPanel
      ref="reviewPanel"
      v-show="mode === 'review'"
      class="flex-1 min-h-0"
      :patients="allPatients"
      :api-base="API_BASE"
      :dispatch-id="dispatch.dispatch_id"
      :can-review="followupStarted || !!finalStats"
    />

    <!-- 左下角微信浮窗（始终保留） -->
    <WechatFloatButton />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useDemoStore } from '../stores/demoStore'
import WechatFloatButton from '../components/WechatFloatButton.vue'
import AgentThinking from '../components/AgentThinking.vue'
import ControlPanel from '../components/ControlPanel.vue'
import DonePhase from '../components/DonePhase.vue'
import PlanReviewPanel from '../components/PlanReviewPanel.vue'
import FollowUpReviewPanel from '../components/FollowUpReviewPanel.vue'
import LanIcon from '../components/LanIcon.vue'
import lanLogo from '../assets/lan-logo-light-gradient.png'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:5000'

// ---------- 顶部 Tab（v3.0 四：三种工作模式 — 按业务流程排序） ----------
const mode = ref('plan') // 'plan' | 'execution' | 'review'
const tabs = [
  { key: 'plan', label: '随访计划审阅' },
  { key: 'execution', label: '随访执行' },
  { key: 'review', label: '随访过程审阅' },
]
// Review 面板实例
const reviewPanel = ref(null)
// 切换到"随访过程审阅"时，如果随访已经开始，自动加载最新数据（无需手动点刷新）。
// WebSocket 事件（review:ready）也会实时推送，双重保障。
watch(mode, (m) => {
  if (m !== 'review') return
  if (followupStarted.value || finalStats.value) reviewPanel.value?.refresh()
  else reviewPanel.value?.resetState()
})

// ---------- 状态（全部来自 store，唯一 WebSocket 连接由 store.initSocket 管理） ----------
const store = useDemoStore()
const {
  wsConnected, allPatients, followupStarted,
  filterResult, finalStats, generatedMessages, isRunning,
  activeDoctorAlert, dispatch,
} = storeToRefs(store)
const loading = ref(true)

// 当前日期
const currentDate = computed(() => {
  return new Date().toLocaleDateString('zh-CN')
})

// ---------- 初始化 ----------
onMounted(() => {
  // WebSocket 单例与患者列表由入口（main.js）的 store.initSocket() 建立，
  // 这里只关掉加载态，避免重复连接 / 重复接线导致事件被处理两次。
  loading.value = false
})

// ---------- 动作 ----------
function handleStartFollowup() {
  if (isRunning.value) return
  // 内部 reset() 并发送 dispatch:start（说明书 13）；已发送人数
  // 由 store 按 dispatch:roster_ready / episode:message 动态记录。
  store.startDispatch()
}

function handleReset() {
  store.reset()
  reviewPanel.value?.resetState()
}
</script>
