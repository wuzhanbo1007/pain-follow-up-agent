<template>
  <div class="glass-panel h-full flex flex-col" :class="activeTab === 'stats' ? 'overflow-y-auto' : 'overflow-hidden'">
    <div class="px-5 py-4 border-b border-gray-200 shrink-0">
      <h2 class="text-lg font-bold text-gray-800">PainSmart 疼痛智能随访平台</h2>
      <p class="text-xs text-gray-400">医生管理后台{{ finalStats ? ' · 随访结果' : ' · 实时监控' }}</p>
    </div>

    <!-- 统计卡片 -->
    <div class="grid grid-cols-6 gap-2.5 p-4 shrink-0">
      <div class="stat-card relative overflow-hidden group">
        <div class="absolute top-0 right-0 w-20 h-20 -mr-6 -mt-6 rounded-full opacity-10 bg-blue-500"></div>
        <div class="relative z-10">
          <div class="flex items-center gap-2 mb-3">
            <LanIcon name="user" :size="20" />
            <span class="text-xs text-gray-500 font-medium">应随访</span>
          </div>
          <div class="text-3xl font-bold text-gray-800">{{ stats.send_count || stats.total_patients || 0 }}</div>
        </div>
      </div>

      <div class="stat-card relative overflow-hidden group">
        <div class="absolute top-0 right-0 w-20 h-20 -mr-6 -mt-6 rounded-full opacity-10 bg-cyan-500"></div>
        <div class="relative z-10">
          <div class="flex items-center gap-2 mb-3">
            <LanIcon name="upload" :size="20" />
            <span class="text-xs text-gray-500 font-medium">已发送</span>
          </div>
          <div class="text-3xl font-bold text-gray-800">{{ stats.sent_count || 0 }}</div>
          <div v-if="hasFollowupStarted" class="text-xs text-gray-400 mt-1">免随访 {{ stats.skip_count }} 人</div>
        </div>
      </div>

      <div class="stat-card relative overflow-hidden group">
        <div class="absolute top-0 right-0 w-20 h-20 -mr-6 -mt-6 rounded-full opacity-10 bg-red-500"></div>
        <div class="relative z-10">
          <div class="flex items-center gap-2 mb-3">
            <LanIcon name="notification" :size="20" />
            <span class="text-xs text-gray-500 font-medium">高风险</span>
          </div>
          <div class="text-3xl font-bold text-gray-800">{{ stats.high_risk_count || 0 }}</div>
        </div>
      </div>

      <div class="stat-card relative overflow-hidden group">
        <div class="absolute top-0 right-0 w-20 h-20 -mr-6 -mt-6 rounded-full opacity-10 bg-yellow-500"></div>
        <div class="relative z-10">
          <div class="flex items-center gap-2 mb-3">
            <LanIcon name="notification" :size="20" />
            <span class="text-xs text-gray-500 font-medium">中风险</span>
          </div>
          <div class="text-3xl font-bold text-gray-800">{{ stats.medium_risk_count || 0 }}</div>
        </div>
      </div>

      <div class="stat-card relative overflow-hidden group">
        <div class="absolute top-0 right-0 w-20 h-20 -mr-6 -mt-6 rounded-full opacity-10 bg-green-500"></div>
        <div class="relative z-10">
          <div class="flex items-center gap-2 mb-3">
            <LanIcon name="check" :size="20" />
            <span class="text-xs text-gray-500 font-medium">低风险</span>
          </div>
          <div class="text-3xl font-bold text-gray-800">{{ stats.low_risk_count || 0 }}</div>
        </div>
      </div>

      <div class="stat-card relative overflow-hidden group">
        <div class="absolute top-0 right-0 w-20 h-20 -mr-6 -mt-6 rounded-full opacity-10 bg-gray-400"></div>
        <div class="relative z-10">
          <div class="flex items-center gap-2 mb-3">
            <LanIcon name="time" :size="20" />
            <span class="text-xs text-gray-500 font-medium">未回复</span>
          </div>
          <div class="text-3xl font-bold text-gray-600">{{ stats.no_reply_count || 0 }}</div>
        </div>
      </div>
    </div>

    <!-- 切换栏 -->
    <div class="flex gap-1 px-4 pb-3 shrink-0">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        @click="activeTab = tab.key"
        class="lan-section-tab rounded-lg font-medium transition-all"
        :class="activeTab === tab.key
          ? 'bg-blue-50 text-blue-700 border border-blue-200'
          : 'bg-gray-100 text-gray-500 hover:bg-gray-200 border border-transparent'"
      >
        <LanIcon :name="tab.icon" />
        {{ tab.label }}
        <LanIcon v-if="tab.key === 'stats' && finalStats" name="check" class="ml-1 text-green-600" />
      </button>
    </div>

    <div v-if="activeTab !== 'stats'" class="flex-1 overflow-y-auto px-4 pb-4">
      <!-- 随访名单 Tab -->
      <div v-if="activeTab === 'patients'" class="lan-patient-results space-y-3">
        <div v-if="!filterResult" class="flex flex-col items-center justify-center py-12 text-gray-400">
          <span class="text-4xl mb-3">⏳</span>
          <span class="text-sm">等待过滤结果...</span>
        </div>
        <template v-else>
          <div>
            <div class="text-xs text-gray-500 font-medium mb-2">应随访患者 <span class="text-blue-600">{{ filterResult.send_count }} 人</span></div>
            <div class="space-y-1 max-h-60 overflow-y-auto">
              <div
                v-for="(p, i) in followupPatients"
                :key="p.patient_id"
                class="flex items-center justify-between py-1.5 px-3 rounded-lg bg-gray-50 hover:bg-gray-100"
              >
                <div class="flex items-center gap-2">
                  <span class="text-xs text-gray-400 w-6">{{ i + 1 }}</span>
                  <span class="text-xs text-gray-700">{{ p.name }}</span>
                  <span class="text-xs text-gray-400">{{ p.patient_id }}</span>
                  <span class="text-xs text-gray-400">· {{ p.diagnosis }}</span>
                </div>
                <div class="flex items-center gap-2">
                  <span
                    v-if="p.followup_detail && p.followup_detail.frequency"
                    class="text-xs px-1.5 py-0.5 rounded bg-blue-50 text-blue-600 truncate max-w-[180px]"
                    :title="p.followup_detail.frequency"
                  >{{ p.followup_detail.frequency }}</span>
                  <span class="text-xs text-gray-400">{{ p.doctor_name }}</span>
                </div>
              </div>
            </div>
          </div>

          <div v-if="filterResult.skip_count > 0">
            <div class="text-xs text-gray-500 font-medium mb-2 mt-4">免随访患者 <span class="text-yellow-600">{{ filterResult.skip_count }} 人</span></div>
            <div class="space-y-1">
              <div
                v-for="(p, i) in (filterResult.skip_details || [])"
                :key="i"
                class="flex items-center justify-between py-1.5 px-3 rounded-lg bg-gray-50"
              >
                <span class="text-xs text-gray-700">{{ p.name }} <span class="text-gray-400">{{ p.patient_id }}</span></span>
                <div class="flex items-center gap-2">
                  <span
                    v-if="p.matched_rule"
                    class="text-xs px-1.5 py-0.5 rounded bg-gray-100 text-gray-500"
                  >{{ p.matched_rule }}</span>
                  <span class="text-xs text-yellow-600">{{ p.reason }}</span>
                </div>
              </div>
            </div>
          </div>
        </template>
      </div>

      <!-- 决策日志 Tab -->
      <div v-if="activeTab === 'logs'" class="space-y-1">
        <div v-if="logs.length === 0 && !hasFollowupStarted" class="flex flex-col items-center justify-center py-12 text-gray-400">
          <LanIcon name="filePaste" :size="32" class="mb-3" />
          <span class="text-sm">暂无日志</span>
          <span class="text-xs mt-1">点击右侧"开始今日随访"启动流程</span>
        </div>
        <div v-else>
          <div
            v-for="(log, i) in reversedLogs"
            :key="log.id || i"
            class="flex items-start gap-3 py-1.5 px-3 rounded-lg hover:bg-gray-50"
          >
            <span class="text-xs text-gray-400 font-mono shrink-0">{{ log.timestamp }}</span>
            <span class="text-xs text-gray-600">{{ log.message }}</span>
          </div>
        </div>
      </div>

      <!-- 风险评分结果 Tab -->
      <div v-if="activeTab === 'scores'" class="space-y-1">
        <div v-if="alerts.length === 0" class="flex flex-col items-center justify-center py-12 text-gray-400">
          <LanIcon name="list" :size="32" class="mb-3" />
          <span class="text-sm">暂无评分数据</span>
          <span class="text-xs mt-1">随访评分完成后将显示在此</span>
        </div>
        <div v-else>
          <div
            v-for="alert in reversedAlerts"
            :key="alert.id"
            class="flex items-center gap-3 py-2 px-3 rounded-lg bg-gray-50 hover:bg-gray-100"
          >
            <span
              class="w-2 h-2 rounded-full shrink-0"
              :class="alert.risk_level === 'high' ? 'bg-red-500' : alert.risk_level === 'medium' ? 'bg-yellow-500' : alert.risk_level === 'callback' ? 'bg-orange-500' : 'bg-green-500'"
            ></span>
            <span class="text-xs text-gray-700 w-20 truncate">{{ alert.patient_name }}</span>
            <span class="text-xs text-gray-400">{{ alert.patient_id }}</span>
            <span class="text-xs font-mono font-bold text-gray-800">{{ alert.risk_score }}分</span>
            <span
              class="text-xs px-1.5 py-0.5 rounded"
              :class="alert.risk_level === 'high' ? 'bg-red-50 text-red-700' : alert.risk_level === 'medium' ? 'bg-yellow-50 text-yellow-700' : alert.risk_level === 'callback' ? 'bg-orange-50 text-orange-700' : 'bg-green-50 text-green-700'"
            >
              {{ alert.risk_level === 'high' ? '高风险' : alert.risk_level === 'medium' ? '中风险' : alert.risk_level === 'callback' ? '需回访' : '低风险' }}
            </span>
            <span class="text-xs text-gray-400 flex-1 truncate" :title="alert.reason">{{ alert.reason }}</span>
            <span class="text-xs text-gray-400">{{ alert.timestamp }}</span>
          </div>
        </div>
      </div>

    </div>

    <!-- 随访结果统计（全展开） -->
    <div v-if="activeTab === 'stats'" class="flex-1 px-4 pb-4">
      <StatsPanel v-if="finalStats" :stats="finalStats" />
      <div v-else class="flex flex-col items-center justify-center py-12 text-gray-400">
        <span class="text-4xl mb-3 animate-pulse">⏳</span>
        <span class="text-sm">等待随访结束，正在生成统计...</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import StatsPanel from './StatsPanel.vue'
