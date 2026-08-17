// frontend/src/composables/useWebSocket.js
import { ref } from 'vue'
import { io } from 'socket.io-client'

/**
 * WebSocket 单例（说明书 15.3：每页面一个连接）。
 *
 * 不再把连接绑定到组件生命周期（旧 useWebSocket 每组件一个 socket，
 * 页面多组件会连出多路）。改为模块级单例：Pinia store 通过 initSocket()
 * 在页面入口只连一次，所有组件共享同一连接与同一批事件。
 *
 * 事件契约与后端 routes/ws.py + infrastructure/messaging/outbox.py 对应：
 *  新架构事件 dispatch:* / episode:* / review:ready / sync:projection；
 *  过渡期兼容旧事件 agent:* / patient:* / demo:*（Outbox 仍会推送）。
 */
const SOCKET_URL =
  import.meta.env.VITE_WS_URL ||
  import.meta.env.VITE_API_BASE ||
  'http://localhost:5000'

let socket = null
let wiredStore = null
const connected = ref(false)

export function getSocket() {
  return socket
}

export function getWsConnected() {
  return connected
}

export function send(event, data = {}) {
  if (socket?.connected) {
    socket.emit(event, data)
    return true
  }
  return false
}

function wireStore(store) {
  if (!store || store === wiredStore) return
  wiredStore = store

  // ===== 新架构事件（说明书 8.4 / 10.3 事件契约）=====
  socket.on('connection:ready', (d) => store.handleConnectionReady?.(d))
  socket.on('dispatch:started', (d) => store.handleDispatchStarted?.(d))
  socket.on('dispatch:state_changed', (d) => store.handleDispatchStateChanged?.(d))
  socket.on('dispatch:roster_ready', (d) => store.handleDispatchRosterReady?.(d))
  socket.on('dispatch:report_ready', (d) => store.handleDispatchReportReady?.(d))
  socket.on('episode:state_changed', (d) => store.handleEpisodeStateChanged?.(d))
  socket.on('episode:decision', (d) => store.handleEpisodeDecision?.(d))
  socket.on('episode:message', (d) => store.handleEpisodeMessage?.(d))
  socket.on('episode:waiting_human', (d) => store.handleEpisodeWaitingHuman?.(d))
  socket.on('episode:report_ready', (d) => store.handleEpisodeReportReady?.(d))
  socket.on('review:ready', (d) => store.handleReviewReady?.(d))
  socket.on('sync:projection', (d) => store.handleSyncProjection?.(d))

  // ===== 过渡期兼容旧事件（Outbox 仍按旧名推送）=====
  socket.on('agent:state_change', (d) => store.handleStateChange?.(d))
  socket.on('agent:log', (d) => store.addLog?.(d))
  socket.on('agent:filter_result', (d) => store.handleFilterResult?.(d))
  socket.on('agent:progress', (d) => store.handleProgress?.(d))
  socket.on('agent:risk_scored', (d) => store.handleRiskScored?.(d))
  socket.on('agent:wechat_sent', (d) => store.addWechatMessage?.(d))
  socket.on('agent:clarification', (d) => store.addClarification?.(d))
  socket.on('patient:reply', (d) => store.handlePatientReply?.(d))
  socket.on('doctor:alert', (d) => store.handleDoctorAlert?.(d))
  socket.on('doctor:callback_alert', (d) => store.handleCallbackAlert?.(d))
  socket.on('agent:final_stats', (d) => store.handleFinalStats?.(d))
  socket.on('agent:reset', () => store.handleAgentReset?.())
  socket.on('demo:patients_selected', (d) => store.handleDemoPatientsSelected?.(d))
}

/**
 * 建立全局唯一连接并接线到 store（幂等：已有连接只重接线，不重复建连）。
 * 重连成功后自动 chat:request_sync，补齐错过的调度/消息投影。
 */
export function connectWebSocket(store) {
  if (socket) {
    wireStore(store)
    return socket
  }
  socket = io(SOCKET_URL, {
    transports: ['websocket', 'polling'],
    reconnection: true,
  })
  connected.value = socket.connected
  socket.on('connect', () => {
    connected.value = true
    console.log('[WS] 已连接:', socket.id)
    socket.emit('chat:request_sync')
  })
  socket.on('disconnect', (reason) => {
    connected.value = false
    console.log('[WS] 断开:', reason)
  })
  wireStore(store)
  return socket
}

export function disconnectWebSocket() {
  if (socket) socket.disconnect()
  socket = null
  wiredStore = null
  connected.value = false
}
