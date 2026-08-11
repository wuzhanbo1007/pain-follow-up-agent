<template>
  <div class="lan-app lan-chat h-screen w-screen flex flex-col bg-white text-gray-800 overflow-hidden">

    <!-- ================================================================ -->
    <!-- 视图 A：微信主页（会话列表）                                        -->
    <!-- ================================================================ -->
    <div v-if="!activeChat" class="flex flex-col h-full">
      <!-- 微信顶部导航 -->
      <div class="h-12 bg-[#EDEDED] flex items-center justify-between px-4 shrink-0 border-b border-[#D9D9D9]">
        <span class="text-lg font-bold text-gray-800">微信</span>
        <span class="text-xs text-gray-500">{{ wsConnected ? '已连接' : '连接中…' }}</span>
      </div>

      <!-- 搜索 -->
      <div class="px-3 py-2 bg-[#EDEDED]">
        <div class="bg-white rounded-md px-3 py-1.5 text-sm text-gray-400 border border-[#E0E0E0]">🔍 搜索</div>
      </div>

      <!-- 会话列表 -->
      <div class="flex-1 overflow-y-auto bg-white">
        <div
          v-for="c in chatList"
          :key="c.id"
          @click="enterChat(c)"
          class="flex items-center gap-3 px-4 py-3 cursor-pointer border-b border-[#F0F0F0] hover:bg-[#F5F5F5] active:bg-[#ECECEC]"
        >
          <div class="w-11 h-11 rounded-md flex items-center justify-center text-white font-semibold shrink-0 text-sm shadow-sm"
            :class="c.avatarColor"
          >
            {{ c.avatarText }}
          </div>
          <div class="flex-1 min-w-0">
            <div class="flex items-center justify-between">
              <span class="text-base font-normal text-gray-900 truncate">{{ c.name }}</span>
              <span class="text-xs text-gray-400 shrink-0 ml-2">{{ c.time }}</span>
            </div>
            <div class="text-sm text-gray-400 truncate mt-0.5">
              {{ c.lastMsg || '' }}
            </div>
          </div>
        </div>

        <!-- 空状态 -->
        <div v-if="chatList.length === 0" class="flex items-center justify-center h-64 text-gray-400 text-sm">
          <div class="text-center">
            <div class="text-4xl mb-3">💬</div>
            <div>{{ filterReady ? '暂无对话' : '等待随访启动…' }}</div>
          </div>
        </div>
      </div>

      <!-- 底部导航栏（微信四 Tab） -->
      <div class="h-12 bg-[#F7F7F7] border-t border-[#D9D9D9] flex items-center shrink-0">
        <div class="flex-1 flex flex-col items-center justify-center text-green-500">
          <span class="text-lg">💬</span><span class="text-[10px]">微信</span>
        </div>
        <div class="flex-1 flex flex-col items-center justify-center text-gray-400">
          <span class="text-lg">👥</span><span class="text-[10px]">通讯录</span>
        </div>
        <div class="flex-1 flex flex-col items-center justify-center text-gray-400">
          <span class="text-lg">🔍</span><span class="text-[10px]">发现</span>
        </div>
        <div class="flex-1 flex flex-col items-center justify-center text-gray-400">
          <span class="text-lg">👤</span><span class="text-[10px]">我</span>
        </div>
      </div>
    </div>

    <!-- ================================================================ -->
    <!-- 视图 B：对话详情                                                  -->
    <!-- ================================================================ -->
    <div v-else class="flex flex-col h-full bg-[#F5F5F5]">
      <!-- 聊天标题栏 -->
      <div class="h-12 bg-[#EDEDED] border-b border-[#D9D9D9] flex items-center px-3 shrink-0">
        <button @click="activeChat = null" class="lan-icon-btn text-gray-600 mr-2" aria-label="返回会话列表"><LanIcon name="left" :size="20" /></button>
        <span class="text-base font-bold text-gray-800 truncate">{{ activeChatName }}</span>
      </div>

      <!-- 消息区域 -->
      <div ref="chatRef" class="flex-1 overflow-y-auto px-3 py-4">
        <!-- 空 -->
        <div v-if="activeMessages.length === 0" class="flex items-center justify-center h-full text-gray-400 text-sm">
          <div class="text-center">
            <div class="text-4xl mb-3">💬</div>
            <div>暂无消息</div>
          </div>
        </div>

        <!-- 消息列表 -->
        <div v-else class="max-w-2xl mx-auto space-y-3">
          <div
            v-for="msg in activeMessages"
            :key="msg.id"
            class="flex items-end gap-2"
            :class="msg.type === 'sent' ? 'justify-start' : 'justify-end'"
          >
            <!-- 医护消息 -->
            <template v-if="msg.type === 'sent'">
              <div class="w-9 h-9 rounded-md flex items-center justify-center shrink-0 bg-blue-500 text-white text-lg">
                🩺
              </div>
              <div class="bubble-received text-sm leading-relaxed whitespace-pre-wrap">
                {{ msg.content }}
              </div>
            </template>
            <!-- 患者消息 -->
            <template v-else>
              <div class="bubble-sent text-sm leading-relaxed whitespace-pre-wrap">
                {{ msg.content }}
              </div>
              <div class="w-9 h-9 rounded-md bg-green-400 flex items-center justify-center text-white text-xs font-semibold shrink-0">
                {{ patientSurname(msg.patient_id) }}
              </div>
            </template>
          </div>
        </div>
      </div>

      <!-- 输入区域 -->
      <div class="bg-[#F7F7F7] px-3 py-2 border-t border-[#D9D9D9] shrink-0 flex items-center gap-2">
        <input
          v-model="inputText"
          type="text"
          placeholder="输入回复…"
          class="flex-1 bg-white rounded-md px-4 py-2 text-sm text-gray-700 placeholder-gray-400 border border-[#DDD] outline-none focus:border-green-400 transition"
          @keydown.enter="handleSend"
        />
        <button
          @click="handleSend"
          class="px-5 py-2 rounded-md bg-[#07C160] hover:bg-[#06AD56] text-white text-sm font-medium transition shrink-0"
        >
          发送 <LanIcon name="right" />
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { io } from 'socket.io-client'
import LanIcon from '../components/LanIcon.vue'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:5000'

