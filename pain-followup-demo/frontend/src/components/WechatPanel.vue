<template>
  <Transition name="panel-slide">
    <div
      v-if="open"
      class="fixed bottom-24 left-6 z-40 w-[400px] h-[70vh] bg-white rounded-2xl shadow-2xl overflow-hidden flex flex-col"
    >
      <!-- 微信顶部栏 -->
      <div class="bg-gray-800 text-white px-4 py-3 flex items-center justify-between shrink-0">
        <div class="flex items-center gap-3">
          <button @click="activeChat = null" class="lan-icon-btn text-white hover:text-gray-300" aria-label="返回"><LanIcon name="left" :size="20" /></button>
          <div>
            <div class="font-semibold text-sm">{{ activePatient ? activePatient.name : '疼痛随访助手' }}</div>
            <div class="text-xs text-gray-400">在线</div>
          </div>
        </div>
        <button @click="$emit('close')" class="lan-icon-btn text-white hover:text-gray-300" aria-label="关闭"><LanIcon name="close" :size="20" /></button>
      </div>

      <!-- 患者列表（无聊天时显示） -->
      <div v-if="!activePatient" class="flex-1 overflow-y-auto bg-gray-50">
        <div v-if="!showPatients" class="flex items-center justify-center h-40 text-gray-400 text-sm">
          <div class="text-center">
            <div class="text-4xl mb-3">⏳</div>
            <div>等待拉取随访名单...</div>
          </div>
        </div>
        <div v-else-if="sortedPatients.length === 0" class="flex items-center justify-center h-40 text-gray-400 text-sm">
          <div class="text-center">
            <div class="text-5xl mb-4">💬</div>
            <div class="text-sm">暂无患者</div>
          </div>
        </div>
        <div v-else>
          <div
            v-for="patient in sortedPatients"
            :key="patient.patient_id"
            @click="activeChat = patient.patient_id"
            class="flex items-center gap-3 px-4 py-3 cursor-pointer border-b border-gray-100 transition-colors hover:bg-gray-100"
          >
            <div
              class="w-10 h-10 rounded-full flex items-center justify-center text-white font-semibold shrink-0"
              :class="getDiagColor(patient.diagnosis)"
            >
              {{ patient.name?.[0] }}
            </div>
            <div class="flex-1 min-w-0">
              <div class="flex items-center justify-between">
                <span class="text-sm font-medium text-gray-800 truncate">
                  <span v-if="demoPatientIds.includes(patient.patient_id)" class="text-xs text-orange-500 mr-1">⭐</span>
                  {{ patient.name }}
                  <span class="text-xs text-gray-500 ml-1">（{{ patient.diagnosis }}）</span>
                </span>
                <span class="text-xs text-gray-400 ml-1 shrink-0">{{ patient.patient_id }}</span>
              </div>
              <div class="text-xs text-gray-400 truncate">
                {{ getLatestMessageText(patient.patient_id) }}
              </div>
            </div>
            <span
              v-if="patientHasMessages(patient.patient_id)"
              class="w-2 h-2 bg-green-500 rounded-full shrink-0"
            ></span>
          </div>
        </div>
      </div>

      <!-- 对话区域 -->
      <div v-if="activePatient" class="flex-1 flex flex-col bg-gray-100 min-h-0 overflow-hidden">
        <div ref="chatRef" class="flex-1 overflow-y-auto p-4 space-y-3 bg-[#ededed]">
          <div v-if="filteredMessages.length === 0" class="flex items-center justify-center h-full text-gray-400 text-sm">
            <div class="text-center">
              <div class="text-4xl mb-3">💬</div>
              <div>等待消息中...</div>
            </div>
          </div>

          <TransitionGroup v-else name="msg" tag="div" class="space-y-3">
            <div
              v-for="msg in filteredMessages"
              :key="msg.id"
              class="flex"
              :class="msg.type === 'sent' ? 'justify-start' : 'justify-end'"
            >
              <!-- 医护智能体消息：左侧显示 -->
              <template v-if="msg.type === 'sent'">
                <div class="w-8 h-8 rounded-full bg-gradient-to-br from-blue-400 to-blue-600 flex items-center justify-center text-white text-xs font-semibold shrink-0 mb-1 shadow-sm" title="医护智能体">🩺</div>
                <div class="bubble-received text-sm leading-relaxed whitespace-pre-wrap">{{ msg.content }}</div>
              </template>
              <!-- 患者消息：右侧显示 -->
              <template v-else>
                <div class="bubble-sent text-sm leading-relaxed whitespace-pre-wrap">{{ msg.content }}</div>
                <div class="w-8 h-8 rounded-full bg-teal-400 flex items-center justify-center text-white text-xs font-semibold shrink-0 mb-1 shadow-sm">
                  {{ (msg.patient_name || '患')[0] }}
                </div>
              </template>
            </div>
          </TransitionGroup>
        </div>

        <div class="bg-gray-50 px-4 py-3 border-t border-gray-200 shrink-0">
          <div class="flex items-center gap-2">
            <input
              v-model="inputText"
              type="text"
              placeholder="输入回复..."
              class="flex-1 bg-white rounded-full px-4 py-2 border border-gray-200 text-sm text-gray-700 outline-none focus:border-green-400"
              @keydown.enter="handleSend"
            />
            <button
              @click="handleSend"
              class="w-9 h-9 rounded-full bg-green-500 hover:bg-green-600 flex items-center justify-center text-white transition"
            >
              <LanIcon name="right" />
            </button>
          </div>
        </div>
      </div>
    </div>
  </Transition>
