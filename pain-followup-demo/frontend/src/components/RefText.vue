<template>
  <span class="ref-text">
    <template v-for="(tok, i) in tokens" :key="i">
      <span v-if="tok.type === 'text'">{{ tok.value }}</span>
      <span
        v-else
        class="ref-wrap"
        @mouseenter="onEnter(tok.num)"
        @mouseleave="onLeave"
      >
        <sup class="ref-chip">[{{ tok.num }}]</sup>
        <div
          v-if="hovered === tok.num"
          class="ref-popover"
          @mouseenter="cancel"
          @mouseleave="onLeave"
        >
          <template v-if="c">
            <div class="ref-pop-title">
              {{ c.guide }}
              <span v-if="c.year" class="ref-pop-year">{{ c.year }}</span>
            </div>
            <div class="ref-pop-meta">
              <span v-if="c.page">页码：第 {{ c.page }} 页</span>
              <span v-if="c.clause"> · {{ c.clause }}</span>
            </div>
            <div class="ref-pop-excerpt">{{ c.excerpt || '（无摘要）' }}</div>
            <button class="ref-pop-btn" @click.stop="openPdf(c)"><LanIcon name="filePaste" />查看原文 PDF</button>
          </template>
          <div v-else class="ref-pop-excerpt text-gray-400">未找到对应溯源信息</div>
        </div>
      </span>
    </template>
  </span>
</template>

<script setup>
import { ref, computed } from 'vue'
import LanIcon from './LanIcon.vue'

const props = defineProps({
  text: { type: [String, Number], default: '' },
  citations: { type: Array, default: () => [] },
  apiBase: { type: String, default: 'http://localhost:5000' },
})

const hovered = ref(null)
let timer = null

const tokens = computed(() => {
  const s = props.text
  if (s == null) return []
  return String(s)
    .split(/(\[\d+\])/g)
    .map((p) => {
      const m = p.match(/^\[(\d+)\]$/)
      if (m) return { type: 'ref', num: parseInt(m[1], 10) }
      return { type: 'text', value: p }
    })
    .filter((t) => !(t.type === 'text' && !t.value))
})

function citationByNum(n) {
  return (props.citations || []).find((c) => {
    const m = String(c.ref || '').match(/\d+/)
    return m && parseInt(m[0], 10) === n
  })
}

const c = computed(() => (hovered.value != null ? citationByNum(hovered.value) : null))

function onEnter(n) {
  if (timer) { clearTimeout(timer); timer = null }
  hovered.value = n
}
function onLeave() {
  timer = setTimeout(() => { hovered.value = null }, 150)
}
function cancel() {
  if (timer) { clearTimeout(timer); timer = null }
}
function openPdf(c) {
  if (!c || !c.guide) return
  const page = c.page ? `&page=${c.page}#page=${c.page}` : ''
  const url = `${props.apiBase}/api/guideline-file?guide=${encodeURIComponent(c.guide)}${page}`
  window.open(url, '_blank')
}
</script>

<style scoped>
.ref-text {
  line-height: 1.7;
}
.ref-wrap {
  position: relative;
  display: inline-block;
}
.ref-chip {
  cursor: pointer;
  color: #079284;
  background: #f4fefe;
  border: 1px solid #43d5c7;
  border-radius: 4px;
  padding: 0 3px;
  font-size: 10px;
  font-weight: 600;
  margin: 0 1px;
  line-height: 1.4;
}
.ref-chip:hover {
  background: #d7fffe;
}
.ref-popover {
  position: absolute;
  z-index: 50;
  top: 100%;
  left: 0;
  margin-top: 4px;
  width: 280px;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
  padding: 10px 12px;
  text-align: left;
}
.ref-pop-title {
  font-size: 13px;
  font-weight: 600;
  color: #111827;
}
.ref-pop-year {
  margin-left: 4px;
  font-size: 11px;
  font-weight: 400;
  color: #9ca3af;
}
.ref-pop-meta {
  font-size: 11px;
  color: #6b7280;
  margin-top: 2px;
}
.ref-pop-excerpt {
  font-size: 12px;
  color: #374151;
  margin-top: 6px;
  line-height: 1.5;
  max-height: 120px;
  overflow: auto;
}
.ref-pop-btn {
  margin-top: 8px;
  font-size: 12px;
  color: #2563eb;
  background: #eff6ff;
  border: 1px solid #bfdbfe;
  border-radius: 6px;
  padding: 3px 8px;
  cursor: pointer;
}
.ref-pop-btn:hover {
  background: #dbeafe;
}
</style>
