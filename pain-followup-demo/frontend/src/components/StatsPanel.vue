<!-- frontend/src/components/StatsPanel.vue -->
<template>
  <div v-if="!stats" class="flex items-center justify-center h-full text-gray-400 text-sm">
    <div class="text-center">
      <div class="text-4xl mb-3 animate-pulse">⏳</div>
      <div>等待随访结束，正在生成统计...</div>
    </div>
  </div>

  <div v-else class="space-y-3 p-3">
    <!-- 风险等级分布（2×2 卡片） -->
    <div class="stat-card p-3">
      <div class="text-xs text-gray-500 font-medium mb-3 flex items-center gap-2">
        <span>📊</span> 风险等级分布统计
        <span class="ml-auto text-gray-400">共 {{ total }} 人</span>
      </div>
      <div class="grid grid-cols-4 gap-2">
        <!-- 高风险 -->
        <div class="stat-card p-2">
          <div class="flex items-center gap-2 mb-1.5">
            <div class="w-7 h-7 rounded-md flex items-center justify-center text-sm bg-red-50 text-red-600 shrink-0">🔴</div>
            <div class="min-w-0">
              <div class="text-xs text-gray-500">高风险</div>
              <div class="text-lg font-bold text-gray-800">{{ highRiskCount }}<span class="text-xs text-gray-400 ml-0.5">人</span></div>
            </div>
          </div>
          <div class="w-full h-1 bg-gray-200 rounded-full overflow-hidden">
            <div class="h-full rounded-full bg-red-400" :style="{ width: percentOf(highRiskCount) + '%' }"></div>
          </div>
        </div>

        <!-- 中风险 -->
        <div class="stat-card p-2">
          <div class="flex items-center gap-2 mb-1.5">
            <div class="w-7 h-7 rounded-md flex items-center justify-center text-sm bg-yellow-50 text-yellow-600 shrink-0">🟡</div>
            <div class="min-w-0">
              <div class="text-xs text-gray-500">中风险</div>
              <div class="text-lg font-bold text-gray-800">{{ mediumRiskCount }}<span class="text-xs text-gray-400 ml-0.5">人</span></div>
            </div>
          </div>
          <div class="w-full h-1 bg-gray-200 rounded-full overflow-hidden">
            <div class="h-full rounded-full bg-yellow-400" :style="{ width: percentOf(mediumRiskCount) + '%' }"></div>
          </div>
        </div>

        <!-- 低风险 -->
        <div class="stat-card p-2">
          <div class="flex items-center gap-2 mb-1.5">
            <div class="w-7 h-7 rounded-md flex items-center justify-center text-sm bg-green-50 text-green-600 shrink-0">🟢</div>
            <div class="min-w-0">
              <div class="text-xs text-gray-500">低风险</div>
              <div class="text-lg font-bold text-gray-800">{{ lowRiskCount }}<span class="text-xs text-gray-400 ml-0.5">人</span></div>
            </div>
          </div>
          <div class="w-full h-1 bg-gray-200 rounded-full overflow-hidden">
            <div class="h-full rounded-full bg-green-400" :style="{ width: percentOf(lowRiskCount) + '%' }"></div>
          </div>
        </div>

        <!-- 需电话回访 -->
        <div class="stat-card p-2">
          <div class="flex items-center gap-2 mb-1.5">
            <div class="w-7 h-7 rounded-md flex items-center justify-center text-sm bg-orange-50 text-orange-600 shrink-0">📞</div>
            <div class="min-w-0">
              <div class="text-xs text-gray-500">需回访</div>
              <div class="text-lg font-bold text-gray-800">{{ callbackCount }}<span class="text-xs text-gray-400 ml-0.5">人</span></div>
            </div>
          </div>
          <div class="w-full h-1 bg-gray-200 rounded-full overflow-hidden">
            <div class="h-full rounded-full bg-orange-400" :style="{ width: percentOf(callbackCount) + '%' }"></div>
          </div>
        </div>
      </div>
    </div>

    <!-- 已评估患者明细：汇总数字之外，直接展示本次随访的具体结果 -->
    <div class="glass-panel overflow-hidden">
      <div class="px-3 py-2 border-b border-gray-200 bg-gray-50 flex items-center gap-2">
        <span class="text-xs">🧾</span>
        <span class="text-xs font-semibold text-gray-800">随访结果明细</span>
        <span class="ml-auto text-xs text-gray-400">{{ assessedPatients.length }} 人</span>
      </div>
      <div v-if="assessedPatients.length === 0" class="px-3 py-5 text-center text-xs text-gray-400">
        暂无已完成评分的患者
      </div>
      <div v-else class="divide-y divide-gray-100">
        <div v-for="(p, i) in assessedPatients" :key="p.patient_id || i" class="px-3 py-2 flex items-center gap-3">
          <span class="text-xs text-gray-400 w-5">{{ i + 1 }}</span>
          <span class="text-xs font-medium text-gray-800 w-24 truncate">{{ p.name || '未知患者' }}</span>
          <span class="text-xs text-gray-400 flex-1 truncate">{{ p.diagnosis || '—' }}</span>
          <span class="text-xs font-mono font-bold text-gray-800">{{ p.score ?? '—' }}分</span>
          <span class="text-xs px-1.5 py-0.5 rounded" :class="riskClass(p.level)">{{ riskLabel(p.level) }}</span>
        </div>
      </div>
    </div>

    <!-- 信息不完整/转人工患者：风险未知，但需要后续随访 -->
    <div v-if="followUpNeededPatients.length" class="glass-panel overflow-hidden border border-purple-200">
      <div class="px-3 py-2 border-b border-purple-200 bg-purple-50 flex items-center gap-2">
        <span class="text-xs">📌</span>
        <span class="text-xs font-semibold text-gray-800">需进一步随访</span>
        <span class="ml-auto text-xs text-purple-600">{{ followUpNeededPatients.length }} 人</span>
      </div>
      <div class="divide-y divide-gray-100">
        <div v-for="(p, i) in followUpNeededPatients" :key="p.patient_id || i" class="px-3 py-2 flex items-center gap-3">
          <span class="text-xs text-gray-400 w-5">{{ i + 1 }}</span>
          <span class="text-xs font-medium text-gray-800 w-24 truncate">{{ p.name || '未知患者' }}</span>
          <span class="text-xs text-gray-400 flex-1 truncate">{{ p.diagnosis || '—' }}</span>
          <span class="text-xs text-gray-500 shrink-0">风险：未知</span>
          <span class="text-xs text-purple-600 shrink-0">{{ p.follow_up_reason || '信息不完整，需进一步随访' }}</span>
        </div>
      </div>
    </div>

    <!-- 重点名单（3 列并排） -->
    <div class="grid grid-cols-3 gap-3">
      <!-- 高风险重点关注名单 -->
      <div class="glass-panel overflow-hidden">
        <div class="px-3 py-2 border-b border-red-200 bg-red-50 flex items-center gap-1.5">
          <span class="text-xs">📋</span>
          <span class="text-xs font-semibold text-gray-800 truncate">高风险 ≥ 8</span>
          <span class="ml-auto text-xs text-gray-400">{{ highRiskFocus.length }}</span>
        </div>
        <div v-if="highRiskFocus.length === 0" class="px-3 py-4 text-center text-xs text-gray-400">无</div>
        <div v-else>
          <div v-for="(p, i) in highRiskFocus" :key="i" class="px-3 py-1.5 border-b border-gray-100 hover:bg-gray-50 transition-colors">
            <div class="flex items-center justify-between">
              <span class="text-xs font-medium text-gray-800 truncate">{{ p.name }}</span>
              <span class="text-xs font-mono font-bold text-gray-800 ml-1 shrink-0">{{ p.score }}分</span>
            </div>
            <div class="text-xs text-gray-400 truncate mt-0.5">{{ p.diagnosis }}</div>
          </div>
        </div>
      </div>

      <!-- 需电话回访 -->
      <div class="glass-panel overflow-hidden">
        <div class="px-3 py-2 border-b border-orange-200 bg-orange-50 flex items-center gap-1.5">
          <span class="text-xs">📞</span>
          <span class="text-xs font-semibold text-gray-800 truncate">三日未回复</span>
          <span class="ml-auto text-xs text-gray-400">{{ noReplyPatients.length }}</span>
        </div>
        <div v-if="noReplyPatients.length === 0" class="px-3 py-4 text-center text-xs text-gray-400">无</div>
        <div v-else>
          <div v-for="(p, i) in noReplyPatients" :key="i" class="px-3 py-1.5 border-b border-gray-100 hover:bg-gray-50 transition-colors">
            <div class="flex items-center justify-between">
              <span class="text-xs font-medium text-gray-800 truncate">{{ p.name }}</span>
              <span class="text-xs font-mono font-bold ml-1 shrink-0" :class="p.score == null ? 'text-orange-500' : 'text-gray-800'">
                {{ p.score == null ? '未回' : p.score + '分' }}
              </span>
            </div>
            <div class="text-xs text-gray-400 truncate mt-0.5">{{ p.diagnosis }}</div>
          </div>
        </div>
      </div>

      <!-- 低风险免除名单 -->
      <div class="glass-panel overflow-hidden">
        <div class="px-3 py-2 border-b border-green-200 bg-green-50 flex items-center gap-1.5">
          <span class="text-xs">🟢</span>
          <span class="text-xs font-semibold text-gray-800 truncate">低风险 ≤ 1</span>
          <span class="ml-auto text-xs text-gray-400">{{ lowRiskExempt.length }}</span>
        </div>
        <div v-if="lowRiskExempt.length === 0" class="px-3 py-4 text-center text-xs text-gray-400">无</div>
        <div v-else>
          <div v-for="(p, i) in lowRiskExempt" :key="i" class="px-3 py-1.5 border-b border-gray-100 hover:bg-gray-50 transition-colors">
            <div class="flex items-center justify-between">
              <span class="text-xs font-medium text-gray-800 truncate">{{ p.name }}</span>
              <span class="text-xs font-mono font-bold text-gray-800 ml-1 shrink-0">{{ p.score }}分</span>
            </div>
            <div class="text-xs text-gray-400 truncate mt-0.5">{{ p.diagnosis }}</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  stats: { type: Object, default: null },
})