const wsConnected = ref(false)
const filterReady = ref(false)
const messages = ref([])
const activeChat = ref(null)          // 当前选中对话的 patient_id
const inputText = ref('')
const chatRef = ref(null)
const demoPatientId = ref(null)       // 演示患者 ID（马淑珍）
const allPatients = ref([])

// 患者消息头像：显示患者姓氏（演示中用户扮演患者）
function patientSurname(patientId) {
  const p = allPatients.value.find(x => x.patient_id === patientId)
  return (p?.name || '患')[0]
}

let socket = null

// 诊断 → 科室映射
function getDepartment(diagnosis) {
  const map = {
    '带状疱疹后神经痛': '疼痛科',
    '癌性疼痛': '疼痛科',
    '腰椎术后疼痛': '疼痛科',
    '糖尿病周围神经痛': '疼痛科',
  }
  for (const [key, val] of Object.entries(map)) {
    if (diagnosis && diagnosis.includes(key)) return val
  }
  return '疼痛科'
}

// ===== 微信主页的会话列表（只显示演示患者的医生联系人） =====
const chatList = computed(() => {
  const map = {}
  for (const m of messages.value) {
    const key = m.patient_id
    if (!map[key]) {
      const p = allPatients.value.find(x => x.patient_id === m.patient_id)
      const isDemo = key === demoPatientId.value
      if (!isDemo) continue  // 仅显示演示患者的对话
      const diag = p?.diagnosis || ''
      const doc = p?.doctor_name || ''
      // 科室/医院优先取数据库（p.department_name/hospital_name），缺省用诊断映射兜底
      const dept = p?.department_name || getDepartment(diag)
      const hospital = p?.hospital_name || '协和医院'
      map[key] = {
        id: key,
        name: `${hospital}${dept} ${doc}`,
        avatarColor: 'bg-green-500',
        avatarText: '医',
        lastMsg: '',
        time: '',
      }
    }
    // 更新最后一条消息摘要
    const prefix = m.type === 'sent' ? '' : '我: '
    map[key].lastMsg = prefix + (m.content || '').substring(0, 20)
    map[key].time = m.timestamp || ''
  }
  return Object.values(map)
})

