<template>
  <div class="lan-workspace h-full flex flex-col bg-gray-50 text-gray-800 overflow-hidden">
    <!-- 统计栏（F2.8） -->
    <div class="shrink-0 bg-white border-b border-gray-200 px-5 py-3 grid grid-cols-3 md:grid-cols-6 gap-3">
      <div class="stat-card">
        <div class="text-xs text-gray-400">总审阅</div>
        <div class="text-2xl font-bold text-gray-800 mt-1">{{ stats.total }}</div>
      </div>
      <div class="stat-card">
        <div class="text-xs text-gray-400">平均评分</div>
        <div class="text-2xl font-bold text-blue-600 mt-1">{{ stats.avg_score ?? '—' }}</div>
      </div>
      <div class="stat-card">
        <div class="text-xs text-gray-400">待跟踪</div>
        <div class="text-2xl font-bold text-gray-500 mt-1">{{ stats.pending_track }}</div>
      </div>
      <div class="stat-card">
        <div class="text-xs text-gray-400">跟踪中</div>
        <div class="text-2xl font-bold text-blue-600 mt-1">{{ stats.tracking }}</div>
      </div>
      <div class="stat-card">
        <div class="text-xs text-gray-400">需复诊</div>
        <div class="text-2xl font-bold text-amber-600 mt-1">{{ stats.need_revisit }}</div>
      </div>
      <div class="stat-card">
        <div class="text-xs text-gray-400">已解决</div>
        <div class="text-2xl font-bold text-green-600 mt-1">{{ stats.resolved }}</div>
      </div>
    </div>

    <!-- 工具栏 -->
    <div class="lan-toolbar shrink-0 bg-white border-b border-gray-200 px-5 py-3 flex flex-wrap items-center gap-3">
      <div class="lan-toolbar-title flex items-center gap-2 text-sm font-semibold text-gray-700"><LanIcon name="search" /><span>随访过程审阅</span></div>
      <div class="w-px h-5 bg-gray-200"></div>
      <label class="text-xs text-gray-500">筛选</label>
      <select v-model="filterStatus" @change="loadReviews"
        class="lan-compact-control text-sm border border-gray-200 rounded-lg px-3 py-1.5 bg-white outline-none focus:border-blue-400">
        <option value="">全部</option>
        <option v-for="t in TRACK" :key="t.value" :value="t.value">{{ t.label }}</option>
      </select>
      <label class="text-xs text-gray-500 flex items-center gap-1">
        医生ID
        <input v-model="doctorId" placeholder="DOC-001"
          class="lan-compact-control text-sm border border-gray-200 rounded-lg px-2 py-1 outline-none focus:border-blue-400" />
      </label>
      <div class="flex-1"></div>
      <button @click="refresh" class="lan-btn lan-btn-outline text-sm px-3 py-1.5 rounded-lg border border-gray-200 text-gray-600"><LanIcon name="reload" />刷新</button>
      <div class="flex items-center gap-1.5 text-xs" :class="wsConnected ? 'text-green-600' : 'text-red-400'">
        <span class="w-2 h-2 rounded-full" :class="wsConnected ? 'bg-green-500' : 'bg-red-400'"></span>
        <span>{{ wsConnected ? '实时已连接' : '实时断开' }}</span>
      </div>
    </div>

    <div v-if="backendError" class="shrink-0 bg-red-50 border-b border-red-200 px-5 py-2 text-sm text-red-700">
      ⚠️ {{ backendError }}
    </div>

    <div class="flex-1 flex overflow-hidden">
      <!-- 左：已生成随访会话列表（仅在触发随访后才出现） -->
      <div class="lan-sidebar w-80 shrink-0 border-r border-gray-200 bg-white overflow-y-auto">
        <div class="lan-list-heading px-4 py-3 text-xs font-semibold text-gray-400 uppercase tracking-wide border-b border-gray-100">
          随访会话（{{ displayedReviews.length }}）
        </div>
        <div v-if="displayedReviews.length === 0" class="px-4 py-10 text-center text-gray-400 text-sm">
          <div class="text-4xl mb-3">🗂️</div>
          <div>暂无随访会话</div>
          <div class="text-xs mt-1 text-gray-300">请在「随访执行」中触发随访后再查看</div>
        </div>
        <div
          v-for="r in displayedReviews" :key="r.patient_id"
          @click="selectReview(r)"
          class="lan-list-item px-4 py-3 border-b border-gray-100 cursor-pointer transition-colors hover:bg-gray-50"
          :class="selectedReview && selectedReview.patient_id === r.patient_id ? 'is-selected' : ''"
        >
          <div class="flex items-center justify-between">
            <span class="text-sm font-medium text-gray-800 truncate">{{ patientName(r.patient_id) }}</span>
            <span
              class="px-2 py-0.5 rounded text-xs font-medium shrink-0 ml-2"
              :class="trackMeta(r.track_status).cls"
            >{{ trackMeta(r.track_status).label }}</span>
          </div>
          <div class="flex items-center justify-between mt-1">
            <span class="text-xs text-gray-400">{{ r.reviewed_at || '未审阅' }}</span>
            <span class="flex items-center gap-2">
              <span v-if="r.ai_review" class="text-xs text-purple-500">🧠 AI已阅</span>
              <span v-if="r.doctor_score" class="text-xs text-amber-500">★ {{ r.doctor_score }}</span>
            </span>
          </div>
        </div>
      </div>

      <!-- 右：会话详情 + 审阅表单 -->
      <div class="lan-content flex-1 overflow-y-auto p-5">
        <div v-if="!sessionDetail" class="lan-empty h-full flex items-center justify-center text-gray-400 text-sm">
          <div class="text-center">
            <div class="text-5xl mb-4">💬</div>
            <div>触发一次随访会话，或在左侧选择一条审阅记录</div>
          </div>
        </div>

        <div v-else class="max-w-3xl mx-auto animate-fade-slide-up">
          <!-- 会话元信息 -->
          <div class="flex items-start justify-between mb-3">
            <div>
              <div class="flex items-center gap-3 flex-wrap">
                <span class="text-lg font-bold text-gray-800">{{ patientName(sessionPatientId) }}</span>
                <span v-if="currentPatient" class="text-xs text-gray-500 bg-gray-100 rounded-full px-2 py-0.5">{{ currentPatient.age }}岁</span>
                <span v-if="currentPatient" class="text-xs text-blue-600 bg-blue-50 rounded-full px-2 py-0.5">{{ currentPatient.diagnosis }}</span>
                <span v-if="currentPatient" class="text-xs text-gray-500 bg-gray-100 rounded-full px-2 py-0.5">主治：{{ currentPatient.doctor_name }}</span>
              </div>
              <div class="text-xs text-gray-400 mt-0.5 font-mono">{{ sessionDetail.session_id }}</div>
            </div>
            <div class="flex items-center gap-2">
              <span class="px-2 py-0.5 rounded text-xs font-medium" :class="riskMeta(sessionDetail.risk_result).cls">{{ riskMeta(sessionDetail.risk_result).label }}</span>
              <span v-if="sessionDetail.risk_result && sessionDetail.risk_result.total_score != null" class="text-xs text-gray-500">评分 {{ sessionDetail.risk_result.total_score }}</span>
            </div>
          </div>

          <!-- Agent 摘要 -->
          <div v-if="sessionDetail.agent_summary" class="mb-3 px-4 py-2.5 bg-blue-50 border border-blue-200 rounded-xl text-sm text-blue-800">
            🤖 {{ sessionDetail.agent_summary }}
          </div>

          <!-- AI 自动审阅意见（D 号 Agent） -->
          <div v-if="sessionDetail.ai_review" class="mb-4 px-4 py-3 bg-purple-50 border border-purple-200 rounded-2xl">
            <div class="flex items-center justify-between mb-2">
              <div class="text-sm font-semibold text-purple-700 flex items-center gap-2">🧠 AI 随访审阅意见</div>
              <span class="text-xs px-2 py-0.5 rounded bg-purple-100 text-purple-600">
                {{ sessionDetail.ai_review.source === 'llm' ? 'LLM 生成' : '规则兜底' }}
              </span>
            </div>
            <div v-if="sessionDetail.ai_review.summary" class="text-sm text-purple-800 leading-relaxed mb-2">{{ sessionDetail.ai_review.summary }}</div>
            <div v-if="sessionDetail.ai_review.risk_flags && sessionDetail.ai_review.risk_flags.length" class="flex flex-wrap gap-2 mb-2">
              <span v-for="(f, i) in sessionDetail.ai_review.risk_flags" :key="i"
                class="text-xs px-2 py-0.5 rounded bg-red-50 text-red-600 border border-red-200">{{ f }}</span>
            </div>
            <div class="flex items-center gap-4 text-xs text-gray-500">
              <span>完成度：<b class="text-purple-700">{{ sessionDetail.ai_review.completion_score }}</b>/100</span>
            </div>
            <div v-if="sessionDetail.ai_review.suggestions && sessionDetail.ai_review.suggestions.length" class="mt-2 text-xs text-gray-600">
              <div class="font-medium text-gray-500 mb-1">建议</div>
              <ul class="list-disc pl-5 space-y-0.5">
                <li v-for="(s, i) in sessionDetail.ai_review.suggestions" :key="i">{{ s }}</li>
              </ul>
            </div>
          </div>

          <!-- 对话原文（复用微信对话气泡渲染） -->
          <div class="bg-[#ededed] rounded-2xl p-4 mb-4 space-y-3 border border-gray-200">
            <div class="text-xs text-gray-400 mb-1">随访对话原文</div>
            <div
              v-for="(m, i) in sessionDetail.transcript_json" :key="i"
              class="flex" :class="m.role === 'patient' ? 'justify-end' : 'justify-start'"
            >
              <template v-if="m.role === 'nurse'">
                <div class="w-8 h-8 rounded-full bg-gradient-to-br from-blue-400 to-blue-600 flex items-center justify-center text-white text-xs font-semibold shrink-0 mb-1 shadow-sm">🩺</div>
                <div class="bubble-received text-sm leading-relaxed whitespace-pre-wrap">{{ m.content }}</div>
              </template>
              <template v-else>
                <div class="bubble-sent text-sm leading-relaxed whitespace-pre-wrap">{{ m.content }}</div>
                <div class="w-8 h-8 rounded-full bg-teal-400 flex items-center justify-center text-white text-xs font-semibold shrink-0 mb-1 shadow-sm">
                  {{ (patientName(sessionPatientId) || '患')[0] }}
                </div>
              </template>
            </div>
            <div v-if="!sessionDetail.transcript_json || sessionDetail.transcript_json.length === 0" class="text-sm text-gray-400 text-center py-4">
              无对话记录
            </div>
          </div>

          <!-- 审阅表单（F2.3 / F2.4 / F2.5） -->
          <div class="lan-card bg-white border border-gray-200 rounded-2xl p-5 space-y-4">
            <div class="text-sm font-semibold text-gray-700 flex items-center gap-2"><LanIcon name="edit" />医生审阅</div>

            <!-- 评分 -->
            <div>
              <div class="text-xs text-gray-500 mb-1.5">评分（1–5 分）</div>
              <div class="flex items-center gap-1">
                <button
                  v-for="n in 5" :key="n" @click="score = n"
                  class="text-2xl leading-none transition"
                  :class="n <= score ? 'text-amber-400' : 'text-gray-300 hover:text-amber-300'"
                >★</button>
                <span v-if="score" class="ml-2 text-sm text-gray-500">{{ score }} 分</span>
              </div>
            </div>

            <!-- 评论 -->
            <div>
              <div class="text-xs text-gray-500 mb-1.5">评论</div>
              <textarea
                v-model="comment" rows="3" placeholder="对本次随访过程的质量、规范性、风险识别等写下评论…"
                class="w-full border border-gray-200 rounded-xl p-3 text-sm outline-none focus:border-blue-400 bg-gray-50 resize-none"
              ></textarea>
            </div>

            <!-- 跟踪状态 -->
            <div>
              <div class="text-xs text-gray-500 mb-1.5">跟踪状态</div>
              <div class="flex flex-wrap gap-2">
                <button
                  v-for="t in TRACK" :key="t.value" @click="trackStatus = t.value"
                  class="px-3 py-1.5 rounded-lg text-sm border transition"
                  :class="trackStatus === t.value ? t.cls + ' border-transparent font-medium' : 'border-gray-200 text-gray-500 hover:bg-gray-50'"
                >{{ t.label }}</button>
              </div>
            </div>

            <div class="flex items-center gap-3 pt-1">
              <button
                @click="submitReview"
                :disabled="busy || !sessionDetail || submitted"
                class="lan-btn lan-btn-primary px-5 py-2 rounded-lg text-white font-medium transition disabled:opacity-50 disabled:cursor-not-allowed"
                :class="submitted ? 'bg-gray-400 cursor-not-allowed' : 'bg-green-500 hover:bg-green-600'"
              ><LanIcon name="check" />{{ busy ? '提交中…' : submitted ? '已提交' : '确认' }}</button>
              <span v-if="submitted" class="text-xs text-green-600 font-medium">✓ 审阅已提交</span>
              <span v-else-if="selectedReview" class="text-xs text-gray-400">该会话已有审阅记录，再次提交将新增一条留痕</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { io } from 'socket.io-client'
