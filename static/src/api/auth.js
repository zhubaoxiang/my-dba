import request from './request'

// 登录与当前用户
export const authApi = {
  login(data) {
    return request.post('/v1/auth/login', data)
  },
  me() {
    return request.get('/v1/auth/me')
  },
  logout() {
    return request.post('/v1/auth/logout')
  }
}
