import { defineStore } from 'pinia'

// 用户状态管理：Token、用户信息
export const useUserStore = defineStore('user', {
  state: () => ({
    token: localStorage.getItem('token') || '',
    userInfo: {
      username: '管理员',
      role: 'admin'
    }
  }),
  getters: {
    isLoggedIn: (state) => !!state.token
  },
  actions: {
    // 设置 Token 并持久化到 localStorage
    setToken(token) {
      this.token = token
      localStorage.setItem('token', token)
    },
    // 清除登录态
    logout() {
      this.token = ''
      this.userInfo = {}
      localStorage.removeItem('token')
    }
  }
})
