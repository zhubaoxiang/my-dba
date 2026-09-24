import request from './request'
import { useUserStore } from '@/store/user'

const baseURL = import.meta.env.VITE_API_BASE_URL || '/my-dba'

// 模型接入配置 API
export const providerApi = {
  // 列表（分页）
  list(params) {
    return request.get('/v1/llm-provider', { params })
  },
  // 详情
  detail(id) {
    return request.get(`/v1/llm-provider/${id}`)
  },
  // 新建
  create(data) {
    return request.post('/v1/llm-provider', data)
  },
  // 修改（api_key 留空表示不更换）
  update(id, data) {
    return request.put(`/v1/llm-provider/${id}`, data)
  },
  // 软删除
  remove(id) {
    return request.delete(`/v1/llm-provider/${id}`)
  },
  // 设为当前生效配置
  activate(id) {
    return request.post(`/v1/llm-provider/${id}/activate`)
  },
  // 连通性测试
  test(id) {
    return request.post(`/v1/llm-provider/${id}/test`)
  }
}

// 知识库 API
export const knowledgeApi = {
  list(params) {
    return request.get('/v1/knowledge-base', { params })
  },
  create(data) {
    return request.post('/v1/knowledge-base', data)
  },
  update(id, data) {
    return request.put(`/v1/knowledge-base/${id}`, data)
  },
  remove(id) {
    return request.delete(`/v1/knowledge-base/${id}`)
  }
}

// 知识库文档 API
export const documentApi = {
  list(params) {
    return request.get('/v1/kb-document', { params })
  },
  upload(formData) {
    return request.post('/v1/kb-document', formData, {
      headers: { 'Content-Type': 'multipart/form-data' }
    })
  },
  importUrl(data) {
    return request.post('/v1/kb-document/import-url', data)
  },
  reingest(id) {
    return request.post(`/v1/kb-document/${id}/reingest`)
  },
  remove(id) {
    return request.delete(`/v1/kb-document/${id}`)
  }
}

// 问答会话 API
export const qaApi = {
  sessions(params) {
    return request.get('/v1/qa-session', { params })
  },
  detail(id) {
    return request.get(`/v1/qa-session/${id}`)
  },
  remove(id) {
    return request.delete(`/v1/qa-session/${id}`)
  },
  ask(data) {
    return request.post('/v1/qa-session/ask', data)
  }
}

/**
 * 解析一帧 SSE 文本，返回 { event, data } 或 null
 */
function parseFrame(frame) {
  let event = 'message'
  const dataLines = []
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
  }
  return dataLines.length ? { event, data: dataLines.join('\n') } : null
}

/**
 * 流式提问，返回是否正常收到结束事件（false 表示中途被中止或断连）
 *
 * 不能用 EventSource：它只支持 GET、且不能带自定义请求头，而提问要走 POST 请求体、
 * 认证走 Token 头。因此用 fetch 读 ReadableStream 自行解析 SSE 帧。
 *
 * 两类失败的处理位置不同：**开流之前**的失败仍是统一响应格式的 JSON（带 HTTP 状态码），
 * 这里直接抛出交给调用方提示；**开流之后**的失败走 `error` 事件，由 onError 接收。
 */
export async function askStream(payload, handlers = {}, signal) {
  const { onStatus, onDelta, onDone, onError } = handlers
  const userStore = useUserStore()
  const response = await fetch(`${baseURL}/v1/qa-session/ask-stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(userStore.token ? { Token: userStore.token } : {})
    },
    body: JSON.stringify(payload),
    signal
  })

  const contentType = response.headers.get('content-type') || ''
  if (!contentType.includes('text/event-stream')) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.message || `请求失败（${response.status}）`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let finished = false

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let cut
    while ((cut = buffer.indexOf('\n\n')) >= 0) {
      const parsed = parseFrame(buffer.slice(0, cut))
      buffer = buffer.slice(cut + 2)
      if (!parsed) continue
      const data = JSON.parse(parsed.data)
      if (parsed.event === 'status') onStatus && onStatus(data.text)
      else if (parsed.event === 'delta') onDelta && onDelta(data.text)
      else if (parsed.event === 'done') {
        finished = true
        onDone && onDone(data)
      } else if (parsed.event === 'error') onError && onError(data.message)
    }
  }
  return finished
}