const assessedPatients = computed(() => {
  if (props.stats?.assessed_patients?.length) return props.stats.assessed_patients
  // 兼容旧版/重连载荷：后端只返回 reports_by_episode 时，从报告还原明细。
  return Object.values(props.stats?.reports_by_episode || {})
    .map(report => ({
      patient_id: report.patient_id,
      name: report.name,
      diagnosis: report.diagnosis,
      level: report.risk?.level,
      score: report.risk?.score,
    }))
    .filter(patient => ['high', 'medium', 'low'].includes(patient.level) && patient.score != null)
})

// 兼容旧事件：若事件只带 reports_by_episode，也要把风险人数显示出来。
const highRiskCount = computed(() => Number(
  props.stats?.high_risk_count ?? assessedPatients.value.filter(p => p.level === 'high').length,
))
const mediumRiskCount = computed(() => Number(
  props.stats?.medium_risk_count ?? assessedPatients.value.filter(p => p.level === 'medium').length,
))
const lowRiskCount = computed(() => Number(
  props.stats?.low_risk_count ?? assessedPatients.value.filter(p => p.level === 'low').length,
))
const callbackCount = computed(() => Number(
  props.stats?.callback_count ?? props.stats?.no_reply_count ?? 0,
))
const totalAssessed = computed(() => Number(
  props.stats?.total_assessed ?? assessedPatients.value.length,
))
const totalFollowup = computed(() => Number(props.stats?.total_followup ?? 0))
const highRiskFocus = computed(() => props.stats?.high_risk_focus || [])
const lowRiskExempt = computed(() => props.stats?.low_risk_exempt || [])
const noReplyPatients = computed(() => props.stats?.no_reply_patients || [])
const followUpNeededPatients = computed(() => props.stats?.follow_up_needed_patients || [])

// 回访人数可能与高风险患者重叠，因此总人数不能把 callback_count 再加一次。
const total = computed(() => {
  return totalFollowup.value || totalAssessed.value + noReplyPatients.value.length || 1
})

function percentOf(val) {
  return Math.round((val / total.value) * 100)
}

function riskLabel(level) {
  return level === 'high' ? '高风险' : level === 'medium' ? '中风险' : level === 'low' ? '低风险' : '未知'
}

function riskClass(level) {
  return level === 'high'
    ? 'bg-red-50 text-red-700'
    : level === 'medium'
      ? 'bg-yellow-50 text-yellow-700'
      : level === 'low'
        ? 'bg-green-50 text-green-700'
        : 'bg-gray-100 text-gray-500'
}
</script>
