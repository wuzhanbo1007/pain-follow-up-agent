import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export const STEP_STATUS = {
  PENDING: 'pending',
  ACTIVE: 'active',
  DONE: 'done',
  ERROR: 'error',
  SKIPPED: 'skipped',
}

export const AGENT_STEPS = [
  { id: 1, key: 'fetch', label: '拉取今日随访名单', icon: '📋' },
  { id: 2, key: 'filter', label: '过滤免随访患者', icon: '🔍' },
  { id: 3, key: 'generate', label: '生成个性化随访消息', icon: '🤖' },
  { id: 4, key: 'send', label: '批量发送微信消息', icon: '📤' },
  { id: 5, key: 'wait', label: '多轮智能追问', icon: '⏳' },
  { id: 6, key: 'parse', label: '解析患者自然语言回复', icon: '📝' },
  { id: 7, key: 'score', label: '计算风险评分', icon: '📊' },
  { id: 8, key: 'route', label: '风险分层路由', icon: '🔀' },
  { id: 9, key: 'alert', label: '推送预警 / 记录结果', icon: '🚨' },
]

const INITIAL_STATS = {
  total_patients: 0,
  skip_count: 0,
  send_count: 0,
  sent_count: 0,
  replied_count: 0,
  high_risk_count: 0,
  medium_risk_count: 0,
  low_risk_count: 0,
  alert_count: 0,
  no_reply_count: 0,
}

const STATE_STEP_MAP = {
  FETCHING: 'fetch',
  FILTERING: 'filter',
  GENERATING: 'generate',
  SENDING: 'send',
  WAITING_REPLY: 'wait',
  FOLLOWING_UP: 'wait',
  PARSING: 'parse',
  SCORING: 'score',
  ROUTING: 'route',
  ALERTING: 'alert',
  LOGGING: 'alert',
}

export const useDemoStore = defineStore('demo', () => {
  const agentState = ref({ name: 'IDLE', label: '空闲，等待触发' })
  const steps = ref(AGENT_STEPS.map(s => ({ ...s, status: STEP_STATUS.PENDING })))
  const logs = ref([])
  const stats = ref({ ...INITIAL_STATS })
  const messages = ref([])
  const alerts = ref([])
  const progress = ref({ current: 0, total: 0, phase: '', detail: {} })
  const filterResult = ref(null)
  const currentPatientDetail = ref(null)
  const replyPatients = ref([])

  function handleStateChange(data) {
    agentState.value = { name: data.to, label: data.to_label || '' }

    const stepKey = STATE_STEP_MAP[data.to]
    if (stepKey) {
      steps.value = steps.value.map(s => {
        if (s.key === stepKey) {
          return { ...s, status: STEP_STATUS.ACTIVE }
        }
        const stepKeys = Object.values(STATE_STEP_MAP)
        const currentIdx = stepKeys.indexOf(s.key)
        const activeIdx = stepKeys.indexOf(stepKey)
        if (currentIdx < activeIdx && currentIdx >= 0) {
          return { ...s, status: STEP_STATUS.DONE }
        }
        return s
      })
    }
    if (data.to === 'ERROR') {
      const activeStep = steps.value.find(s => s.status === STEP_STATUS.ACTIVE)
      if (activeStep) {
        steps.value = steps.value.map(s =>
          s.key === activeStep.key ? { ...s, status: STEP_STATUS.ERROR } : s
        )
      }
    }
  }

  function addLog(data) {
    logs.value = [...logs.value, {
      message: data.message,
      timestamp: data.timestamp,
      state: data.state,
      id: Date.now() + Math.random(),
    }]
  }

  function handleProgress(data) {
    progress.value = data
    if (data.phase === 'filtering' && data.detail) {
      filterResult.value = data.detail
    }
  }

  function addMessage(data) {
    messages.value = [...messages.value, {
      ...data,
      id: Date.now() + Math.random(),
      timestamp: data.timestamp || new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
    }]
  }

  function addAlert(data) {
    // 去重：同一患者只保留最新一条评分，防止后端重复推送
    const others = alerts.value.filter(a => a.patient_id !== data.patient_id)
    alerts.value = [{
      ...data,
      id: Date.now() + Math.random(),
    }, ...others]
  }

  // 全部随访流程完成（含演示患者）后，将所有执行步骤标记为已完成（✓）
  function markAllStepsDone() {
    steps.value = steps.value.map(s => ({ ...s, status: STEP_STATUS.DONE }))
  }

  function reset() {
    agentState.value = { name: 'IDLE', label: '空闲，等待触发' }
    steps.value = AGENT_STEPS.map(s => ({ ...s, status: STEP_STATUS.PENDING }))
    logs.value = []
    stats.value = { ...INITIAL_STATS }
    messages.value = []
    alerts.value = []
    progress.value = { current: 0, total: 0, phase: '', detail: {} }
    filterResult.value = null
    currentPatientDetail.value = null
  }

  const doneStepsCount = computed(() => steps.value.filter(s => s.status === 'done').length)

  return {
    agentState, steps, logs, stats, messages, alerts, progress,
    filterResult, currentPatientDetail, replyPatients,
    doneStepsCount,
    handleStateChange, addLog, handleProgress, addMessage, addAlert, reset,
    markAllStepsDone,
  }
})
