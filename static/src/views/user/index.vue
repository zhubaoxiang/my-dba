<template>
  <div class="user-page">
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>用户管理</span>
          <div class="header-actions">
            <el-input
              v-model="keyword"
              placeholder="搜索用户名或邮箱"
              clearable
              style="width: 240px"
              @keyup.enter="search"
              @clear="search"
            />
            <el-button @click="search">搜索</el-button>
            <el-button type="primary" @click="openCreate">新增用户</el-button>
          </div>
        </div>
      </template>

      <el-table :data="rows" v-loading="loading" border>
        <el-table-column prop="username" label="用户名" min-width="140" />
        <el-table-column prop="email" label="邮箱" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">{{ row.email || '—' }}</template>
        </el-table-column>
        <el-table-column label="角色" width="120">
          <template #default="{ row }">
            <el-tag :type="row.role === 'admin' ? 'danger' : 'info'" size="small">
              {{ roleLabel(row.role) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'info'" size="small">
              {{ row.is_active ? '启用' : '禁用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="date_joined" label="创建时间" width="170" />
        <el-table-column label="最后登录" width="170">
          <template #default="{ row }">{{ row.last_login || '—' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button
              link
              :type="row.is_active ? 'warning' : 'success'"
              :disabled="isSelf(row)"
              @click="toggleActive(row)"
            >
              {{ row.is_active ? '停用' : '启用' }}
            </el-button>
            <el-button link type="danger" :disabled="isSelf(row)" @click="removeUser(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        class="pager"
        background
        layout="total, sizes, prev, pager, next"
        :total="total"
        :current-page="query.page"
        :page-size="query.page_size"
        :page-sizes="[10, 20, 50, 100]"
        @current-change="onPageChange"
        @size-change="onSizeChange"
      />
    </el-card>

    <el-dialog v-model="dialogVisible" :title="editing ? '编辑用户' : '新增用户'" width="480px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="90px">
        <el-form-item label="用户名" prop="username">
          <el-input v-model="form.username" :disabled="!!editing" maxlength="150" />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            show-password
            :placeholder="editing ? '留空表示不修改' : '至少 8 位，不能是纯数字'"
          />
        </el-form-item>
        <el-form-item label="邮箱" prop="email">
          <el-input v-model="form.email" maxlength="254" />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="form.role" :disabled="isSelf(editing)">
            <el-option value="admin" label="超级管理员" />
            <el-option value="user" label="普通用户" />
          </el-select>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.is_active" :disabled="isSelf(editing)" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { userApi } from '@/api/user'
import { useUserStore } from '@/store/user'

const userStore = useUserStore()
const rows = ref([])
const total = ref(0)
const loading = ref(false)
const keyword = ref('')
const query = reactive({ page: 1, page_size: 20 })

const dialogVisible = ref(false)
const editing = ref(null)
const saving = ref(false)
const formRef = ref()
const form = reactive({ username: '', password: '', email: '', role: 'user', is_active: true })

const rules = computed(() => ({
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: editing.value ? [] : [{ required: true, message: '请输入密码', trigger: 'blur' }],
  email: [{ type: 'email', message: '邮箱格式不正确', trigger: 'blur' }]
}))

function roleLabel(role) {
  return role === 'admin' ? '超级管理员' : '普通用户'
}

function isSelf(row) {
  return !!row && row.username === userStore.userInfo.username
}

async function loadUsers() {
  loading.value = true
  try {
    const data = await userApi.list({
      page: query.page,
      page_size: query.page_size,
      keyword: keyword.value || undefined
    })
    rows.value = data.results || []
    total.value = data.total || 0
  } finally {
    loading.value = false
  }
}

function search() {
  query.page = 1
  loadUsers()
}

function onPageChange(page) {
  query.page = page
  loadUsers()
}

function onSizeChange(size) {
  query.page_size = size
  query.page = 1
  loadUsers()
}

function openCreate() {
  editing.value = null
  Object.assign(form, { username: '', password: '', email: '', role: 'user', is_active: true })
  dialogVisible.value = true
  nextTick(() => formRef.value?.clearValidate())
}

function openEdit(row) {
  editing.value = row
  Object.assign(form, {
    username: row.username,
    password: '',
    email: row.email || '',
    role: row.role,
    is_active: row.is_active
  })
  dialogVisible.value = true
  nextTick(() => formRef.value?.clearValidate())
}

async function submit() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  saving.value = true
  try {
    if (editing.value) {
      const payload = {
        email: form.email,
        role: form.role,
        is_active: form.is_active
      }
      if (form.password) payload.password = form.password
      await userApi.update(editing.value.id, payload)
      ElMessage.success('已保存')
    } else {
      await userApi.create({
        username: form.username,
        password: form.password,
        email: form.email,
        role: form.role,
        is_active: form.is_active
      })
      ElMessage.success('已创建')
    }
    dialogVisible.value = false
    await loadUsers()
  } finally {
    saving.value = false
  }
}

async function toggleActive(row) {
  const action = row.is_active ? '停用' : '启用'
  if (row.is_active) {
    await ElMessageBox.confirm(`确认停用用户「${row.username}」？停用后该用户将无法登录。`, '提示', {
      type: 'warning'
    })
  }
  await userApi.update(row.id, { is_active: !row.is_active })
  ElMessage.success(`已${action}`)
  await loadUsers()
}

async function removeUser(row) {
  await ElMessageBox.confirm(`确认删除用户「${row.username}」？删除后无法登录，且不可恢复。`, '提示', {
    type: 'warning'
  })
  await userApi.remove(row.id)
  ElMessage.success('已删除')
  await loadUsers()
}

onMounted(loadUsers)
</script>

<style scoped>
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.header-actions {
  display: flex;
  gap: 8px;
}

.pager {
  margin-top: 16px;
  justify-content: flex-end;
}
</style>
