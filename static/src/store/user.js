import { defineStore } from 'pinia'

function readUserInfo() {
  try {
    return JSON.parse(localStorage.getItem('userInfo') || '{}') || {}
  } catch {
    return {}
  }
}

// 用户状态管理：JWT token 与用户信息
//
// 这里不 import API 模块，避免 store -> api -> request -> store 的循环依赖；
// 登录与 me 的请求由页面/布局发起，再把结果写回 store。
export const useUserStore = defineStore('user', {
  state: () => ({
    token: localStorage.getItem('token') || '',
    userInfo: readUserInfo()
  }),
  getters: {
    isLoggedIn: (state) => !!state.token
  },
  actions: {
    setLogin(token, userInfo) {
      this.token = token || ''
      this.userInfo = userInfo || {}
      localStorage.setItem('token', this.token)
      localStorage.setItem('userInfo', JSON.stringify(this.userInfo))
    },
    setUserInfo(userInfo) {
      this.userInfo = userInfo || {}
      localStorage.setItem('userInfo', JSON.stringify(this.userInfo))
    },
    logout() {
      this.token = ''
      this.userInfo = {}
      localStorage.removeItem('token')
      localStorage.removeItem('userInfo')
    }
  }
})
