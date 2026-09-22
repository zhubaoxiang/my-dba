import { defineStore } from 'pinia'

// 应用全局状态：侧边栏折叠、全局 loading 等
export const useAppStore = defineStore('app', {
  state: () => ({
    sidebarCollapsed: false,
    loading: false
  }),
  actions: {
    toggleSidebar() {
      this.sidebarCollapsed = !this.sidebarCollapsed
    }
  }
})