import { toast } from 'vue-sonner'
import LanIcon from './LanIcon.vue'

const props = defineProps({
  patients: { type: Array, default: () => [] },
  apiBase: { type: String, default: 'http://localhost:5000' },
})

const TRACK = [
  { value: 'followup_done', label: '随访完成', cls: 'bg-teal-100 text-teal-700' },
  { value: 'pending_track', label: '待跟踪', cls: 'bg-gray-100 text-gray-500' },
  { value: 'tracking', label: '跟踪中', cls: 'bg-blue-100 text-blue-700' },
  { value: 'resolved', label: '已解决', cls: 'bg-green-100 text-green-700' },
  { value: 'need_revisit', label: '需复诊', cls: 'bg-amber-100 text-amber-700' },
  { value: 'to_human', label: '转人工', cls: 'bg-red-100 text-red-600' },
]
function trackMeta(v) {
  const t = TRACK.find(x => x.value === v) || TRACK[0]
  return { label: t.label, cls: t.cls }
}
function riskMeta(rr) {
  if (!rr) return { label: '未知', cls: 'bg-gray-100 text-gray-500' }
  const level = rr.level_label || rr.level || '未知'
  const cls = rr.level === 'high' ? 'bg-red-100 text-red-600'
    : rr.level === 'medium' ? 'bg-amber-100 text-amber-700'
    : rr.level === 'low' ? 'bg-green-100 text-green-700'
    : 'bg-gray-100 text-gray-500'
  return { label: level, cls }
}

