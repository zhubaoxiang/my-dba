<template>
  <el-card shadow="never">
    <template #header>
      <span>关于本脚手架</span>
    </template>
    <el-descriptions :column="1" border>
      <el-descriptions-item label="技术栈">Vue 3 + Vite + Element Plus + Pinia + Vue Router</el-descriptions-item>
      <el-descriptions-item label="构建工具">Vite 5</el-descriptions-item>
      <el-descriptions-item label="UI 组件库">Element Plus 2</el-descriptions-item>
      <el-descriptions-item label="状态管理">Pinia</el-descriptions-item>
      <el-descriptions-item label="HTTP 客户端">Axios</el-descriptions-item>
      <el-descriptions-item label="后端对接">Django + DRF（响应格式 code/message/data）</el-descriptions-item>
    </el-descriptions>
  </el-card>
</template>

<script setup>
// 关于页面：展示脚手架基本信息
</script>
