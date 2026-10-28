import request from './request'

// 本地用户管理（仅超级管理员）
export const userApi = {
  list(params) {
    return request.get('/v1/user', { params })
  },
  create(data) {
    return request.post('/v1/user', data)
  },
  update(id, data) {
    return request.put(`/v1/user/${id}`, data)
  },
  remove(id) {
    return request.delete(`/v1/user/${id}`)
  }
}
