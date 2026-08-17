// frontend/src/main.js
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import { useDemoStore } from './stores/demoStore'
import './index.css'

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)
app.mount('#root')

// 页面级唯一 WebSocket 连接（说明书 15.3）：入口初始化一次，store 接线全部事件
useDemoStore(pinia).initSocket()
