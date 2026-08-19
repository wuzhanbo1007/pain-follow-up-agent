import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { connectWebSocket, send } from '../composables/useWebSocket'

export const STEP_STATUS = {
  PENDING: 'pending',
  ACTIVE: 'active',
  DONE: 'done',
  ERROR: 'error',
  SKIPPED: 'skipped',
}

export const AGENT_STEPS = [
  { id: 1, key: 'load', group: 'dispatch', label: '读取患者、计划与历史数据', icon: '📋' },
  { id: 2, key: 'roster', group: 'dispatch', label: '计算应随访 / 免随访名单', icon: '🔍' },
  { id: 3, key: 'policy', group: 'dispatch', label: '应用回访策略并确定渠道', icon: '⚙️' },
  { id: 4, key: 'episodes', group: 'dispatch', label: '创建患者随访 Episode', icon: '📤' },
  { id: 5, key: 'followup', group: 'episode', label: '发送消息并进行多轮对话', icon: '💬' },
  { id: 6, key: 'risk', group: 'finalize', label: '对话结束后计算最终风险', icon: '📊' },
  { id: 7, key: 'persist', group: 'finalize', label: '保存随访会话与风险结果', icon: '💾' },
  { id: 8, key: 'review', group: 'finalize', label: '生成 AI 审阅结果', icon: '🩺' },
  { id: 9, key: 'report', group: 'finalize', label: '生成患者随访报告', icon: '📄' },
  { id: 10, key: 'aggregate', group: 'aggregate', label: '汇总本次随访结果', icon: '✅' },
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
  unassessed_count: 0,
}

const STATE_STEP_MAP = {
  FETCHING: 'load',
  FILTERING: 'roster',
  GENERATING: 'followup',
  SENDING: 'followup',
  WAITING_REPLY: 'followup',
  FOLLOWING_UP: 'followup',
  PARSING: 'followup',
  SCORING: 'risk',
  ROUTING: 'review',
  ALERTING: 'report',
  LOGGING: 'aggregate',
}

// 新架构总调度状态 → 前端执行步骤
const DISPATCH_STEP_MAP = {
  loading_inputs: 'load',
  calculating_base_roster: 'roster',
  applying_callback_policy: 'policy',
  dispatching: 'episodes',
  waiting_patients: 'followup',
  completed: 'aggregate',
}

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:5000'

