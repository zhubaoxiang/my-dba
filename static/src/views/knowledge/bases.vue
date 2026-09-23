<template>
  <div class="bases-page">
    <el-row :gutter="16">
      <el-col :span="7">
        <el-card shadow="never">
          <template #header>
            <div class="card-header">
              <span>知识库</span>
              <el-button type="primary" size="small" @click="openCreate">新建</el-button>
            </div>
          </template>

          <el-empty v-if="!bases.length" description="还没有知识库" :image-size="70" />
          <div
            v-for="item in bases"
            :key="item.id"
            class="base-item"
            :class="{ active: item.id === currentId }"
            @click="selectBase(item)"
          >
            <div class="base-name">
              {{ item.name }}
              <el-tag v-if="!item.is_enabled" type="info" size="small">停用</el-tag>
            </div>
            <div class="base-desc">{{ item.description || '无描述' }}</div>
            <div class="base-meta">{{ item.document_count }} 份文档</div>
            <div class="base-actions">
              <el-button link type="primary" size="small" @click.stop="openEdit(item)">编辑</el-button>
              <el-button link type="danger" size="small" @click.stop="removeBase(item)">删除</el-button>
            </div>
          </div>
        </el-card>
      </el-col>

      <el-col :span="17">
        <el-card shadow="never">
          <template #header>
            <div class="card-header">
              <span>文档{{ current ? ` · ${current.name}` : '' }}</span>
              <div>
                <el-button type="primary" :disabled="!current" @click="uploadVisible = true">上传文件</el-button>
                <el-button :disabled="!current" @click="urlVisible = true">导入链接</el-button>
                <el-button :disabled="!current" @click="loadDocuments">刷新</el-button>
              </div>
            </div>
          </template>

          <el-empty v-if="!current" description="请先在左侧选择或新建知识库" />
          <template v-else>
            <el-alert
              class="tip"
              type="info"
              :closable="false"
              show-icon
              title="支持 Markdown / 文本 / PDF 文件与网页链接。摄入在后台进行，可稍后刷新查看状态；扫描件类 PDF 抽不出文本会明确报错。"
            />
            <el-table :data="documents" v-loading="loading" border>
              <el-table-column prop="title" label="标题" min-width="180" show-overflow-tooltip />
              <el-table-column prop="source_type_label" label="来源" width="100" />
              <el-table-column prop="source" label="来源标识" min-width="200" show-overflow-tooltip />
              <el-table-column label="状态" width="100">
                <template #default="{ row }">
                  <el-tag :type="statusType(row.status)" size="small">{{ row.status_label }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="chunk_count" label="块数" width="80" />
              <el-table-column prop="fail_reason" label="失败原因" min-width="180" show-overflow-tooltip />
              <el-table-column label="操作" width="180" fixed="right">
                <template #default="{ row }">
                  <el-button link type="primary" :disabled="row.source_type_label !== '网页链接'" @click="reingest(row)">
                    重新摄入
                  </el-button>
                  <el-button link type="danger" @click="removeDocument(row)">删除</el-button>
                </template>
              </el-table-column>
            </el-table>

            <el-pagination
              class="pager"
              background
              layout="total, prev, pager, next"
              :total="total"
              :current-page="query.page"
              :page-size="query.page_size"
              @current-change="onPageChange"
            />
          </template>
        </el-card>
      </el-col>
    </el-row>

    <el-dialog v-model="dialogVisible" :title="editing ? '编辑知识库' : '新建知识库'" width="480px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="90px">
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" maxlength="64" placeholder="如：PostgreSQL 手册" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" maxlength="255" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.is_enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitBase">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="uploadVisible" title="上传文件" width="480px">
      <el-upload drag :auto-upload="false" :limit="1" :on-change="onFileChange" :file-list="fileList">
        <el-icon class="el-icon--upload"><upload-filled /></el-icon>
        <div class="el-upload__text">拖拽文件到此处，或<em>点击选择</em></div>
        <template #tip>
          <div class="hint">支持 .md / .markdown / .txt / .pdf，单个不超过 20MB</div>
        </template>
      </el-upload>
      <template #footer>
        <el-button @click="uploadVisible = false">取消</el-button>
        <el-button type="primary" :loading="uploading" :disabled="!pendingFile" @click="submitUpload">开始摄入</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="urlVisible" title="导入网页链接" width="520px">
      <el-form ref="urlFormRef" :model="urlForm" :rules="urlRules" label-width="90px">
        <el-form-item label="链接" prop="url">
          <el-input v-model="urlForm.url" placeholder="https://..." />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="urlVisible = false">取消</el-button>
        <el-button type="primary" :loading="importing" @click="submitUrl">开始摄入</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { UploadFilled } from '@element-plus/icons-vue'
import { documentApi, knowledgeApi } from '@/api/knowledge'

const bases = ref([])
const currentId = ref(null)
const current = computed(() => bases.value.find((item) => item.id === currentId.value) || null)

const documents = ref([])
const total = ref(0)
const loading = ref(false)
const query = reactive({ page: 1, page_size: 10 })

const dialogVisible = ref(false)
const editing = ref(null)
const saving = ref(false)
const formRef = ref()
const form = reactive({ name: '', description: '', is_enabled: true })
const rules = { name: [{ required: true, message: '请输入名称', trigger: 'blur' }] }

const uploadVisible = ref(false)
const uploading = ref(false)
const pendingFile = ref(null)
const fileList = ref([])

const urlVisible = ref(false)
const importing = ref(false)
const urlFormRef = ref()
const urlForm = reactive({ url: '' })
const urlRules = { url: [{ required: true, message: '请输入链接', trigger: 'blur' }] }

function statusType(status) {
  if (status === 3) return 'success'
  if (status === 4) return 'danger'
  if (status === 2) return 'warning'
  return 'info'
}

async function loadBases() {
  const data = await knowledgeApi.list({ page: 1, page_size: 100 })
  bases.value = data.results || []
  if (!currentId.value && bases.value.length) currentId.value = bases.value[0].id
}

async function selectBase(item) {
  currentId.value = item.id
  query.page = 1
  await loadDocuments()
}

async function loadDocuments() {
  if (!currentId.value) return
  loading.value = true
  try {
    const data = await documentApi.list({
      knowledge_base_id: currentId.value,
      page: query.page,
      page_size: query.page_size
    })
    documents.value = data.results || []
    total.value = data.total || 0
  } finally {
    loading.value = false
  }
}

function onPageChange(page) {
  query.page = page
  loadDocuments()
}

function openCreate() {
  editing.value = null
  Object.assign(form, { name: '', description: '', is_enabled: true })
  dialogVisible.value = true
}

function openEdit(item) {
  editing.value = item
  Object.assign(form, { name: item.name, description: item.description, is_enabled: item.is_enabled })
  dialogVisible.value = true
}

async function submitBase() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  saving.value = true
  try {
    if (editing.value) {
      await knowledgeApi.update(editing.value.id, { ...form })
      ElMessage.success('已保存')
    } else {
      const created = await knowledgeApi.create({ ...form })
      currentId.value = created.id
      ElMessage.success('已创建')
    }
    dialogVisible.value = false
    await loadBases()
    await loadDocuments()
  } finally {
    saving.value = false
  }
}

