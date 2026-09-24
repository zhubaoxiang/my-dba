<template>
  <div class="qa-page">
    <el-row :gutter="16">
      <el-col :span="6">
        <el-card shadow="never" class="session-card">
          <template #header>
            <div class="card-header">
              <span>会话</span>
              <el-button type="primary" size="small" @click="newSession">新会话</el-button>
            </div>
          </template>
          <el-empty v-if="!sessions.length" description="还没有会话" :image-size="60" />
          <div
            v-for="item in sessions"
            :key="item.id"
            class="session-item"
            :class="{ active: item.id === currentSessionId }"
            @click="openSession(item)"
          >
            <div class="session-title">{{ item.title || '未命名会话' }}</div>
            <div class="session-meta">{{ item.message_count }} 条 · {{ item.create_time }}</div>
            <el-button link type="danger" size="small" @click.stop="removeSession(item)">删除</el-button>
          </div>
        </el-card>
      </el-col>

      <el-col :span="18">
        <el-card shadow="never" class="chat-card">
          <template #header>
            <div class="card-header">
              <span>知识问答</span>
              <div class="controls">
                <el-select v-model="knowledgeBaseId" placeholder="不使用知识库" clearable style="width: 180px">
                  <el-option v-for="b in bases" :key="b.id" :value="b.id" :label="b.name" />
                </el-select>
                <el-select v-model="mode" style="width: 170px">
                  <el-option :value="1" label="自动" />
                  <el-option :value="2" label="仅知识库" />
                  <el-option :value="3" label="仅通用模型" />
                </el-select>
              </div>
            </div>
          </template>

          <div ref="messagesEl" class="messages">
            <el-empty v-if="!messages.length" description="提个问题试试，例如「PostgreSQL 默认隔离级别是什么」" />
            <div v-for="(item, index) in messages" :key="index" class="message" :class="item.role">
              <div class="bubble">
                <div v-if="item.status" class="status">{{ item.status }}</div>
                <div v-if="item.content" class="content">{{ item.content }}</div>

                <el-alert
                  v-if="item.is_complete === false"
                  class="interrupted"
                  type="info"
                  :closable="false"
                  show-icon
                  title="本次回答已中断，内容不完整"
                />

                <el-alert
                  v-if="item.is_fallback && item.note"
                  class="fallback"
                  type="warning"
                  :closable="false"
                  show-icon
                  :title="item.note"
                />

                <div v-if="item.tools_used && item.tools_used.length" class="tools">
                  调用工具：{{ item.tools_used.join('、') }}
                </div>

                <div v-if="item.sources && item.sources.length" class="sources">
                  <div class="sources-title">引用来源</div>
                  <div v-for="(s, i) in item.sources" :key="i" class="source">
                    <span class="source-index">[{{ i + 1 }}]</span>
                    《{{ s.document_title }}》
                    <span v-if="s.heading_path" class="source-path">· {{ s.heading_path }}</span>
                    <span class="source-score">相似度 {{ s.score }}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div class="ask-box">
            <el-input
              v-model="question"
              type="textarea"
              :autosize="{ minRows: 2, maxRows: 5 }"
              placeholder="输入问题，Ctrl+Enter 发送"
              @keydown.ctrl.enter="ask"
            />
            <el-button v-if="asking" type="danger" @click="stop">停止生成</el-button>
            <el-button v-else type="primary" :disabled="!question.trim()" @click="ask">发送</el-button>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { nextTick, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { askStream, knowledgeApi, qaApi } from '@/api/knowledge'

const sessions = ref([])
const currentSessionId = ref(null)
const messages = ref([])
const bases = ref([])
const knowledgeBaseId = ref(null)
const mode = ref(1)
const question = ref('')
const asking = ref(false)
const messagesEl = ref(null)
// 生成中的请求，供「停止生成」中断
let controller = null

async function loadSessions() {
  const data = await qaApi.sessions({ page: 1, page_size: 100 })
  sessions.value = data.results || []
}

async function loadBases() {
  const data = await knowledgeApi.list({ page: 1, page_size: 100 })
  bases.value = data.results || []
  if (bases.value.length && !knowledgeBaseId.value) knowledgeBaseId.value = bases.value[0].id
}

async function openSession(item) {
  currentSessionId.value = item.id
  const detail = await qaApi.detail(item.id)
  messages.value = (detail.messages || []).map((m) => ({ ...m }))
  if (detail.knowledge_base_id) knowledgeBaseId.value = detail.knowledge_base_id
  mode.value = detail.mode
}

function newSession() {
  currentSessionId.value = null
  messages.value = []
  question.value = ''
}

async function scrollToBottom() {
  await nextTick()
  if (messagesEl.value) messagesEl.value.scrollTop = messagesEl.value.scrollHeight
}

async function ask() {
  const text = question.value.trim()
  if (!text) return
  asking.value = true
  messages.value.push({ role: 'user', content: text })
  question.value = ''

  // 先占位再由流式事件填充：工具阶段（实测数秒到十几秒）因此有进度可看，而不是一片空白
  const reply = reactive({
    role: 'assistant',
    content: '',
    status: '',
    sources: [],
    tools_used: [],
    is_fallback: false,
    note: '',
    is_complete: true
  })
  messages.value.push(reply)
  controller = new AbortController()
  await scrollToBottom()

  try {
    const finished = await askStream(
      {
        question: text,
        session_id: currentSessionId.value || undefined,
        knowledge_base_id: knowledgeBaseId.value || undefined,
        mode: mode.value
      },
      {
        onStatus: (message) => {
          reply.status = message
          scrollToBottom()
        },
        onDelta: (chunk) => {
          reply.status = ''
          reply.content += chunk
          scrollToBottom()
        },
        onDone: (data) => {
          currentSessionId.value = data.session_id
          reply.status = ''
          reply.sources = data.sources || []
          reply.tools_used = data.tools_used || []
          reply.is_fallback = data.is_fallback
          reply.note = data.note
          reply.is_complete = true
          loadSessions()
        },
        onError: (message) => {
          reply.status = ''
          reply.is_complete = false
          ElMessage.error(message)
        }
      },
      controller.signal
    )
    // 没收到 done 就说明这一轮没生成完（点了停止，或连接断了）——已生成的内容保留
    if (!finished) {
      reply.status = ''
      reply.is_complete = false
    }
  } catch (err) {
    reply.status = ''
    reply.is_complete = false
    // 主动停止不算失败，不弹提示
    if (err.name !== 'AbortError') ElMessage.error(err.message || '请求失败')
  } finally {
    controller = null
    asking.value = false
    scrollToBottom()
  }
}

function stop() {
  if (controller) controller.abort()
}

async function removeSession(item) {
  await ElMessageBox.confirm('确认删除该会话？', '提示', { type: 'warning' })
  await qaApi.remove(item.id)
  if (currentSessionId.value === item.id) newSession()
  ElMessage.success('已删除')
  loadSessions()
}

onMounted(async () => {
  await loadBases()
  await loadSessions()
})
</script>

<style scoped>
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.controls {
  display: flex;
  gap: 8px;
}

.session-card,
.chat-card {
  height: calc(100vh - 140px);
  display: flex;
  flex-direction: column;
}

.session-item {
  padding: 8px 10px;
  border-radius: 4px;
  cursor: pointer;
  border: 1px solid transparent;
  margin-bottom: 4px;
}

.session-item:hover {
  background-color: #f5f7fa;
}

.session-item.active {
  background-color: #ecf5ff;
  border-color: #b3d8ff;
}

.session-title {
  font-size: 13px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.session-meta {
  font-size: 12px;
  color: #909399;
}

.messages {
  flex: 1;
  overflow-y: auto;
  padding-right: 4px;
}

.message {
  display: flex;
  margin-bottom: 12px;
}

.message.user {
  justify-content: flex-end;
}

.message .bubble {
  max-width: 78%;
  padding: 10px 12px;
  border-radius: 6px;
  background-color: #f5f7fa;
  white-space: pre-wrap;
  line-height: 1.6;
}

.message.user .bubble {
  background-color: #ecf5ff;
}

.status {
  font-size: 12px;
  color: #909399;
}

.fallback,
.interrupted {
  margin-top: 8px;
}

.tools {
  margin-top: 6px;
  font-size: 12px;
  color: #909399;
}

.sources {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed #dcdfe6;
}

.sources-title {
  font-size: 12px;
  color: #909399;
  margin-bottom: 4px;
}

.source {
  font-size: 12px;
  color: #606266;
  margin-bottom: 2px;
}

.source-index {
  color: #409eff;
  margin-right: 2px;
}

.source-path {
  color: #909399;
}

.source-score {
  color: #c0c4cc;
  margin-left: 6px;
}

.ask-box {
  display: flex;
  gap: 8px;
  align-items: flex-end;
  margin-top: 12px;
}
</style>
