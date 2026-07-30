import { ref, onMounted, onUnmounted, watch } from 'vue'
import { io } from 'socket.io-client'

const SOCKET_URL = import.meta.env.VITE_WS_URL || 'http://localhost:5000'

/**
 * WebSocket composable for Vue 3
 * @param {Object} handlers - Event handler callbacks
 * @param {boolean} enabled - Whether to connect
 * @returns {{ send: Function, connected: import('vue').Ref<boolean> }}
 */
export function useWebSocket(handlers = {}, enabled = true) {
  const socketRef = ref(null)
  const connected = ref(false)

  // Store latest handlers so socket callbacks stay fresh
  const handlerProxy = new Proxy(handlers, {
    get(target, prop) {
      return target[prop]
    },
  })

  function send(event, data = {}) {
    if (socketRef.value?.connected) {
      socketRef.value.emit(event, data)
      return true
    }
    return false
  }

  function setupSocket() {
    if (!enabled) return

    const socket = io(SOCKET_URL, {
      transports: ['websocket', 'polling'],
      reconnection: true,
      reconnectionAttempts: 10,
      reconnectionDelay: 1000,
    })
    socketRef.value = socket

    socket.on('connect', () => {
      connected.value = true
      console.log('[WS] 已连接:', socket.id)
      handlers.onConnect?.()
    })

    socket.on('disconnect', (reason) => {
      connected.value = false
      console.log('[WS] 断开:', reason)
      handlers.onDisconnect?.(reason)
    })

    socket.on('agent:state_change', (data) => {
      handlers.onStateChange?.(data)
    })

    socket.on('agent:log', (data) => {
      handlers.onLog?.(data)
    })

    socket.on('agent:progress', (data) => {
      handlers.onProgress?.(data)
    })

    socket.on('agent:tool_call', (data) => {
      handlers.onToolCall?.(data)
    })

    socket.on('patient:reply', (data) => {
      handlers.onPatientReply?.(data)
    })

    socket.on('doctor:alert', (data) => {
      handlers.onAlert?.(data)
    })

    socket.on('agent:wechat_sent', (data) => {
      handlers.onWechatSent?.(data)
    })

    socket.on('agent:clarification', (data) => {
      handlers.onClarification?.(data)
    })

    socket.on('agent:filter_result', (data) => {
      handlers.onFilterResult?.(data)
    })

    socket.on('agent:risk_scored', (data) => {
      handlers.onRiskScored?.(data)
    })

    socket.on('agent:final_stats', (data) => {
      handlers.onFinalStats?.(data)
    })

    socket.on('agent:reset', (data) => {
      handlers.onReset?.(data)
    })

    socket.on('agent:connected', (data) => {
      handlers.onAgentConnected?.(data)
    })

    socket.on('demo:patient_list', (data) => {
      handlers.onPatientList?.(data)
    })

    socket.on('doctor:callback_alert', (data) => {
      handlers.onCallbackAlert?.(data)
    })

    return socket
  }

  let cleanupSocket = null

  onMounted(() => {
    const socket = setupSocket()
    cleanupSocket = () => {
      socket?.disconnect()
      socketRef.value = null
      connected.value = false
    }
  })

  onUnmounted(() => {
    cleanupSocket?.()
  })

  return { send, connected, socket: socketRef }
}