async function removeBase(item) {
  await ElMessageBox.confirm(`确认删除知识库「${item.name}」？其文档与向量索引会一并清理。`, '提示', { type: 'warning' })
  await knowledgeApi.remove(item.id)
  ElMessage.success('已删除')
  currentId.value = null
  documents.value = []
  await loadBases()
  if (currentId.value) await loadDocuments()
}

function onFileChange(file) {
  pendingFile.value = file.raw
  fileList.value = [file]
}

async function submitUpload() {
  if (!pendingFile.value) return
  uploading.value = true
  try {
    const formData = new FormData()
    formData.append('knowledge_base_id', currentId.value)
    formData.append('file', pendingFile.value)
    await documentApi.upload(formData)
    ElMessage.success('已提交摄入，稍后刷新查看状态')
    uploadVisible.value = false
    pendingFile.value = null
    fileList.value = []
    loadDocuments()
  } finally {
    uploading.value = false
  }
}

async function submitUrl() {
  const valid = await urlFormRef.value.validate().catch(() => false)
  if (!valid) return
  importing.value = true
  try {
    await documentApi.importUrl({ knowledge_base_id: currentId.value, url: urlForm.url })
    ElMessage.success('已提交摄入，稍后刷新查看状态')
    urlVisible.value = false
    urlForm.url = ''
    loadDocuments()
  } finally {
    importing.value = false
  }
}

async function reingest(row) {
  await documentApi.reingest(row.id)
  ElMessage.success('已重新提交摄入')
  loadDocuments()
}

async function removeDocument(row) {
  await ElMessageBox.confirm(`确认删除文档「${row.title}」？`, '提示', { type: 'warning' })
  await documentApi.remove(row.id)
  ElMessage.success('已删除')
  loadDocuments()
}

onMounted(async () => {
  await loadBases()
  if (currentId.value) await loadDocuments()
})
</script>

<style scoped>
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.base-item {
  padding: 10px 12px;
  border-radius: 4px;
  cursor: pointer;
  border: 1px solid transparent;
}

.base-item:hover {
  background-color: #f5f7fa;
}

.base-item.active {
  background-color: #ecf5ff;
  border-color: #b3d8ff;
}

.base-name {
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 6px;
}

.base-desc,
.base-meta {
  font-size: 12px;
  color: #909399;
  margin-top: 2px;
}

.base-actions {
  margin-top: 4px;
}

.tip {
  margin-bottom: 12px;
}

.pager {
  margin-top: 16px;
  justify-content: flex-end;
}

.hint {
  font-size: 12px;
  color: #909399;
}
</style>
