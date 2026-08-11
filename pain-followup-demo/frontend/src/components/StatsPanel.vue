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

const highRiskCount = computed(() => props.stats?.high_risk_count || 0)
const mediumRiskCount = computed(() => props.stats?.medium_risk_count || 0)
const lowRiskCount = computed(() => props.stats?.low_risk_count || 0)
const callbackCount = computed(() => props.stats?.callback_count || 0)
const totalAssessed = computed(() => props.stats?.total_assessed || 0)
const totalFollowup = computed(() => props.stats?.total_followup || 0)
const highRiskFocus = computed(() => props.stats?.high_risk_focus || [])
const lowRiskExempt = computed(() => props.stats?.low_risk_exempt || [])
const noReplyPatients = computed(() => props.stats?.no_reply_patients || [])

// 应随访总数 = 各类别人数之和（高风险 + 中风险 + 低风险 + 需回访），
// 保证"共 X 人"与下方四格数值加总完全一致。
const total = computed(() => {
  const sum = highRiskCount.value + mediumRiskCount.value + lowRiskCount.value + callbackCount.value
  return sum || totalFollowup.value || (totalAssessed.value + callbackCount.value) || 1
})

function percentOf(val) {
  return Math.round((val / total.value) * 100)
}
</script>
