import request from './request'

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
