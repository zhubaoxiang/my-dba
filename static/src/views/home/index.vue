<template>
  <div class="home-page">
    <el-row :gutter="16">
      <el-col :span="8" v-for="card in statCards" :key="card.title">
        <el-card shadow="hover">
          <div class="stat-card">
            <el-icon :size="40" :color="card.color"><component :is="card.icon" /></el-icon>
            <div class="stat-info">
              <div class="stat-value">{{ card.value }}</div>
              <div class="stat-title">{{ card.title }}</div>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-card class="welcome-card" shadow="never">
      <template #header>
        <span>欢迎使用</span>
      </template>
      <el-alert
        title="Vue3 + Element Plus 前端脚手架已就绪"
        type="success"
        :closable="false"
        show-icon
      />
      <p class="welcome-desc">
        本脚手架基于 Vue3 + Vite + Element Plus + Pinia + Vue Router 构建，已对接
        Django + DRF 后端统一响应格式（<code>{ code, message, data }</code>）。
      </p>
      <el-button type="primary" @click="handleTestApi">测试后端接口连通性</el-button>
      <el-button @click="router.push('/about')">查看关于</el-button>
    </el-card>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { User, Document, Bell } from '@element-plus/icons-vue'
import { testApi } from '@/api/test'

const router = useRouter()

// 顶部统计卡片数据
const statCards = ref([
  { title: '用户总数', value: '1,024', icon: User, color: '#409EFF' },
  { title: '文档数量', value: '256', icon: Document, color: '#67C23A' },
  { title: '待办通知', value: '8', icon: Bell, color: '#E6A23C' }
])

// 测试后端接口连通性
const handleTestApi = async () => {
  try {
    await testApi.getList({ page: 1, page_size: 10 })
    ElMessage.success('接口调用成功')
  } catch (err) {
    // 拦截器已统一提示，这里无需重复处理
  }
}
</script>

<style scoped>
.stat-card {
  display: flex;
  align-items: center;
}

.stat-info {
  margin-left: 16px;
}

.stat-value {
  font-size: 24px;
  font-weight: 600;
  color: #303133;
}

.stat-title {
  font-size: 13px;
  color: #909399;
  margin-top: 4px;
}

.welcome-card {
  margin-top: 16px;
}

.welcome-desc {
  margin: 16px 0;
  color: #606266;
  line-height: 1.6;
}

.welcome-desc code {
  background-color: #f5f7fa;
  padding: 2px 6px;
  border-radius: 3px;
  color: #e96900;
}
</style>