import LanIcon from './LanIcon.vue'

const props = defineProps({
  stats: { type: Object, default: () => ({}) },
  logs: { type: Array, default: () => [] },
  finalStats: { type: Object, default: null },
  filterResult: { type: Object, default: null },
  alerts: { type: Array, default: () => [] },
  isRunning: { type: Boolean, default: false },
  allPatients: { type: Array, default: () => [] },
})

const activeTab = ref('logs')

watch(() => props.finalStats, (val) => {
  if (val) activeTab.value = 'stats'
})

watch(() => props.filterResult, (val) => {
  if (val) activeTab.value = 'patients'
})

const hasFollowupStarted = computed(() => props.isRunning || !!props.finalStats)

const tabs = computed(() => [
  { key: 'patients', label: '随访名单', icon: 'user', count: props.filterResult ? props.filterResult.send_count : 0 },
  { key: 'logs', label: '决策日志', icon: 'filePaste', count: props.logs.length },
  { key: 'scores', label: '风险评分结果', icon: 'notification', count: props.alerts.length },
  { key: 'stats', label: '随访结果统计', icon: 'list', count: props.finalStats ? props.finalStats.total_assessed : null },
])

const reversedLogs = computed(() => [...props.logs].slice(-50).reverse())
const reversedAlerts = computed(() => [...props.alerts].reverse())

const followupPatients = computed(() => {
  return props.allPatients.filter(p => !p.skip_follow_up)
})
</script>