</template>

<script setup>
import { ref, computed, watch, nextTick } from 'vue'
import LanIcon from './LanIcon.vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  patients: { type: Array, default: () => [] },
  messages: { type: Array, default: () => [] },
  showPatients: { type: Boolean, default: true },
  onManualReply: { type: Function, default: () => {} },
  demoPatientIds: { type: Array, default: () => [] },
})

const emit = defineEmits(['close', 'manualReply'])

const activeChat = ref(null)
const inputText = ref('')
const chatRef = ref(null)

const DIAG_COLORS = {
  '癌性疼痛': 'bg-red-400',
  '带状疱疹后神经痛': 'bg-orange-400',
  '腰椎术后疼痛': 'bg-blue-400',
  '糖尿病周围神经痛': 'bg-purple-400',
}

function getDiagColor(diagnosis) {
  return DIAG_COLORS[diagnosis] || 'bg-gray-400'
}

// 演示患者置顶排序
const sortedPatients = computed(() => {
  const demoSet = new Set(props.demoPatientIds)
  const demo = props.patients.filter(p => demoSet.has(p.patient_id))
  const rest = props.patients.filter(p => !demoSet.has(p.patient_id))
  return [...demo, ...rest]
})

const filteredMessages = computed(() => {
  return activeChat.value
    ? props.messages.filter(m => m.patient_id === activeChat.value)
    : []
})

const activePatient = computed(() => {
  return activeChat.value
    ? sortedPatients.value.find(p => p.patient_id === activeChat.value)
    : null
})

function patientHasMessages(pid) {
  return props.messages.some(m => m.patient_id === pid)
}

function getLatestMessageText(pid) {
  const patientMsgs = props.messages.filter(m => m.patient_id === pid)
  const latest = patientMsgs[patientMsgs.length - 1]
  if (!latest) return ''
  return (latest.type === 'reply' ? `${latest.patient_name || ''}: ` : '') + (latest.content || '')
}

function handleSend() {
  if (inputText.value.trim()) {
    emit('manualReply', activePatient.value, inputText.value.trim())
    inputText.value = ''
  }
}

// 自动滚动到底部
watch(filteredMessages, async () => {
  await nextTick()
  if (chatRef.value) {
    const el = chatRef.value
    const isAtBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60
    if (isAtBottom) {
      el.scrollTop = el.scrollHeight
    }
  }
}, { deep: true })
</script>

<style scoped>
.panel-slide-enter-active {
  transition: all 0.4s cubic-bezier(0.34, 1.56, 0.64, 1);
}
.panel-slide-leave-active {
  transition: all 0.3s ease-in;
}
.panel-slide-enter-from {
  opacity: 0;
  transform: translateY(100px) scale(0.95);
}
.panel-slide-leave-to {
  opacity: 0;
  transform: translateY(100px) scale(0.95);
}

.msg-enter-active {
  transition: all 0.4s cubic-bezier(0.34, 1.56, 0.64, 1);
}
.msg-enter-from {
  opacity: 0;
  transform: translateY(20px) scale(0.95);
}
</style>