// ===== 对话详情 =====
const activeChatName = computed(() => {
  const c = chatList.value.find(x => x.id === activeChat.value)
  return c?.name || '疼痛随访助手'
})

const activeMessages = computed(() => {
  return activeChat.value
    ? messages.value.filter(m => m.patient_id === activeChat.value)
    : []
})

function enterChat(c) {
  activeChat.value = c.id
  inputText.value = ''
}

function handleSend() {
  if (!inputText.value.trim() || !demoPatientId.value) return
  socket?.emit('demo:simulate_reply', {
    patient_id: demoPatientId.value,
    reply_text: inputText.value.trim(),
  })
  inputText.value = ''
}

watch(activeMessages, async () => {
  await nextTick()
  if (chatRef.value) chatRef.value.scrollTop = chatRef.value.scrollHeight
}, { deep: true })

// ===== 初始化 =====
onMounted(async () => {
  try {
    const res = await fetch(`${API_BASE}/api/patients`)
    allPatients.value = await res.json()
  } catch (e) {}

  socket = io(API_BASE, { transports: ['websocket', 'polling'], reconnection: true })
  socket.on('connect', () => { wsConnected.value = true; socket.emit('chat:request_sync') })
  socket.on('disconnect', () => { wsConnected.value = false })

  socket.on('agent:filter_result', () => { filterReady.value = true })

  socket.on('agent:state_change', (data) => {
    if (data.to === 'FETCHING') messages.value = []
    if (!filterReady.value && data.to && !['IDLE', 'FETCHING'].includes(data.to)) {
      filterReady.value = true
    }
  })

  // 演示患者选中 → 绑定为马淑珍
  socket.on('demo:patients_selected', (data) => {
    if (data.demo_patient_ids?.length) {
      demoPatientId.value = data.demo_patient_ids[0]
    }
  })

  // 医护消息（只收演示患者的）
  socket.on('agent:wechat_sent', (data) => {
    if (demoPatientId.value && data.patient_id !== demoPatientId.value) return
    messages.value = [...messages.value, {
      id: Date.now() + Math.random(),
      type: 'sent',
      patient_id: data.patient_id,
      patient_name: data.patient_name,
      content: data.message,
    }]
  })

  socket.on('agent:clarification', (data) => {
    if (demoPatientId.value && data.patient_id !== demoPatientId.value) return
    messages.value = [...messages.value, {
      id: Date.now() + Math.random(),
      type: 'sent',
      patient_id: data.patient_id,
      patient_name: data.patient_name,
      content: data.message,
    }]
  })

  // 患者回复
  socket.on('patient:reply', (data) => {
    if (demoPatientId.value && data.patient_id !== demoPatientId.value) return
    messages.value = [...messages.value, {
      id: Date.now() + Math.random(),
      type: 'reply',
      patient_id: data.patient_id,
      patient_name: data.patient_name || '',
      content: data.text,
    }]
  })
})

onUnmounted(() => {
  if (socket) { socket.disconnect(); socket = null }
})
</script>

<style scoped>
.bubble-received {
  background: #fff;
  border-radius: 4px 12px 12px 12px;
  padding: 8px 12px;
  max-width: 70%;
  word-break: break-word;
  margin-left: 8px;
}
.bubble-sent {
  background: #95EC69;
  border-radius: 12px 4px 12px 12px;
  padding: 8px 12px;
  max-width: 70%;
  word-break: break-word;
  margin-right: 8px;
}
</style>
