<!-- frontend/src/App.vue -->
<template>
  <DemoPage />
  <Toaster position="top-right" rich-colors />
</template>

<script setup>
import DemoPage from './pages/DemoPage.vue'
import { Toaster } from 'vue-sonner'
</script>
