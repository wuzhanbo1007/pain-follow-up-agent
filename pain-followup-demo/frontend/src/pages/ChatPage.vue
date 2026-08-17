<!-- frontend/src/pages/ChatPage.vue -->
<template>
  <div class="lan-app lan-chat h-screen w-screen flex flex-col bg-white text-gray-800 overflow-hidden">

    <!-- ================================================================ -->
    <!-- 视图 A：微信主页（会话列表）                                        -->
    <!-- ================================================================ -->
    <div v-if="!activeChat" class="flex flex-col h-full">
      <!-- 微信顶部导航 -->
      <div class="h-12 bg-[#EDEDED] flex items-center justify-between px-4 shrink-0 border-b border-[#D9D9D9]">
        <span class="text-lg font-bold text-gray-800">微信</span>
        <span class="text-xs text-gray-500">{{ store.wsConnected ? '已连接' : '连接中…' }}</span>
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
            <div class="flex items-center gap-2 mt-0.5">
              <span class="text-sm text-gray-400 truncate flex-1">{{ c.lastMsg || '' }}</span>
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
          placeholder="输入消息"
          class="flex-1 bg-white rounded-md px-4 py-2 text-sm text-gray-700 placeholder-gray-400 border border-[#DDD] outline-none focus:border-green-400 transition"
          @keydown.enter="handleSend"
        />
        <button
          @click="handleSend"
          :disabled="!activeWaiting || sending"
          class="px-5 py-2 rounded-md bg-[#07C160] hover:bg-[#06AD56] text-white text-sm font-medium transition shrink-0 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <span v-if="sending">发送中…</span>
          <span v-else>发送 <LanIcon name="right" /></span>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, nextTick } from 'vue'
import { useDemoStore } from '../stores/demoStore'
import LanIcon from '../components/LanIcon.vue'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:5000'

const store = useDemoStore()

const filterReady = ref(false)
const activeChat = ref(null)          // 当前选中对话的 patient_id
const inputText = ref('')
const chatRef = ref(null)
const sending = ref(false)
const allPatients = ref([])

// 患者消息头像：显示患者姓氏（演示中用户扮演患者）
function patientSurname(patientId) {
  const p = allPatients.value.find(x => String(x.patient_id) === String(patientId))
  return (p?.name || '患')[0]
}

// 科室必须来自患者绑定的主治医生；后端未提供时只使用通用兜底，
// 不再根据诊断猜测科室，避免出现“医生姓名和科室不匹配”。
function getDepartment(_diagnosis) {
  return '疼痛科'
}

// 每个患者对应的等待人工回复 episode（若有）
function waitingByPatient(pid) {
  return store.waitingEpisodes.find(e => String(e.patient_id) === String(pid)) || null
}

// ===== 微信主页的会话列表（按患者分组，等待人工回复的排前） =====
const chatList = computed(() => {
  const map = {}
  // 微信端只展示人工模拟患者；自动患者的对话由主页面决策日志展示。
  const roster = store.filterResult?.send_list || []
  const manualIds = new Set([
    ...store.demoPatients
      .filter(id => !roster.some(patient => String(patient.patient_id) === String(id) && patient.phone_callback))
      .map(id => String(id)),
    ...roster
      .filter(patient => patient.input_source === 'human' && !patient.phone_callback)
      .map(patient => String(patient.patient_id)),
  ])
  const rosterPatients = roster.filter(patient =>
    !patient.phone_callback && manualIds.has(String(patient.patient_id)))
  for (const patient of rosterPatients) {
    const key = patient.patient_id
    if (key == null) continue
    const p = allPatients.value.find(x => String(x.patient_id) === String(key)) || patient
    const diag = p?.diagnosis || ''
    const doc = p?.doctor_name || ''
    const dept = p?.department_name || getDepartment(diag)
    const hospital = p?.hospital_name || '协和医院'
    const isDemo = store.demoPatients.some(d => String(d) === String(key))
    map[key] = {
      id: key,
      name: `${hospital}${dept} ${doc}`,
      avatarColor: 'bg-green-500',
      avatarText: '医',
      lastMsg: '',
      time: '',
      isDemo,
      waiting: !!waitingByPatient(key),
    }
  }
  for (const m of store.messages) {
    const key = m.patient_id
    if (key == null) continue
    if (!manualIds.has(String(key))) continue
    if (!map[key]) {
      const p = allPatients.value.find(x => String(x.patient_id) === String(key))
      const diag = p?.diagnosis || ''
      const doc = p?.doctor_name || ''
      const dept = p?.department_name || getDepartment(diag)
      const hospital = p?.hospital_name || '协和医院'
      const isDemo = store.demoPatients.some(d => String(d) === String(key))
      map[key] = {
        id: key,
        name: `${hospital}${dept} ${doc}`,
        avatarColor: 'bg-green-500',
        avatarText: '医',
        lastMsg: '',
        time: '',
        isDemo,
        waiting: !!waitingByPatient(key),
      }
    }
    const prefix = m.type === 'sent' ? '' : '我: '
    // 列表中保留医护原始消息，不截断、不改写为摘要；长消息由列表项自动换行展示。
    map[key].lastMsg = prefix + (m.content || '')
    map[key].time = m.timestamp || ''
    if (waitingByPatient(key)) map[key].waiting = true
  }
  return Object.values(map).sort((a, b) => {
    if (a.waiting !== b.waiting) return a.waiting ? -1 : 1
    return 0
  })
})

// ===== 对话详情 =====
const activeChatName = computed(() => {
  const c = chatList.value.find(x => x.id === activeChat.value)
  return c?.name || '疼痛随访助手'
})

const activeWaiting = computed(() => waitingByPatient(activeChat.value))

const activeMessages = computed(() => {
  return activeChat.value
    ? store.messages.filter(m => String(m.patient_id) === String(activeChat.value))
    : []
})

function enterChat(c) {
  activeChat.value = c.id
  inputText.value = ''
}

async function handleSend() {
  const waiting = activeWaiting.value
  if (!inputText.value.trim() || !waiting || sending.value) return
  sending.value = true
  const pid = activeChat.value
  const text = inputText.value.trim()
  const p = allPatients.value.find(x => String(x.patient_id) === String(pid))
  try {
    await store.resumeEpisode(waiting.episode_id, text)
    // 乐观追加患者回复，避免等待后端重新广播
    store.addMessage({
      type: 'reply', patient_id: pid,
      patient_name: p?.name || '', content: text,
      timestamp: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
    })
    inputText.value = ''
  } catch (e) {
    console.error('发送失败:', e)
  } finally {
    sending.value = false
  }
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
  } catch (e) { /* 非阻断 */ }

  // 收到名单后标记已就绪（过滤了空名单展示文案）
  const unsub = watch(() => store.dispatch.status, (s) => {
    if (s && s !== 'created' && s !== 'idle') {
      filterReady.value = true
      unsub()
    }
  }, { immediate: true })
  if (store.messages.length) filterReady.value = true
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
