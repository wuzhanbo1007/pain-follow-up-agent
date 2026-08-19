<!-- frontend/src/components/DoctorDashboard.vue -->
<template>
  <div class="flex flex-col h-full">
    <!-- 顶部标题栏 -->
    <div class="flex items-center justify-between px-5 py-4 border-b border-gray-700/50 shrink-0">
      <div>
        <h2 class="text-lg font-bold text-white">医生管理后台</h2>
        <p class="text-xs text-gray-500">实时监控与预警</p>
      </div>
      <div class="flex items-center gap-2 text-xs text-gray-500">
        <span class="w-2 h-2 bg-green-400 rounded-full animate-pulse"></span>
        <span>实时连接</span>
      </div>
    </div>

    <!-- 统计卡片 -->
    <div class="grid grid-cols-5 gap-2.5 p-4 shrink-0">
      <div class="stat-card bg-gradient-to-br from-gray-800/80 to-gray-900/80 border border-gray-700/50 rounded-2xl p-5 relative overflow-hidden group">
        <div class="absolute top-0 right-0 w-20 h-20 -mr-6 -mt-6 rounded-full opacity-10 bg-blue-500"></div>
        <div class="relative z-10">
          <div class="flex items-center gap-2 mb-3">
            <span class="text-xl">👥</span>
            <span class="text-xs text-gray-400 font-medium">应随访</span>
          </div>
          <div class="text-3xl font-bold text-white">{{ stats.send_count || stats.total_patients }}</div>
        </div>
      </div>

      <div class="stat-card bg-gradient-to-br from-gray-800/80 to-gray-900/80 border border-gray-700/50 rounded-2xl p-5 relative overflow-hidden group">
        <div class="absolute top-0 right-0 w-20 h-20 -mr-6 -mt-6 rounded-full opacity-10 bg-cyan-500"></div>
        <div class="relative z-10">
          <div class="flex items-center gap-2 mb-3">
            <span class="text-xl">📤</span>
            <span class="text-xs text-gray-400 font-medium">已发送</span>
          </div>
          <div class="text-3xl font-bold text-white">{{ stats.sent_count }}</div>
          <div class="text-xs text-gray-500 mt-1">待发送 {{ stats.send_count }} 人 / 免随访 {{ stats.skip_count }} 人</div>
        </div>
      </div>

      <div class="stat-card bg-gradient-to-br from-gray-800/80 to-gray-900/80 border border-gray-700/50 rounded-2xl p-5 relative overflow-hidden group">
        <div class="absolute top-0 right-0 w-20 h-20 -mr-6 -mt-6 rounded-full opacity-10 bg-red-500"></div>
        <div class="relative z-10">
          <div class="flex items-center gap-2 mb-3">
            <span class="text-xl">🔴</span>
            <span class="text-xs text-gray-400 font-medium">高风险</span>
          </div>
          <div class="text-3xl font-bold text-white">{{ stats.high_risk_count }}</div>
        </div>
      </div>

      <div class="stat-card bg-gradient-to-br from-gray-800/80 to-gray-900/80 border border-gray-700/50 rounded-2xl p-5 relative overflow-hidden group">
        <div class="absolute top-0 right-0 w-20 h-20 -mr-6 -mt-6 rounded-full opacity-10 bg-yellow-500"></div>
        <div class="relative z-10">
          <div class="flex items-center gap-2 mb-3">
            <span class="text-xl">🟡</span>
            <span class="text-xs text-gray-400 font-medium">中风险</span>
          </div>
          <div class="text-3xl font-bold text-white">{{ stats.medium_risk_count }}</div>
        </div>
      </div>

      <div class="stat-card bg-gradient-to-br from-gray-800/80 to-gray-900/80 border border-gray-700/50 rounded-2xl p-5 relative overflow-hidden group">
        <div class="absolute top-0 right-0 w-20 h-20 -mr-6 -mt-6 rounded-full opacity-10 bg-green-500"></div>
        <div class="relative z-10">
          <div class="flex items-center gap-2 mb-3">
            <span class="text-xl">🟢</span>
            <span class="text-xs text-gray-400 font-medium">低风险</span>
          </div>
          <div class="text-3xl font-bold text-white">{{ stats.low_risk_count }}</div>
        </div>
      </div>
    </div>

    <!-- Tab 切换 -->
    <div class="flex gap-1 px-4 pb-3 shrink-0">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        @click="activeTab = tab.key"
        class="px-4 py-1.5 rounded-lg text-xs font-medium transition-all"
        :class="activeTab === tab.key
          ? 'bg-blue-500/20 text-blue-400 border border-blue-500/30'
          : 'bg-gray-800/50 text-gray-400 hover:bg-gray-800 border border-transparent'"
      >
        {{ tab.label }}
        <span
          v-if="tab.count > 0"
          class="ml-1.5 px-1.5 py-0.5 rounded-full text-xs"
          :class="activeTab === tab.key ? 'bg-blue-500/30' : 'bg-gray-700'"
        >{{ tab.count }}</span>
      </button>
    </div>

    <!-- 内容区域 -->
    <div class="flex-1 overflow-y-auto px-4 pb-4">
      <!-- 随访记录 Tab -->
      <div v-if="activeTab === 'alerts'" class="space-y-3">
        <div v-if="alerts.length === 0" class="flex flex-col items-center justify-center py-12 text-gray-500">
          <span class="text-4xl mb-3">✅</span>
          <span class="text-sm">暂无预警</span>
        </div>
        <TransitionGroup v-else name="alert-card" tag="div" class="space-y-3">
          <div
            v-for="alert in alerts"
            :key="alert.id"
            class="border rounded-xl p-4 mb-3"
            :class="getAlertColors(alert.risk_level).bg + ' ' + getAlertColors(alert.risk_level).border"
          >
            <div class="flex items-start gap-3">
              <div class="w-10 h-10 rounded-full flex items-center justify-center shrink-0" :class="getAlertColors(alert.risk_level).iconBg">
                <span class="text-lg">{{ getAlertColors(alert.risk_level).icon }}</span>
              </div>
              <div class="flex-1 min-w-0">
                <div class="flex items-center gap-2 mb-1">
                  <span class="font-semibold text-sm" :class="getAlertColors(alert.risk_level).text">{{ alert.patient_name }}</span>
                  <span
                    class="px-2 py-0.5 rounded-full text-xs font-medium border"
                    :class="getAlertColors(alert.risk_level).badgeClass || 'bg-green-500/20 text-green-400 border-green-500/30'"
                  >{{ getAlertLevelLabel(alert.risk_level) }}</span>
                </div>
                <div v-if="alert.risk_level !== 'callback'" class="text-xs text-gray-400 mb-1">
                  {{ alert.doctor_name ? `${alert.doctor_name} · ` : '' }}NRS: {{ alert.nrs }} · 风险评分: {{ alert.risk_score }}分
                </div>
                <div v-if="alert.risk_level === 'callback'" class="text-xs text-gray-400 mb-1">
                  {{ alert.doctor_name }} · {{ alert.diagnosis }}
                </div>
                <div class="text-xs" :class="getAlertColors(alert.risk_level).text + '/80'">{{ alert.reason }}</div>
                <div class="text-xs text-gray-500 mt-1">{{ alert.timestamp }}</div>
              </div>
            </div>
          </div>
        </TransitionGroup>
      </div>

      <!-- 随访名单 Tab -->
      <div v-if="activeTab === 'patients'" class="space-y-4">
        <div v-if="!filterResult" class="flex flex-col items-center justify-center py-12 text-gray-500">
          <span class="text-4xl mb-3">⏳</span>
          <span class="text-sm">等待过滤结果...</span>
        </div>
        <template v-else>
          <div>
            <div class="text-xs text-gray-500 font-medium mb-2 flex items-center gap-2">
              <span>📤</span> 应随访患者
              <span class="ml-auto text-blue-400">{{ filterResult.send_count }} 人</span>
            </div>
            <div class="bg-gray-800/30 rounded-lg p-3">
              <div class="text-xs text-gray-400">共 {{ filterResult.send_count }} 位患者需要发送随访消息</div>
            </div>
          </div>
          <div v-if="filterResult.skip_count > 0">
            <div class="text-xs text-gray-500 font-medium mb-2 flex items-center gap-2">
              <span>🚫</span> 免随访患者
              <span class="ml-auto text-yellow-400">{{ filterResult.skip_count }} 人</span>
            </div>
            <div class="space-y-1">
              <div
                v-for="(p, i) in (filterResult.skip_details || [])"
                :key="i"
                class="flex items-center justify-between gap-3 py-2 px-3 rounded-lg bg-gray-800/30"
              >
                <div class="flex items-center gap-1 min-w-0 truncate">
                  <span class="text-xs text-white shrink-0">{{ p.name }}</span>
                  <span class="text-xs text-gray-500 truncate">· {{ p.diagnosis || '未填写诊断' }}</span>
                </div>
                <span class="text-xs text-yellow-400 shrink-0">{{ p.reason }}</span>
              </div>
            </div>
          </div>
          <div v-else class="bg-gray-800/30 rounded-lg p-4 text-center">
            <span class="text-xs text-gray-500">✅ 全部患者均需随访，无免随访患者</span>
          </div>
        </template>
      </div>

      <!-- 决策日志 Tab -->
      <div v-if="activeTab === 'logs'" class="space-y-1">
        <div v-if="logs.length === 0" class="flex flex-col items-center justify-center py-12 text-gray-500">
          <span class="text-4xl mb-3">📋</span>
          <span class="text-sm">暂无日志</span>
          <span class="text-xs mt-1">智能体活动将实时记录在此</span>
        </div>
        <TransitionGroup v-else name="log-item" tag="div" class="space-y-1">
          <div
            v-for="(log, i) in reversedLogs"
            :key="log.id || i"
            class="flex items-start gap-3 py-2 px-3 rounded-lg hover:bg-gray-800/30 transition-colors"
          >
            <span class="text-xs text-gray-500 font-mono shrink-0 mt-0.5">{{ log.timestamp }}</span>
            <div class="flex-1 min-w-0">
              <div class="text-xs text-gray-300">{{ log.message }}</div>
              <span v-if="log.state" class="text-xs text-gray-600 font-mono">{{ log.state }}</span>
            </div>
          </div>
        </TransitionGroup>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  stats: { type: Object, default: () => ({}) },
  alerts: { type: Array, default: () => [] },
  logs: { type: Array, default: () => [] },
  filterResult: { type: Object, default: null },
})

