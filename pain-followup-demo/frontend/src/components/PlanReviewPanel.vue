<template>
  <div class="lan-workspace h-full flex flex-col bg-gray-50 text-gray-800 overflow-hidden">
    <!-- 顶部工具栏 -->
    <div class="lan-toolbar shrink-0 bg-white border-b border-gray-200 px-5 py-3 flex flex-wrap items-center gap-3">
      <div class="lan-toolbar-title flex items-center gap-2 text-sm font-semibold text-gray-700">
        <LanIcon name="list" /><span>随访计划审阅</span>
      </div>
      <div class="w-px h-5 bg-gray-200"></div>
      <button
        @click="generatePlan" :disabled="loading || !selectedPatientId"
        class="lan-btn lan-btn-primary text-sm px-4 py-1.5 rounded-lg bg-blue-500 text-white font-medium disabled:opacity-50 disabled:cursor-not-allowed transition"
      >
        <LanIcon name="settings" />
        {{ loading ? '生成中…' : '为该患者生成随访计划' }}
      </button>
      <button
        @click="generateAllPlans" :disabled="allLoading"
        class="lan-btn lan-btn-outline text-sm px-4 py-1.5 rounded-lg font-medium disabled:opacity-50 disabled:cursor-not-allowed transition"
      >
        <LanIcon name="filePaste" />
        为全部患者生成随访计划
      </button>
      <button
        @click="approveAll" :disabled="batchApproving || pendingCount === 0"
        class="lan-btn lan-btn-brand-outline text-sm px-4 py-1.5 rounded-lg font-medium disabled:opacity-50 disabled:cursor-not-allowed transition"
      >
        <LanIcon name="check" />
        {{ batchApproving ? '同意中…' : `批量同意 (${pendingCount})` }}
      </button>
      <div class="w-px h-5 bg-gray-200"></div>
      <label class="text-xs text-gray-500 flex items-center gap-1">
        医生ID
        <input
          v-model="doctorId" placeholder="DOC-001"
          class="lan-compact-control text-sm border border-gray-200 rounded-lg px-2 py-1 outline-none focus:border-blue-400"
        />
      </label>
      <div class="flex-1"></div>
      <button @click="refresh" class="lan-btn lan-btn-outline text-sm px-3 py-1.5 rounded-lg border border-gray-200 text-gray-600"><LanIcon name="reload" />刷新</button>
      <div class="flex items-center gap-1.5 text-xs" :class="wsConnected ? 'text-green-600' : 'text-red-400'">
        <span class="w-2 h-2 rounded-full" :class="wsConnected ? 'bg-green-500' : 'bg-red-400'"></span>
        <span>{{ wsConnected ? '实时已连接' : '实时断开' }}</span>
      </div>
    </div>

    <!-- 批量生成进度条 -->
    <div v-if="allLoading" class="lan-batch-progress shrink-0 border-b px-5 py-2">
      <div class="lan-batch-progress-text flex items-center justify-between text-xs mb-1">
        <span>正在为全部患者生成随访计划… {{ genDone }} / {{ genTotal }}</span>
        <span v-if="genCurrentName">当前：{{ genCurrentName }}</span>
      </div>
      <div class="lan-batch-progress-track h-2 rounded-full overflow-hidden">
        <div
          class="lan-batch-progress-value h-full transition-all duration-300"
          :style="{ width: progressPct + '%' }"
        ></div>
      </div>
    </div>

    <!-- 后端依赖缺失提示 -->
    <div v-if="backendError" class="shrink-0 bg-red-50 border-b border-red-200 px-5 py-2 text-sm text-red-700">
      ⚠️ {{ backendError }}
    </div>

    <div class="flex-1 flex overflow-hidden">
      <!-- 左：所有患者列表 -->
      <div class="lan-sidebar w-80 shrink-0 border-r border-gray-200 bg-white overflow-y-auto">
        <div class="lan-list-heading px-4 py-3 text-xs font-semibold text-gray-400 uppercase tracking-wide border-b border-gray-100">
          患者列表（{{ patients.length }}）
        </div>
        <div v-if="patients.length === 0" class="px-4 py-10 text-center text-gray-400 text-sm">
          患者列表为空
        </div>
        <div
          v-for="p in patients" :key="p.patient_id"
          @click="selectPatient(p)"
          class="lan-list-item px-4 py-3 border-b border-gray-100 cursor-pointer transition-colors hover:bg-gray-50"
          :class="selectedPatientId === p.patient_id ? 'is-selected' : ''"
        >
          <div class="flex items-center justify-between">
            <span class="text-sm font-medium text-gray-800 truncate">{{ p.name }}</span>
            <div class="flex items-center gap-1">
              <span
                v-for="(t, i) in statusTags(plans[p.patient_id])" :key="i"
                class="px-2 py-0.5 rounded text-xs font-medium" :class="t.cls"
              >{{ t.label }}</span>
              <span
                v-if="!plans[p.patient_id]"
                class="px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-400"
              >未生成</span>
            </div>
          </div>
          <div class="text-xs text-gray-500 mt-0.5 truncate">{{ p.diagnosis }}</div>
          <div class="text-xs text-gray-400 mt-0.5 font-mono">{{ p.patient_id }}</div>
        </div>
      </div>

      <!-- 右：患者基本信息 + 随访计划 -->
      <div class="lan-content flex-1 overflow-y-auto p-5">
        <div v-if="!selectedPatient" class="lan-empty h-full flex items-center justify-center text-gray-400 text-sm">
          <div class="text-center">
            <div class="text-5xl mb-4">👈</div>
            <div>从左侧选择一位患者查看基本信息与随访计划</div>
          </div>
        </div>

        <div v-else class="max-w-3xl mx-auto animate-fade-slide-up">
          <!-- 患者基本信息卡片 -->
          <div class="lan-card bg-white border border-gray-200 rounded-2xl p-4 mb-4">
            <div class="flex items-start justify-between mb-3">
              <div>
                <div class="text-lg font-bold text-gray-800">{{ info.name }}</div>
                <div class="text-sm text-gray-500 mt-0.5">
                  <span class="font-mono">{{ info.patient_id }}</span>
                </div>
              </div>
              <div class="flex items-center gap-1">
                <span
                  v-for="(t, i) in statusTags(activeDraft)" :key="i"
                  class="px-2 py-0.5 rounded text-xs font-medium" :class="t.cls"
                >{{ t.label }}</span>
                <span
                  v-if="!activeDraft"
                  class="px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-400"
                >未生成计划</span>
              </div>
            </div>
            <div class="grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
              <div class="flex gap-2"><span class="text-gray-400 w-16 shrink-0">诊断</span><span class="text-gray-800">{{ info.diagnosis || '—' }}</span></div>
              <div class="flex gap-2"><span class="text-gray-400 w-16 shrink-0">年龄</span><span class="text-gray-800">{{ info.age != null ? info.age + ' 岁' : '—' }}</span></div>
              <div class="flex gap-2"><span class="text-gray-400 w-16 shrink-0">主管医生</span><span class="text-gray-800">{{ info.doctor_name || '—' }}</span></div>
              <div class="flex gap-2"><span class="text-gray-400 w-16 shrink-0">出院日期</span><span class="text-gray-800">{{ info.discharge_date || '—' }}</span></div>
            </div>
          </div>

          <!-- 未生成计划：提示生成 -->
          <div v-if="!activeDraft" class="lan-card lan-empty bg-white border border-dashed border-gray-300 rounded-2xl p-8 text-center">
            <div class="text-4xl mb-3">🗒️</div>
            <div class="text-gray-500 text-sm mb-4">该患者尚未生成出院随访计划</div>
            <button
              @click="generatePlan" :disabled="loading"
              class="lan-btn lan-btn-primary px-5 py-2 rounded-lg bg-blue-500 text-white font-medium disabled:opacity-50 transition"
            ><LanIcon name="settings" />生成随访计划</button>
          </div>

          <!-- 已生成计划 -->
          <div v-else>
            <!-- 降级/合规提示 -->
            <div v-if="activeDraft.plan_json && activeDraft.plan_json.note" class="mb-4 px-4 py-2.5 bg-amber-50 border border-amber-200 rounded-xl text-sm text-amber-700">
              ⓘ {{ activeDraft.plan_json.note }}
            </div>

            <!-- 计划字段（编辑时原地可改，不另开页面） -->
            <div class="lan-card bg-white border border-gray-200 rounded-2xl p-5 mb-4 space-y-4">
              <div v-for="row in planRows(activeDraft.plan_json)" :key="row.key" class="flex gap-3">
                <div class="w-28 shrink-0 text-sm font-semibold text-gray-600 pt-0.5">{{ row.label }}</div>
                <div class="flex-1 text-sm text-gray-800 leading-relaxed">
                  <!-- 编辑态：原地编辑 -->
                  <template v-if="editing">
                    <div v-if="row.type === 'list'" class="space-y-1.5">
                      <div v-for="(item, idx) in editForm[row.key]" :key="idx" class="flex gap-1.5">
                        <input v-model="editForm[row.key][idx]"
                          class="flex-1 text-sm border border-gray-200 rounded-lg px-3 py-1.5 outline-none focus:border-amber-400 bg-white"
                        />
                        <button @click="editForm[row.key].splice(idx, 1)" class="lan-icon-btn text-gray-400 hover:text-red-500"><LanIcon name="close" /></button>
                      </div>
                      <button @click="editForm[row.key].push('')" class="lan-text-action text-xs text-blue-500 hover:text-blue-700"><LanIcon name="add" />添加一项</button>
                    </div>
                    <textarea v-else-if="row.key === 'note'" v-model="editForm[row.key]" rows="2"
                      class="w-full text-sm border border-gray-200 rounded-lg px-3 py-1.5 outline-none focus:border-amber-400 bg-white resize-none"
                    ></textarea>
                    <input
                      v-else
                      v-model="editForm[row.key]"
                      :type="row.key === 'duration_days' ? 'number' : 'text'"
                      :placeholder="row.label"
                      class="w-full text-sm border border-gray-200 rounded-lg px-3 py-1.5 outline-none focus:border-amber-400 bg-white"
                    />
                  </template>
                  <!-- 展示态：正文中 [n] 内联溯源 -->
                  <template v-else>
                    <ul v-if="row.type === 'list'" class="space-y-1.5">
                      <li v-for="(item, i) in row.value" :key="i" class="flex gap-2">
                        <span class="lan-brand-bullet mt-0.5">•</span>
                        <span class="flex-1"><RefText :text="item" :citations="activeDraft.citations" :api-base="apiBase" /></span>
                      </li>
                    </ul>
                    <RefText v-else :text="row.value" :citations="activeDraft.citations" :api-base="apiBase" />
                  </template>
                </div>
              </div>
            </div>

            <!-- 操作区 -->
            <div v-if="!editing" class="flex items-center gap-3 pt-2">
              <button
                @click="approve(activeDraft)"
                :disabled="busy || activeDraft.status === 'approved'"
                :class="activeDraft.status === 'approved'
                  ? 'bg-gray-300 text-gray-500 cursor-not-allowed'
                  : 'bg-green-500 text-white hover:bg-green-600 disabled:opacity-50'"
                class="lan-btn lan-btn-primary px-5 py-2 rounded-lg font-medium transition"
              ><LanIcon name="check" />{{ activeDraft.status === 'approved' ? '已同意' : '同意' }}</button>
              <button
                @click="startEdit(activeDraft)" :disabled="busy"
                class="lan-btn lan-btn-brand-outline px-5 py-2 rounded-lg font-medium disabled:opacity-50 transition"
              ><LanIcon name="edit" />修改</button>

            </div>

            <!-- 编辑态操作 -->
            <div v-else class="flex items-center gap-3 pt-2">
              <button
                @click="submitModify(activeDraft)" :disabled="busy"
                class="lan-btn lan-btn-warning px-5 py-2 rounded-lg bg-amber-500 text-white font-medium disabled:opacity-50 transition"
              ><LanIcon name="save" />{{ busy ? '提交中…' : '提交修改' }}</button>
              <button @click="editing = false" class="lan-btn lan-btn-outline px-4 py-2 rounded-lg border border-gray-200 text-gray-600"><LanIcon name="close" />取消</button>
              <span v-if="editError" class="text-xs text-red-500">{{ editError }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { io } from 'socket.io-client'
import { toast } from 'vue-sonner'
import RefText from './RefText.vue'
import LanIcon from './LanIcon.vue'

const props = defineProps({
  patients: { type: Array, default: () => [] },
  apiBase: { type: String, default: 'http://localhost:5000' },
})

// 以 patient_id 为键的计划草稿表（每位患者最多一条，天然去重）
const plans = ref({})
const selectedPatientId = ref('')
const doctorId = ref('DOC-001')
const selectedDetail = ref(null)
const editing = ref(false)
const editForm = ref({})  // 字段编辑表单，非 JSON 文本
const editError = ref('')
const loading = ref(false)
const busy = ref(false)
const allLoading = ref(false)
const genDone = ref(0)
const genTotal = ref(0)
const genCurrentName = ref('')
const batchApproving = ref(false)
const backendError = ref('')
const wsConnected = ref(false)
let socket = null

const STATUS_MAP = {
  draft: ['草稿', 'bg-gray-100 text-gray-500'],
  approved: ['已同意', 'bg-green-100 text-green-700'],
  modified: ['已修改', 'bg-amber-100 text-amber-700'],
}
// 状态标签：支持同时展示多个（如“已修改”+“已同意”）。
// - 被修改过（modified 标记或状态为 modified）→ 显示「已修改」
// - 已同意 → 追加「已同意」；其余 → 「草稿」
function statusTags(d) {
  if (!d) return []
  const tags = []
  const isModified = d.modified || d.status === 'modified'
  if (isModified) tags.push({ label: '已修改', cls: 'bg-amber-100 text-amber-700' })
  if (d.status === 'approved') tags.push({ label: '已同意', cls: 'bg-green-100 text-green-700' })
  else if (d.status === 'draft') tags.push({ label: '草稿', cls: 'bg-gray-100 text-gray-500' })
  return tags
}

const selectedPatient = computed(() => props.patients.find(p => p.patient_id === selectedPatientId.value) || null)
const activeDraft = computed(() => plans.value[selectedPatientId.value] || null)

// 基本信息：优先用详情接口返回（含出院日期等），缺字段回退列表数据
const info = computed(() => {
  const base = selectedDetail.value || selectedPatient.value || {}
  return {
    patient_id: base.patient_id || selectedPatientId.value,
    name: base.name || (selectedPatient.value && selectedPatient.value.name) || '未知患者',
    age: base.age,
    diagnosis: base.diagnosis || (selectedPatient.value && selectedPatient.value.diagnosis) || '',
    doctor_name: base.doctor_name || (selectedPatient.value && selectedPatient.value.doctor_name) || '',
    discharge_date: base.discharge_date || '',
  }
})

// 待处理数量：按患者去重，草稿/已修改计入（修改不减少），已同意不计入
const pendingCount = computed(() => {
  return Object.values(plans.value).filter(d => d.status === 'draft' || d.status === 'modified').length
})

const progressPct = computed(() => (genTotal.value ? Math.round((genDone.value / genTotal.value) * 100) : 0))

function makeDraft(res, createdAt) {
  return {
    plan_id: res.plan_id,
    patient_id: res.patient_id,
    diagnosis: res.diagnosis,
    plan_json: res.plan_json || {},
    citations: res.citations || [],
    status: 'draft',
    created_at: createdAt,
  }
}

// 把 plan_json 规整为可渲染的行（同时兼容缺失字段），并带上 key 供原地编辑绑定
function planRows(plan) {
  if (!plan) return []
  const rows = []
  const push = (key, label, value, type) => {
    if (value === undefined || value === null || value === '' || (Array.isArray(value) && value.length === 0)) return
    rows.push({ key, label, value, type })
  }
  push('frequency', '随访频次', plan.frequency, 'text')
  push('duration_days', '随访周期', plan.duration_days != null ? `${plan.duration_days} 天` : '', 'text')
  push('pain_type', '疼痛类型', plan.pain_type, 'text')
  push('recheck_items', '复查项目', plan.recheck_items, 'list')
  push('medication_adjustment', '用药调整', plan.medication_adjustment, 'text')
  push('warning_threshold', '预警阈值', plan.warning_threshold, 'text')
  push('health_education', '健康教育', plan.health_education, 'list')
  push('lifestyle', '生活方式建议', plan.lifestyle, 'list')
  push('note', '备注', plan.note, 'text')
  return rows
}

function selectPatient(p) {
  selectedPatientId.value = p.patient_id
  editing.value = false
  editError.value = ''
  selectedDetail.value = null
  // 读取患者详情（只读，不修改数据库），用于展示出院日期等
  fetch(`${props.apiBase}/api/patients/${p.patient_id}`)
    .then(r => r.json())
    .then(det => { if (det && !det.error) selectedDetail.value = det })
    .catch(() => {})
}

function generatePlan() {
  if (!selectedPatientId.value) {
    toast.error('请先在左侧选择患者')
    return
  }
  loading.value = true
  backendError.value = ''
  const pid = selectedPatientId.value
  fetch(`${props.apiBase}/api/plans/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ patient_id: pid, doctor_id: doctorId.value || undefined }),
  }).then(r => r.json()).then(res => {
    if (!res.ok) {
      backendError.value = res.error || '生成失败'
      toast.error('生成失败：' + (res.error || '未知错误'))
      return
    }
    const draft = makeDraft(res, new Date().toLocaleString('zh-CN'))
    plans.value = { ...plans.value, [pid]: draft }
    editing.value = false
    toast.success('已生成随访计划草稿，请审阅')
  }).catch(e => {
    backendError.value = '无法连接后端，请确认 python app.py 已启动且已安装多 Agent 依赖（requirements-knowledge.txt）'
    toast.error('网络错误：' + e.message)
  }).finally(() => { loading.value = false })
}

function generateAllPlans() {
  if (props.patients.length === 0) {
    toast.error('患者列表为空')
    return
  }
  allLoading.value = true
  backendError.value = ''
  genDone.value = 0
  genTotal.value = props.patients.length
  genCurrentName.value = ''
  let success = 0, fail = 0
  const list = props.patients
  const CONCURRENCY = 5
  let cursor = 0
  const worker = async () => {
    while (cursor < list.length) {
      const i = cursor++
      const p = list[i]
      genCurrentName.value = `${p.name}（${i + 1}/${list.length}）`
      // 已同意的计划不再覆盖，保持原有同意状态
      if (plans.value[p.patient_id]?.status === 'approved') {
        success++
        genDone.value++
        continue
      }
      try {
        const res = await fetch(`${props.apiBase}/api/plans/generate`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ patient_id: p.patient_id, doctor_id: doctorId.value || undefined }),
        }).then(r => r.json())
        if (res.ok) {
          const draft = makeDraft(res, new Date().toLocaleString('zh-CN'))
          plans.value = { ...plans.value, [p.patient_id]: draft }
          success++
        } else {
          fail++
          console.warn(`计划生成失败（${p.name}）：${res.error}`)
        }
      } catch (e) {
        fail++
        console.warn(`计划生成错误（${p.name}）：${e.message}`)
      } finally {
        genDone.value++
      }
    }
  }
  Promise.all(Array.from({ length: Math.min(CONCURRENCY, list.length) }, worker)).then(() => {
    allLoading.value = false
    genCurrentName.value = ''
    toast.success(`全部完成：成功 ${success} 份，失败 ${fail} 份`)
  })
}

async function approve(d) {
  if (d.status === 'approved') return
  busy.value = true
  try {
    const res = await fetch(`${props.apiBase}/api/plans/${d.plan_id}/approve`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ doctor_id: doctorId.value || undefined }),
    }).then(r => r.json())
    if (!res.ok) { toast.error('同意失败：' + (res.error || '')); return }
    d.status = 'approved'
    toast.success('已同意该随访计划，计划生效')
  } catch (e) { toast.error('网络错误：' + e.message) }
  finally { busy.value = false }
}

async function approveAll() {
  if (pendingCount.value === 0) {
    toast.error('暂无待同意计划，请先生成随访计划')
    return
  }
  batchApproving.value = true
  try {
    const res = await fetch(`${props.apiBase}/api/plans/batch-approve`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ doctor_id: doctorId.value || undefined }),
    }).then(r => r.json())
    if (!res.ok) { toast.error('批量同意失败：' + (res.error || '')); return }
    Object.values(plans.value).forEach(d => {
      if (d.status === 'draft' || d.status === 'modified') d.status = 'approved'
    })
    toast.success(`已批量同意 ${res.approved} 份随访计划，全部生效`)
  } catch (e) { toast.error('网络错误：' + e.message) }
  finally { batchApproving.value = false }
}

function startEdit(d) {
  const p = d.plan_json || {}
  editForm.value = {
    frequency: p.frequency || '',
    duration_days: p.duration_days ?? 90,
    pain_type: p.pain_type || '',
    recheck_items: Array.isArray(p.recheck_items) ? [...p.recheck_items] : [],
    medication_adjustment: p.medication_adjustment || '',
    warning_threshold: p.warning_threshold || '',
    health_education: Array.isArray(p.health_education) ? [...p.health_education] : [],
    lifestyle: Array.isArray(p.lifestyle) ? [...p.lifestyle] : [],
    note: p.note || '',
  }
  editError.value = ''
  editing.value = true
}

async function submitModify(d) {
  const plan_json = {
    frequency: editForm.value.frequency,
    duration_days: editForm.value.duration_days,
    pain_type: editForm.value.pain_type,
    recheck_items: editForm.value.recheck_items.filter(Boolean),
    medication_adjustment: editForm.value.medication_adjustment,
    warning_threshold: editForm.value.warning_threshold,
    health_education: editForm.value.health_education.filter(Boolean),
    lifestyle: editForm.value.lifestyle.filter(Boolean),
    note: editForm.value.note,
  }
  busy.value = true
  try {
    const res = await fetch(`${props.apiBase}/api/plans/${d.plan_id}/modify`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ plan_json, doctor_id: doctorId.value || undefined }),
    }).then(r => r.json())
    if (!res.ok) { toast.error('修改提交失败：' + (res.error || '')); return }
    d.plan_json = plan_json
    d.status = 'modified'
    d.modified = true  // 记录“曾被修改”，供后续同意时同时展示「已修改」+「已同意」
    editing.value = false
    toast.success('已保存修改后的计划（原文已留痕）')
  } catch (e) { toast.error('网络错误：' + e.message) }
  finally { busy.value = false }
}

// 从后端加载所有已存在的计划草稿（启动时预填充的）
async function loadAllPlans() {
  try {
    const res = await fetch(`${props.apiBase}/api/plans`).then(r => r.json())
    if (res.ok && res.plans) {
      const map = {}
      for (const p of res.plans) {
        map[p.patient_id] = {
          plan_id: p.plan_id,
          patient_id: p.patient_id,
          diagnosis: p.diagnosis || '',
          plan_json: p.plan_json || {},
          citations: p.guideline_citations || p.citations || [],
          status: p.status || 'draft',
          created_at: p.created_at || '',
        }
      }
      plans.value = { ...plans.value, ...map }
    }
  } catch (e) { /* 非阻断 */ }
}

function refresh() { backendError.value = '' }

// 患者列表异步加载完成后，默认选中第一位患者，并加载已有的预填充计划草稿
watch(() => props.patients, (list) => {
  if (list && list.length && !selectedPatientId.value) selectPatient(list[0])
  if (list && list.length > 0) loadAllPlans()
}, { immediate: true })

// ---- WebSocket：监听实时草稿推送（出院触发 / 其他端生成） ----
function connectWs() {
  socket = io(props.apiBase, { transports: ['websocket', 'polling'], reconnection: true })
  socket.on('connect', () => { wsConnected.value = true })
  socket.on('disconnect', () => { wsConnected.value = false })
  socket.on('plan:drafted', (data) => {
    if (!data || !data.plan_id) return
    const draft = makeDraft(data, new Date().toLocaleString('zh-CN'))
    plans.value = { ...plans.value, [draft.patient_id]: draft }
    if (!selectedPatientId.value) selectPatient({ patient_id: draft.patient_id })
    toast.info('收到一份新的随访计划草稿')
  })
}

onMounted(connectWs)
onUnmounted(() => { if (socket) { socket.disconnect(); socket = null } })
</script>