export const useDemoStore = defineStore('demo', () => {
  const agentState = ref({ name: 'IDLE', label: '空闲，等待触发' })
  const steps = ref(AGENT_STEPS.map(s => ({ ...s, status: STEP_STATUS.PENDING })))
  const logs = ref([])
  const stats = ref({ ...INITIAL_STATS })
  // 本轮已经成功发送过首条随访消息的患者，按 patient_id 去重。
  // sent_count 表示“已发送患者数”，不是消息条数。
  const sentPatientIds = ref({})
  const messages = ref([])
  const alerts = ref([])
  const activeDoctorAlert = ref(null)
  const progress = ref({ current: 0, total: 0, phase: '', detail: {} })
  const filterResult = ref(null)
  const currentPatientDetail = ref(null)
  const replyPatients = ref([])

  // ---- 新架构状态（dispatch:* / episode:* / sync:projection） ----
  const wsConnected = ref(false)
  const followupStarted = ref(false)
  const finalStats = ref(null)
  const phase = ref('idle') // 'idle' | 'running' | 'done'
  const generatedMessages = ref([])
  const allPatients = ref([])
  const demoPatients = ref([])
  const waitingEpisodes = ref([])
  const dispatch = ref({ status: 'idle', dispatch_id: '', counts: {} })
  // Episode 是并行执行的，执行步骤展示的是批次阶段，以下计数用于避免
  // 用第一个完成的患者错误地代表整批患者已经完成。
  const executionProgress = ref({
    total: 0, started: 0, waiting: 0, scored: 0,
    persisted: 0, reviewed: 0, reported: 0,
  })
  const episodeProgressById = ref({})

  const isRunning = computed(() => phase.value === 'running')
  const doneStepsCount = computed(() => steps.value.filter(s => s.status === 'done').length)

  function setStepStatus(stepKey, status) {
    steps.value = steps.value.map(step =>
      step.key === stepKey ? { ...step, status } : step
    )
  }

  function activateStage(stepKey) {
    const index = AGENT_STEPS.findIndex(step => step.key === stepKey)
    if (index < 0) return
    steps.value = steps.value.map((step, stepIndex) => {
      if (step.key === stepKey) return { ...step, status: STEP_STATUS.ACTIVE }
      if (stepIndex < index && step.status !== STEP_STATUS.ERROR) {
        return { ...step, status: STEP_STATUS.DONE }
      }
      return step
    })
  }

  function refreshEpisodeProgress() {
    const items = Object.values(episodeProgressById.value)
    const count = (predicate) => items.filter(predicate).length
    executionProgress.value = {
      total: Number(filterResult.value?.send_count || items.length || 0),
      started: count(item => ['running', 'waiting_human', 'completed', 'human_handoff', 'incomplete_handoff'].includes(item.status)),
      waiting: count(item => item.status === 'waiting_human'),
      scored: count(item => item.scored),
      persisted: count(item => item.persisted),
      reviewed: count(item => item.reviewed),
      reported: count(item => item.reported),
    }
  }

  // ================= 基础写入 =================
  function addLog(data) {
    const logKey = data.log_key || (
      data.episode_id && data.turn_no != null
        ? `decision:${data.episode_id}:${data.turn_no}`
        : ''
    )
    // 同一条决策会同时通过 episode:decision 和兼容事件 agent:log 到达，去重。
    if (logKey && logs.value.some(item => item.logKey === logKey)) return
    logs.value = [...logs.value, {
      message: data.message || formatDecisionLog(data),
      timestamp: data.timestamp || new Date().toLocaleTimeString('zh-CN'),
      state: data.state,
      logKey,
      id: Date.now() + Math.random(),
    }]
  }

  function formatDecisionLog(data) {
    const decision = data.decision || {}
    const actionLabels = {
      clarify: '继续追问',
      continue_wait: '继续等待回复',
      complete: '完成随访',
      incomplete_handoff: '信息不完整，转人工',
      emergency_handoff: '紧急转人工',
    }
    const action = actionLabels[decision.action] || decision.action || '处理中'
    const patient = data.patient_name || `患者${data.patient_id || ''}`
    const reason = decision.reason || '未提供原因'
    return `第${data.turn_no || '?'}轮 · ${patient} · ${action} · 原因：${reason}`
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

  function markAllStepsDone() {
    steps.value = steps.value.map(s => ({ ...s, status: STEP_STATUS.DONE }))
  }

  function activateStep(stepKey) {
    if (!stepKey) return
    const idx = AGENT_STEPS.findIndex(s => s.key === stepKey)
    steps.value = steps.value.map(s => {
      const i = AGENT_STEPS.findIndex(x => x.key === s.key)
      if (s.key === stepKey) return { ...s, status: STEP_STATUS.ACTIVE }
      if (i >= 0 && idx >= 0 && i < idx) return { ...s, status: STEP_STATUS.DONE }
      return s
    })
  }

  // ================= 已发送人数（核心需求） =================
  // 一个患者发送完首条消息后动态记录（按 patient_id 去重）；
  // 已发送人数最多 = 当日应随访人数（send_count），不会超过它。
  function markPatientSent(patientId) {
    const key = patientId == null ? '' : String(patientId)
    if (!key || sentPatientIds.value[key]) return

    const next = { ...sentPatientIds.value, [key]: true }
    sentPatientIds.value = next

    const uniqueSentCount = Object.keys(next).length
    const rosterCount = Number(stats.value.send_count || 0)
    stats.value = {
      ...stats.value,
      sent_count: rosterCount > 0
        ? Math.min(uniqueSentCount, rosterCount)
        : uniqueSentCount,
    }
  }

  // dispatch:roster_ready → 应随访人数（上限）＋ 重置本轮已发送
  function setRoster(d) {
    const sendList = d.send_roster || d.send_list || []
    const skipList = d.skip_roster || d.skip_list || []
    const details = d.details || {}
    const skipDetails = skipList.map(patient => {
      const detail = details[patient.patient_id] || details[String(patient.patient_id)] || {}
      return {
        ...patient,
        ...detail,
        reason: detail.reason || patient.skip_reason || '规则判定免随访',
      }
    })
    filterResult.value = {
      total: Number(d.total ?? sendList.length + skipList.length),
      send_count: Number(d.send_count ?? d.counts?.send_count ?? sendList.length),
      skip_count: Number(d.skip_count ?? d.counts?.skip_count ?? skipList.length),
      send_list: sendList,
      skip_list: skipList,
      skip_details: d.skip_details || skipDetails,
      details,
    }
    // 微信端只展示人工模拟患者；自动患者的消息进入决策日志。
    const manualIds = sendList
      .filter(patient => patient.input_source === 'human' && !patient.phone_callback)
      .map(patient => String(patient.patient_id))
    demoPatients.value = [...new Set([
      ...demoPatients.value.map(id => String(id)),
      ...manualIds,
    ])]
    stats.value = {
      ...stats.value,
      send_count: Number(d.send_count ?? d.counts?.send_count ?? sendList.length),
      skip_count: Number(d.skip_count ?? d.counts?.skip_count ?? skipList.length),
      sent_count: 0,
    }
    executionProgress.value = {
      // 电话回访患者不创建 Episode，不参与微信对话/风险评分进度。
      total: sendList.filter(patient => !patient.phone_callback).length,
      started: 0, waiting: 0, scored: 0, persisted: 0, reviewed: 0, reported: 0,
    }
    episodeProgressById.value = {}
    sentPatientIds.value = {}
  }

  // episode:message → 动态记录：护士消息记“已发送”，患者回复记“已回复”
  function onEpisodeMessage(d) {
    const role = d.role
    const roleLabel = role === 'nurse' ? '随访助手' : (d.patient_name || `患者${d.patient_id}`)
    addLog({
      message: `${roleLabel}：${d.content || ''}`,
      timestamp: d.timestamp,
      state: 'conversation',
      log_key: `message:${d.episode_id}:${d.turn_no}:${role}`,
    })
    if (role === 'nurse') {
      addMessage({
        type: 'sent',
        patient_id: d.patient_id,
        patient_name: d.patient_name,
        content: d.content,
      })
      markPatientSent(d.patient_id)
    } else {
      addMessage({
        type: 'reply',
        patient_id: d.patient_id,
        patient_name: d.patient_name || '',
        content: d.content,
      })
      stats.value = { ...stats.value, replied_count: stats.value.replied_count + 1 }
    }
  }

  // ================= 事件处理器 =================
  function handleConnectionReady() {
    wsConnected.value = true
  }

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

  function handleProgress(data) {
    progress.value = {
      ...data,
      current: data.current ?? progress.value.current,
      total: data.total ?? progress.value.total,
    }
    if (data.phase === 'filtering' && data.detail) {
      filterResult.value = data.detail
    }
  }

  function handleRiskScored(data) {
    const episodeId = data.episode_id || `patient:${data.patient_id}`
    episodeProgressById.value = {
      ...episodeProgressById.value,
      [episodeId]: {
        ...(episodeProgressById.value[episodeId] || {}),
        patient_id: data.patient_id,
        scored: true,
      },
    }
    refreshEpisodeProgress()
    // 风险节点按患者并行到达；先只激活本节点，不提前把仍在对话的
    // 其他患者所在的 followup 阶段标记为完成。
    setStepStatus('risk', STEP_STATUS.ACTIVE)
    if (executionProgress.value.total > 0 && executionProgress.value.scored >= executionProgress.value.total) {
      setStepStatus('followup', STEP_STATUS.DONE)
      setStepStatus('risk', STEP_STATUS.DONE)
      setStepStatus('persist', STEP_STATUS.ACTIVE)
    }
    const s = { ...stats.value }
    if (data.level === 'high') {
      s.high_risk_count = (s.high_risk_count || 0) + 1
    } else if (data.level === 'medium') {
      s.medium_risk_count = (s.medium_risk_count || 0) + 1
    } else {
      s.low_risk_count = (s.low_risk_count || 0) + 1
    }
    stats.value = s
    addAlert({
      patient_id: data.patient_id,
      patient_name: data.patient_name,
      doctor_name: data.doctor_name || '',
      risk_level: data.level,
      risk_score: data.total_score,
      nrs: data.nrs,
      reason: data.reason || '',
      timestamp: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
    })
    addLog({
      message: `${data.patient_name || `患者${data.patient_id}`}：对话结束，最终风险评分 ${data.total_score ?? '—'} 分（${riskLabel(data.level)}）`,
      state: 'final_risk',
      log_key: `final-risk:${data.episode_id || data.patient_id}`,
    })
  }

  function riskLabel(level) {
    return level === 'high' ? '高风险' : level === 'medium' ? '中风险' : level === 'low' ? '低风险' : '未知'
  }

  function handleDoctorAlert(data) {
    stats.value = { ...stats.value, alert_count: stats.value.alert_count + 1 }
    if (data?.alert_type === 'emotional_instability' || data?.requires_immediate_action) {
      activeDoctorAlert.value = {
        ...data,
        id: Date.now() + Math.random(),
      }
      addLog({
        message: data.message || `${data.patient_name || `患者${data.patient_id}`}情绪不稳定，需要人工介入`,
        state: 'doctor_alert',
        log_key: `doctor-alert:${data.episode_id || data.patient_id}:${data.turn_no || Date.now()}`,
      })
    }
  }

  function dismissDoctorAlert() {
    activeDoctorAlert.value = null
  }

  function handleCallbackAlert(data) {
    addAlert({ ...data, risk_level: 'callback' })
    // 电话回访患者不创建普通 Episode，但后端已经将回访提醒入队；
    // 该患者也必须计入本轮“已发送”人数。
    markPatientSent(data.patient_id)
    if (data.type === 'no_reply') {
      stats.value = { ...stats.value, no_reply_count: (stats.value.no_reply_count || 0) + 1 }
    }
  }

  function handlePatientReply(data) {
    addMessage({
      type: 'reply',
      patient_id: data.patient_id,
      patient_name: data.patient_name || '',
      content: data.text,
    })
    stats.value = { ...stats.value, replied_count: stats.value.replied_count + 1 }
  }

  function handleDemoPatientsSelected(d) {
    demoPatients.value = d.demo_patient_ids || []
  }

  // 随访收尾：合并当前已累计计数，已发送仍然封顶于应随访人数。
  // 后端 report_ready 现在带患者级统计（high_risk_focus 等），优先取后端权威值，
  // 本地累计只作兜底（晚连/刷新后本地计数为 0，不能用它覆盖真实结果）。
  function finalize(data) {
    // 新事件使用 { counts, reports_by_episode }，旧事件则直接把统计字段
    // 放在顶层。合并两层，避免取 counts 时丢掉外层的结果明细。
    const d = data && data.counts
      ? { ...data, ...data.counts }
      : (data || {})
    const s = { ...stats.value }
    const merged = {
      ...d,
      send_count: Number(d.send_count ?? s.send_count ?? 0),
      sent_count: Number(d.sent_count ?? s.sent_count ?? 0),
      high_risk_count: Number(d.high_risk_count ?? s.high_risk_count ?? 0),
      medium_risk_count: Number(d.medium_risk_count ?? s.medium_risk_count ?? 0),
      low_risk_count: Number(d.low_risk_count ?? s.low_risk_count ?? 0),
      callback_count: Number(d.callback_count ?? 0),
      alert_count: Number(d.alert_count ?? s.alert_count ?? 0),
      no_reply_count: Number(d.no_reply_count ?? s.no_reply_count ?? 0),
      unassessed_count: Number(d.unassessed_count ?? d.no_reply_count ?? 0),
      skip_count: Number(d.skip_count ?? s.skip_count ?? 0),
    }

    // 最终统计必须等到总调度器确认所有分发 Agent 都有终态报告。
    // 仅用“已评分 + 未回复”不能判断完成，因为仍在等待人工回复的患者
    // 也可能被错误地算进总人数。
    const totalFollowup = Number(
      merged.total_followup ?? merged.total_agents ?? merged.send_count ?? 0,
    )
    const totalAgents = Number(merged.total_agents ?? 0)
    const unfinished = Number(merged.unfinished ?? 0)
    const reportCount = Object.keys(merged.reports_by_episode || {}).length
    if (d.status !== 'completed') return
    if (
      totalFollowup > 0 &&
      (unfinished > 0 || totalAgents < totalFollowup || reportCount < totalFollowup)
    ) return

    const rosterCount = merged.send_count
    // dispatch 完成代表本轮应随访名单的发送流程已经收尾；电话回访患者
    // 没有普通 Episode，但仍算已发送，因此最终人数与应随访人数保持一致。
    merged.sent_count = rosterCount > 0 ? rosterCount : merged.sent_count
    finalStats.value = merged
    stats.value = { ...s, ...merged }
    phase.value = 'done'
    markAllStepsDone()
  }

  function handleFinalStats(data) { finalize(data) }
  function handleDispatchReportReady(data) { finalize(data) }

  function handleDispatchStarted(d) {
    followupStarted.value = true
    phase.value = 'running'
    agentState.value = { name: 'FETCHING', label: '拉取今日随访名单' }
    dispatch.value = { ...dispatch.value, dispatch_id: d.dispatch_id || '', status: 'running' }
    addLog({
      message: `开始拉取今日随访患者名单（调度 ${d.dispatch_id || '当前批次'}）`,
      state: 'loading_inputs',
      log_key: `dispatch-start:${d.dispatch_id || 'current'}`,
    })
  }

  function handleDispatchStateChanged(d) {
    const status = d.status || ''
    dispatch.value = { ...dispatch.value, dispatch_id: d.dispatch_id || dispatch.value.dispatch_id, status }
    if (d.counts && d.counts.send_count) {
      stats.value = { ...stats.value, send_count: Number(d.counts.send_count) }
    }
    agentState.value = { name: status, label: status }
    activateStage(DISPATCH_STEP_MAP[status])
    const labels = {
      loading_inputs: '正在读取患者、计划和历史状态',
      calculating_base_roster: '正在按随访规则计算应访/免访名单',
      applying_callback_policy: '正在应用电话回访策略',
      dispatching: '名单已确定，正在为患者启动独立随访',
      completed: '本次调度已完成',
    }
    addLog({
      message: `调度过程：${labels[status] || status}`,
      state: status,
      log_key: `dispatch-state:${d.dispatch_id || dispatch.value.dispatch_id}:${status}`,
    })
  }

  function handleDispatchRosterReady(d) {
    setRoster(d)
    const sendList = d.send_roster || d.send_list || []
    const skipList = d.skip_roster || d.skip_list || []
    addLog({
      message: `名单判定完成：共 ${sendList.length + skipList.length} 人，应随访 ${sendList.length} 人，免随访 ${skipList.length} 人`,
      state: 'roster_ready',
      log_key: `roster-summary:${d.dispatch_id || 'current'}`,
    })
    const details = d.details || {}
    for (const patient of sendList) {
      const detail = details[patient.patient_id] || details[String(patient.patient_id)] || {}
      addLog({
        message: `${patient.name || `患者${patient.patient_id}`}(${patient.patient_id})：纳入随访，原因：${detail.reason || '命中随访计划/窗口'}，${patient.input_source === 'human' ? '手动模拟' : '自动模拟'}`,
        state: 'roster_decision',
        log_key: `roster-send:${d.dispatch_id || 'current'}:${patient.patient_id}`,
      })
    }
    for (const patient of skipList) {
      const detail = details[patient.patient_id] || details[String(patient.patient_id)] || {}
      addLog({
        message: `${patient.name || `患者${patient.patient_id}`}(${patient.patient_id})：免随访，原因：${detail.reason || patient.skip_reason || '规则判定免随访'}`,
        state: 'roster_decision',
        log_key: `roster-skip:${d.dispatch_id || 'current'}:${patient.patient_id}`,
      })
    }
  }

  function handleEpisodeStateChanged(d) {
    if (!d || !d.patient_id) return
    const episodeId = d.episode_id || `patient:${d.patient_id}`
    episodeProgressById.value = {
      ...episodeProgressById.value,
      [episodeId]: {
        ...(episodeProgressById.value[episodeId] || {}),
        patient_id: d.patient_id,
        status: d.status,
      },
    }
    refreshEpisodeProgress()
    if (['running', 'waiting_human'].includes(d.status)) {
      activateStage('followup')
    }
    const patient = allPatients.value.find(p => String(p.patient_id) === String(d.patient_id))
    const name = patient?.name || `患者${d.patient_id}`
    const statusLabels = {
      running: '开始处理',
      waiting_human: '等待手动回复',
      completed: '对话流程完成',
      human_handoff: '转人工处理',
      incomplete_handoff: '信息不完整，转人工',
    }
    addLog({
      message: `${name}(${d.patient_id})：${statusLabels[d.status] || d.status}`,
      state: d.status,
      log_key: `episode-state:${d.episode_id}:${d.status}`,
    })
  }

  function handleEpisodeDecision(data) {
    addLog(data)
  }

  function handleEpisodeMessage(d) { onEpisodeMessage(d) }

  function handleEpisodeWaitingHuman(d) {
    // 弹窗晚连会错过开场白/追问的 episode:message 广播（sync:projection 不重放消息）。
    // 若该患者还没有任何护士气泡，用 waiting_human.question 补齐，避免患者回复
    // 成为对话中第一条可见消息（"患者先发"的观感）。
    const hasNurseMsg = messages.value.some(
      m => String(m.patient_id) === String(d.patient_id) && m.type === 'sent')
    if (!hasNurseMsg && d.question) {
      addMessage({
        type: 'sent',
        patient_id: d.patient_id,
        patient_name: d.patient_name || '',
        content: d.question,
      })
    }
    waitingEpisodes.value = [
      ...waitingEpisodes.value.filter(e => e.episode_id !== d.episode_id),
      {
        episode_id: d.episode_id, patient_id: d.patient_id,
        question: d.question, thread_id: d.thread_id,
      },
    ]
  }

  function handleEpisodeReportReady(d) {
    if (!d || !d.patient_id) return
    const episodeId = d.episode_id || `patient:${d.patient_id}`
    episodeProgressById.value = {
      ...episodeProgressById.value,
      [episodeId]: {
        ...(episodeProgressById.value[episodeId] || {}),
        patient_id: d.patient_id,
        reported: true,
        persisted: true,
      },
    }
    refreshEpisodeProgress()
    if (executionProgress.value.total > 0 && executionProgress.value.reported >= executionProgress.value.total) {
      setStepStatus('report', STEP_STATUS.DONE)
      setStepStatus('aggregate', STEP_STATUS.ACTIVE)
    }
  }

  function handleReviewReady(d) {
    if (!d || !d.patient_id) return
    const episodeId = d.episode_id || `patient:${d.patient_id}`
    episodeProgressById.value = {
      ...episodeProgressById.value,
      [episodeId]: {
        ...(episodeProgressById.value[episodeId] || {}),
        patient_id: d.patient_id,
        reviewed: true,
        persisted: true,
      },
    }
    refreshEpisodeProgress()
    if (executionProgress.value.total > 0 && executionProgress.value.reviewed >= executionProgress.value.total) {
      setStepStatus('persist', STEP_STATUS.DONE)
      setStepStatus('review', STEP_STATUS.DONE)
      setStepStatus('report', STEP_STATUS.ACTIVE)
    }
  }

  function handleSyncProjection(d) {
    const counts = d.counts || {}
    // 微信页是独立窗口，连接建立后通过 projection 恢复本次完整应访名单。
    if (d.send_roster || d.skip_roster) {
      setRoster({
        ...d,
        details: d.roster_details || d.details || {},
      })
    }
    const rosterCount = counts.send_count ?? counts.total_followup ?? counts.total_agents
    if (rosterCount != null) {
      stats.value = { ...stats.value, send_count: Number(rosterCount) }
    }
    dispatch.value = { dispatch_id: d.dispatch_id || '', status: d.status || 'idle', counts }
    if (d.status && d.status !== 'idle') {
      followupStarted.value = true
      phase.value = d.status === 'completed' && finalStats.value ? 'done' : 'running'
    }
    // 晚连/刷新：projection 已含患者级统计，若调度已结束则直接生成最终统计
    if (d.status === 'completed' && !finalStats.value) {
      finalize(d)
    }
  }

  function handleAgentReset() { reset() }

  // ================= 动作 =================
  // 页面入口调用一次：建立全局唯一 WebSocket 连接并接线全部事件（说明书 15.3）。
  function initSocket() {
    connectWebSocket({
      handleConnectionReady,
      handleDispatchStarted,
      handleDispatchStateChanged,
      handleDispatchRosterReady,
      handleDispatchReportReady,
      handleEpisodeStateChanged,
      handleEpisodeDecision,
      handleEpisodeMessage,
      handleEpisodeWaitingHuman,
      handleEpisodeReportReady,
      handleReviewReady,
      handleSyncProjection,
      // 旧事件兼容（与新事件同源；消息类只走 episode:message，避免重复计数）
      handleStateChange,
      addLog,
      handleProgress,
      handleRiskScored,
      handlePatientReply,
      handleDoctorAlert,
      handleCallbackAlert,
      handleFinalStats,
      handleAgentReset,
      handleDemoPatientsSelected,
    })
    loadPatients()
  }

  async function loadPatients() {
    try {
      const r = await fetch(`${API_BASE}/api/patients`)
      allPatients.value = await r.json()
    } catch (e) {
      console.error('[store] 加载患者列表失败', e)
    }
  }

  function startDispatch() {
    reset()
    if (!wsConnected.value) {
      agentState.value = { name: 'ERROR', label: 'WebSocket 未连接，请稍后重试' }
      addLog({
        message: '启动随访失败：WebSocket 尚未连接',
        state: 'error',
        log_key: 'dispatch-start-not-connected',
      })
      return false
    }
    phase.value = 'running'
    const sent = send('dispatch:start', { scope: 'ward-A' })
    if (!sent) {
      phase.value = 'idle'
      agentState.value = { name: 'ERROR', label: '随访启动失败，请重试' }
      addLog({
        message: '启动随访失败：无法发送 dispatch:start',
        state: 'error',
        log_key: 'dispatch-start-send-failed',
      })
    }
    return sent
  }

  function resumeEpisode(episodeId, text) {
    return send('episode:resume', { episode_id: episodeId, text })
  }

  // 全部随访流程完成（含演示患者）后，将所有执行步骤标记为已完成（✓）
  function reset() {
    agentState.value = { name: 'IDLE', label: '空闲，等待触发' }
    steps.value = AGENT_STEPS.map(s => ({ ...s, status: STEP_STATUS.PENDING }))
    logs.value = []
    stats.value = { ...INITIAL_STATS }
    sentPatientIds.value = {}
    messages.value = []
    alerts.value = []
    activeDoctorAlert.value = null
    progress.value = { current: 0, total: 0, phase: '', detail: {} }
    filterResult.value = null
    currentPatientDetail.value = null
    finalStats.value = null
    phase.value = 'idle'
    followupStarted.value = false
    generatedMessages.value = []
    waitingEpisodes.value = []
    executionProgress.value = {
      total: 0, started: 0, waiting: 0, scored: 0,
      persisted: 0, reviewed: 0, reported: 0,
    }
    episodeProgressById.value = {}
  }

  return {
    agentState, steps, logs, stats, messages, alerts, activeDoctorAlert, progress,
    filterResult, currentPatientDetail, replyPatients,
    wsConnected, followupStarted, finalStats, phase, generatedMessages,
    allPatients, demoPatients, waitingEpisodes, dispatch,
    executionProgress, isRunning, doneStepsCount,
    handleStateChange, addLog, handleProgress, addMessage, addAlert,
    dismissDoctorAlert,
    handleEpisodeDecision,
    markPatientSent, reset, markAllStepsDone,
    initSocket, loadPatients, startDispatch, resumeEpisode,
  }
})