const activeTab = ref('alerts')

const tabs = computed(() => [
  { key: 'alerts', label: '随访记录', count: props.alerts.length },
  { key: 'patients', label: '随访名单', count: props.filterResult ? props.filterResult.send_count : 0 },
  { key: 'logs', label: '决策日志', count: props.logs.length },
])

const reversedLogs = computed(() => [...props.logs].reverse())

const alertColorMap = {
  callback: { bg: 'bg-orange-500/10', border: 'border-orange-500/30', iconBg: 'bg-orange-500/20', text: 'text-orange-400', icon: '📞', badgeClass: 'bg-orange-500/20 text-orange-400 border-orange-500/30' },
  high:     { bg: 'bg-red-500/10', border: 'border-red-500/30', iconBg: 'bg-red-500/20', text: 'text-red-400', icon: '🚨', badgeClass: 'bg-red-500/20 text-red-400 border-red-500/30' },
  medium:   { bg: 'bg-yellow-500/10', border: 'border-yellow-500/30', iconBg: 'bg-yellow-500/20', text: 'text-yellow-400', icon: '⚠️', badgeClass: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30' },
  low:      { bg: 'bg-green-500/10', border: 'border-green-500/30', iconBg: 'bg-green-500/20', text: 'text-green-400', icon: '✅', badgeClass: 'bg-green-500/20 text-green-400 border-green-500/30' },
}

function getAlertColors(level) {
  return alertColorMap[level] || alertColorMap.low
}

function getAlertLevelLabel(level) {
  if (level === 'high') return '高风险'
  if (level === 'medium') return '中风险'
  if (level === 'callback') return '需回访'
  return '低风险'
}
</script>

<style scoped>
.alert-card-enter-active {
  transition: all 0.4s ease-out;
}
.alert-card-leave-active {
  transition: all 0.3s ease-in;
}
.alert-card-enter-from {
  opacity: 0;
  transform: translateX(100px);
}
.alert-card-leave-to {
  opacity: 0;
  transform: translateX(-100px);
}
.log-item-enter-active {
  transition: all 0.3s ease-out;
}
.log-item-enter-from {
  opacity: 0;
  transform: translateX(-10px);
}
</style>