const doctorId = ref('DOC-001')
const reviews = ref([])
const selectedReview = ref(null)
const sessionDetail = ref(null)
const score = ref(0)
const comment = ref('')
const trackStatus = ref('followup_done')
const filterStatus = ref('')
const busy = ref(false)
const submitted = ref(false)
const backendError = ref('')
const wsConnected = ref(false)
let socket = null

const stats = ref({ total: 0, pending_track: 0, tracking: 0, resolved: 0, need_revisit: 0, avg_score: null })

// 应随访患者 = 全部患者中未标记免随访（skip_follow_up）的人
const needFollowup = computed(() =>
  (props.patients || []).filter(p => !p.skip_follow_up)
)
// 已加载会话按 patient_id 建索引，便于左侧每位应随访患者显示其对应会话内容。
const sessionByPatient = computed(() => {
  const m = {}
  for (const r of reviews.value) if (r.patient_id) m[r.patient_id] = r
  return m
})
// 仅显示在应随访名单中的会话记录（过滤历史累积的脏数据）
const displayedReviews = computed(() => {
  const needIds = new Set(needFollowup.value.map(p => p.patient_id))
  return reviews.value.filter(r => needIds.has(r.patient_id))
})

function patientName(pid) {
  const p = props.patients.find(x => x.patient_id === pid)
  return p ? p.name : (pid || '未知患者')
}
const sessionPatientId = ref('')
const currentPatient = computed(() =>
  props.patients.find(x => x.patient_id === sessionPatientId.value) || null
)

