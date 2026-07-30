<template>
  <button
    @click="handleClick"
    class="lan-chat-fab fixed bottom-6 left-6 z-50 w-12 h-12 rounded-full
      flex items-center justify-center text-white text-xl transition-all hover:scale-105 active:scale-95"
    title="患者微信对话"
  >
    <LanIcon name="chat" :size="20" />
  </button>
</template>

<script setup>
import LanIcon from './LanIcon.vue'

let chatWindow = null

function handleClick() {
  // 复用已打开的窗口，避免每次新开
  if (chatWindow && !chatWindow.closed) {
    chatWindow.focus()
    return
  }
  const chatUrl = `${window.location.origin}/chat.html`
  chatWindow = window.open(chatUrl, 'pain-chat', 'width=1200,height=800,scrollbars=yes')
}

defineProps({
  onClick: { type: Function, default: () => {} },
  hasUnread: { type: Boolean, default: false },
  isOpen: { type: Boolean, default: false },
})

defineEmits(['click'])
</script>
