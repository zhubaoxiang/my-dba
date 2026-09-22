<template>
  <el-card shadow="never" class="dev-page">
    <el-result icon="info" :title="title" sub-title="功能开发中，敬请期待" />
  </el-card>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'

// 占位页面：用于尚未实现的菜单项（用户管理、系统管理），展示当前菜单标题
const route = useRoute()
const title = computed(() => route.meta.title || '功能开发中')
</script>

<style scoped>
.dev-page {
  min-height: 400px;
}
</style>