async function loadStats() {
  try {
    const today = new Date().toISOString().slice(0, 10)
    const res = await fetch(`${props.apiBase}/api/reviews/stats?today=${today}`).then(r => r.json())
    if (res.ok) stats.value = res
  } catch (e) { /* 非阻断 */ }
}
async function loadReviews() {
  try {
    const today = new Date().toISOString().slice(0, 10)
    const qsParts = [`today=${today}`]
    if (filterStatus.value) qsParts.push(`status=${encodeURIComponent(filterStatus.value)}`)
    const res = await fetch(`${props.apiBase}/api/reviews/latest?${qsParts.join('&')}`).then(r => r.json())
    if (res.ok) reviews.value = res.reviews || []
  } catch (e) { /* 非阻断 */ }
}
async function loadSession(sessionId) {
  if (!sessionId) return
  try {
    const res = await fetch(`${props.apiBase}/api/sessions/${sessionId}`).then(r => r.json())
    if (res.ok && res.session) {
      sessionDetail.value = res.session
      sessionPatientId.value = res.session.patient_id
    } else {
      toast.error('会话详情获取失败：' + (res.error || ''))
    }
  } catch (e) { toast.error('网络错误：' + e.message) }
}

function selectReview(r) {
  selectedReview.value = r
  score.value = r.doctor_score || 0
  comment.value = r.doctor_comment || ''
  trackStatus.value = r.track_status || 'followup_done'
  submitted.value = false
  loadSession(r.session_id)
}

