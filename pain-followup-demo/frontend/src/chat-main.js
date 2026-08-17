// frontend/src/chat-main.js
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ChatPage from './pages/ChatPage.vue'
import { useDemoStore } from './stores/demoStore'
import './index.css'

const app = createApp(ChatPage)
const pinia = createPinia()
app.use(pinia)
app.mount('#chat-root')

// 与主页面共享同一事件契约：入口初始化一次，ChatPage 通过 store 收发消息
useDemoStore(pinia).initSocket()
