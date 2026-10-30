import request from './request'

// 模型调用用量（仅超级管理员）
export const llmUsageApi = {
  summary(params) {
    return request.get('/v1/llm-usage', { params })
  },
  calls(params) {
    return request.get('/v1/llm-usage/calls', { params })
  }
}
