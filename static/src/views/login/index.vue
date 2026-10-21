<template>
  <div class="login-page">
    <el-card class="login-card" shadow="always">
      <div class="login-title">数据库管理专家</div>
      <div class="login-hint">请使用域账号或本地账号登录</div>

      <el-form :model="form" @submit.prevent="submit">
        <el-form-item>
          <el-input
            v-model="form.username"
            size="large"
            placeholder="用户名"
            autocomplete="username"
            @keyup.enter="submit"
          />
        </el-form-item>
        <el-form-item>
          <el-input
            v-model="form.password"
            size="large"
            type="password"
            show-password
            placeholder="密码"
            autocomplete="current-password"
            @keyup.enter="submit"
          />
        </el-form-item>
        <el-button class="login-button" type="primary" size="large" :loading="loading" @click="submit">
          登录
        </el-button>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { authApi } from '@/api/auth'
import { useUserStore } from '@/store/user'

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()

const form = reactive({ username: '', password: '' })
const loading = ref(false)

async function submit() {
  if (loading.value) return
  if (!form.username.trim() || !form.password) {
    ElMessage.warning('请输入用户名和密码')
    return
  }
  loading.value = true
  try {
    const data = await authApi.login({ username: form.username.trim(), password: form.password })
    userStore.setLogin(data.token, data.user)
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : ''
    // 只接受站内绝对路径，避免 //evil.com 这类协议相对跳转
    router.replace(redirect.startsWith('/') && !redirect.startsWith('//') ? redirect : '/home')
  } catch {
    // 拦截器已经提示具体失败原因；这里只保留登录页，不清空已输入的用户名
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #001529 0%, #123a63 100%);
}

.login-card {
  width: 380px;
  border-radius: 8px;
}

.login-title {
  text-align: center;
  font-size: 22px;
  font-weight: 600;
  color: #303133;
}

.login-hint {
  margin: 4px 0 22px;
  text-align: center;
  font-size: 12px;
  color: #909399;
}

.login-button {
  width: 100%;
}
</style>