async function submitReview() {
  if (!sessionDetail.value) return
  busy.value = true
  try {
    const res = await fetch(`${props.apiBase}/api/reviews`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: sessionDetail.value.session_id,
        doctor_score: score.value || null,
        doctor_comment: comment.value,
        track_status: trackStatus.value,
        reviewer_id: doctorId.value || undefined,
      }),
    }).then(r => r.json())
    if (!res.ok) { toast.error('提交失败：' + (res.error || '')); return }
    toast.success('审阅已提交（已留痕）')
    submitted.value = true
    await loadReviews(); await loadStats()
  } catch (e) { toast.error('网络错误：' + e.message) }
  finally { busy.value = false }
}

function refresh() { backendError.value = ''; loadReviews(); loadStats() }

function connectWs() {
  socket = io(props.apiBase, { transports: ['websocket', 'polling'], reconnection: true })
  socket.on('connect', () => { wsConnected.value = true })
  socket.on('disconnect', () => { wsConnected.value = false })
  socket.on('review:session_ready', (data) => {
    if (!data || !data.session_id) return
    toast.info(`${patientName(data.patient_id)} 的一次随访会话待审阅`)
    loadReviews(); loadStats()
    // 当前未查看会话，或正在查看该患者 → 切换到最新会话，避免一直显示最早一次
    const viewingSame = sessionPatientId.value && data.patient_id === sessionPatientId.value
    if (!sessionDetail.value || viewingSame) {
      selectedReview.value = null
      score.value = 0; comment.value = ''; trackStatus.value = 'followup_done'
      loadSession(data.session_id)
    }
  })
  socket.on('review:auto_done', (data) => {
    if (data) {
      const msg = `自动演示完成：生成 ${data.generated} 条随访会话` +
        (data.skipped ? `，跳过 ${data.skipped} 条已存在` : '')
      toast.success(msg)
    }
    loadReviews(); loadStats()
  })
}

// 页面加载时不再预拉取历史，避免「一进页面内容就已生成好」。
// 内容只在点击「开始今天随访」、随访真正运行（WS 推送 review:session_ready / review:auto_done）后才出现；
// 如需查看历史，可点右上角「刷新」手动拉取。
onMounted(() => { connectWs() })
onUnmounted(() => { if (socket) { socket.disconnect(); socket = null } })

// 每次「打开/进入」Review 面板时归零：统计栏回到 0、会话列表清空，
// 不预载后端历史审阅数据，等医生手动 review（提交）后再实时统计。
function resetState() {
  stats.value = { total: 0, pending_track: 0, tracking: 0, resolved: 0, need_revisit: 0, avg_score: null }
  reviews.value = []
  selectedReview.value = null
  sessionDetail.value = null
  score.value = 0
  comment.value = ''
  trackStatus.value = 'followup_done'
  sessionPatientId.value = ''
  submitted.value = false
}
defineExpose({ resetState, refresh })
</script>
