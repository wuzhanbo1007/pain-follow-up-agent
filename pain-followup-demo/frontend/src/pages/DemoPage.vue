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
    />

    <!-- 左下角微信浮窗（始终保留） -->
    <WechatFloatButton />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useDemoStore } from '../stores/demoStore'
import { io } from 'socket.io-client'
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
// WebSocket 事件（review:session_ready）也会实时推送，双重保障。
watch(mode, (m) => {
  if (m === 'review' && followupStarted.value) {
    reviewPanel.value?.refresh()
  }
})

// ---------- 状态 ----------
const store = useDemoStore()
const isRunning = ref(false)
const generatedMessages = ref([])
const filterResult = ref(null)
const wsConnected = ref(false)
const loading = ref(true)
const allPatients = ref([])
const replyMap = ref({})
const phase = ref('idle') // 'idle' | 'running' | 'done'
const finalStats = ref(null)
const followupStarted = ref(false) // 拉取今日随访名单后变为 true

// generatedMessages 的可变内部引用
const generatedRef = []

// 当前日期
const currentDate = computed(() => {
  return new Date().toLocaleDateString('zh-CN')
})

// WebSocket 引用
let socket = null

// ---------- 初始化 ----------
onMounted(async () => {
  try {
    const [pRes, rRes] = await Promise.all([
      fetch(`${API_BASE}/api/patients`).then(r => r.json()),
      fetch(`${API_BASE}/api/replies`).then(r => r.json()),
    ])
    allPatients.value = pRes
    replyMap.value = rRes
  } catch (e) {
    console.error('API加载失败:', e)
  } finally {
    loading.value = false
  }

  // 建立 WebSocket
  socket = io(API_BASE, {
    transports: ['websocket', 'polling'],
    reconnection: true,
  })

  socket.on('connect', () => {
    wsConnected.value = true
    console.log('[WS] 已连接')
  })

  socket.on('disconnect', () => {
    wsConnected.value = false
  })

  socket.on('agent:state_change', (data) => {
    store.handleStateChange(data)
    // 从 IDLE 进入 FETCHING 时标记随访已开始，显示患者数量
    if (data.from === 'IDLE' && data.to !== 'IDLE') {
      followupStarted.value = true
    }
    // 如果页面打开时随访已经在进行中（已过 IDLE 阶段），也显示患者数量
    if (!followupStarted.value && data.to && !['IDLE', 'FETCHING'].includes(data.to)) {
      followupStarted.value = true
    }
  })

  socket.on('agent:log', (data) => {
    store.addLog({ ...data, id: Date.now() + Math.random() })
  })

  socket.on('agent:filter_result', (data) => {
    filterResult.value = data
    store.stats = {
      ...store.stats,
      skip_count: data.skip_count || 0,
      send_count: data.send_count || 0,
    }
  })

  // 计划生成后重新拉患者列表，让 followup_detail.frequency 刷新为 LLM 推断值
  socket.on('plan:drafted', async () => {
    try {
      const r = await fetch(`${API_BASE}/api/patients`).then(r => r.json())
      allPatients.value = r
    } catch (e) { console.error('[plan:drafted] 刷新患者列表失败', e) }
  })

  socket.on('agent:progress', (data) => {
    store.handleProgress(data)
    if (data.phase === 'sending') {
      store.stats = { ...store.stats, sent_count: data.current }
    }
    if (data.phase === 'generating') {
      store.stats = { ...store.stats, total_patients: data.total }
    }
  })

  socket.on('agent:tool_call', (data) => {
    if (data.tool === 'generate_personalized_message' && data.status === 'end') {
      const res = data.result || {}
      const item = {
        patient_id: res.patient_id || data.params?.patient?.patient_id,
        patient_name: res.patient_name || data.params?.patient?.name,
        message: res.message || '',
        basis: res.generation_basis || {},
      }
      generatedRef.push(item)
      generatedMessages.value = [...generatedRef]
    }
  })

  socket.on('agent:wechat_sent', (data) => {
    store.addMessage({
      type: 'sent',
      patient_id: data.patient_id,
      patient_name: data.patient_name,
      content: data.message,
    })
  })

  socket.on('agent:clarification', (data) => {
    store.addMessage({
      type: 'sent',
      patient_id: data.patient_id,
      patient_name: data.patient_name,
      content: data.message,
    })
  })

  socket.on('patient:reply', (data) => {
    store.addMessage({
      type: 'reply',
      patient_id: data.patient_id,
      patient_name: data.patient_name || '',
      content: data.text,
    })
    store.stats = { ...store.stats, replied_count: store.stats.replied_count + 1 }
  })

  socket.on('agent:risk_scored', (data) => {
    const s = { ...store.stats }
    if (data.level === 'high') {
      s.high_risk_count = (s.high_risk_count || 0) + 1
    } else if (data.level === 'medium') {
      s.medium_risk_count = (s.medium_risk_count || 0) + 1
    } else {
      s.low_risk_count = (s.low_risk_count || 0) + 1
    }
    store.stats = s

    // 所有已评分患者都进入"风险评分结果"列表（含高危），
    // 确保马淑珍这类重点患者也能在页面上完整展示，而不是被刻意排除。
    store.addAlert({
      patient_id: data.patient_id,
      patient_name: data.patient_name,
      doctor_name: data.doctor_name || '',
      risk_level: data.level,
      risk_score: data.total_score,
      nrs: data.nrs,
      reason: data.reason || '',
      timestamp: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
    })
  })

  socket.on('doctor:alert', (data) => {
    // 只更新预警计数，不再重复添加到 alerts（agent:risk_scored 已添加该患者）
    store.stats = { ...store.stats, alert_count: store.stats.alert_count + 1 }
  })

  socket.on('doctor:callback_alert', (data) => {
    store.addAlert({ ...data, risk_level: 'callback' })
    if (data.type === 'no_reply') {
      store.stats = { ...store.stats, no_reply_count: (store.stats.no_reply_count || 0) + 1 }
    }
  })

  socket.on('agent:final_stats', (data) => {
    console.log('[Demo] 收到最终统计:', data)
    finalStats.value = data
    // 用最终统计覆盖面板顶部计数（sent_count / no_reply_count 等）
    store.stats = { ...store.stats, ...data }
    phase.value = 'done'
    store.markAllStepsDone()
  })

  socket.on('agent:reset', () => {
    generatedRef.length = 0
    generatedMessages.value = []
    filterResult.value = null
    isRunning.value = false
    phase.value = 'idle'
    finalStats.value = null
    followupStarted.value = false
    // 新一轮随访启动时，归零审阅面板统计与列表
    reviewPanel.value?.resetState()
  })

  socket.on('agent:connected', (data) => {
    console.log('[WS] Agent 就绪，患者数:', data.total_patients)
  })
})

onUnmounted(() => {
  if (socket) {
    socket.disconnect()
    socket = null
  }
})

// ---------- 动作 ----------
function sendWs(event, data = {}) {
  socket?.emit(event, data)
}

function handleStartFollowup() {
  if (isRunning.value) return
  isRunning.value = true
  phase.value = 'running'
  finalStats.value = null
  store.reset()
  generatedRef.length = 0
  generatedMessages.value = []
  filterResult.value = null
  sendWs('demo:start_followup', { speed: 1.0 })
}

function handleReset() {
  isRunning.value = false
  phase.value = 'idle'
  finalStats.value = null
  generatedRef.length = 0
  generatedMessages.value = []
  filterResult.value = null
  store.reset()
  sendWs('demo:reset')
}
</script>
