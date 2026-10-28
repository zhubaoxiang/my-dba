<template>
  <el-container class="layout-container">
    <!-- 侧边栏 -->
    <el-aside :width="isCollapse ? '64px' : '210px'" class="layout-aside">
      <div class="logo">
        <span v-if="!isCollapse">数据库管理专家</span>
        <span v-else>库</span>
      </div>
      <el-menu
        :default-active="activeMenu"
        :collapse="isCollapse"
        :collapse-transition="false"
        router
        background-color="#001529"
        text-color="#bfcbd9"
        active-text-color="#409EFF"
      >
        <template v-for="entry in menuEntries" :key="entry.key">
          <!-- 带分组的项收在 el-sub-menu 下；不带分组的（首页）直接平铺 -->
          <el-sub-menu v-if="entry.children" :index="entry.key">
            <template #title>
              <el-icon><component :is="entry.icon" /></el-icon>
              <span>{{ entry.title }}</span>
            </template>
            <el-menu-item v-for="child in entry.children" :key="child.path" :index="child.path">
              <el-icon><component :is="child.meta.icon" /></el-icon>
              <template #title>{{ child.meta.title }}</template>
            </el-menu-item>
          </el-sub-menu>
          <el-menu-item v-else :index="entry.path">
            <el-icon><component :is="entry.meta.icon" /></el-icon>
            <template #title>{{ entry.meta.title }}</template>
          </el-menu-item>
        </template>
      </el-menu>
    </el-aside>

    <el-container>
      <!-- 顶部导航 -->
      <el-header class="layout-header">
        <div class="header-left">
          <el-icon class="collapse-btn" @click="isCollapse = !isCollapse">
            <Fold v-if="!isCollapse" />
            <Expand v-else />
          </el-icon>
          <el-breadcrumb separator="/">
            <el-breadcrumb-item :to="{ path: '/' }">首页</el-breadcrumb-item>
            <el-breadcrumb-item v-if="currentTitle">{{ currentTitle }}</el-breadcrumb-item>
          </el-breadcrumb>
        </div>
        <div class="header-right">
          <el-dropdown>
            <span class="user-info">
              <el-avatar :size="30" icon="UserFilled" />
              <span class="username">{{ userStore.userInfo.username || '用户' }}</span>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item>个人中心</el-dropdown-item>
                <el-dropdown-item divided @click="handleLogout">退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>

      <!-- 主体内容区 -->
      <el-main class="layout-main">
        <router-view v-slot="{ Component }">
          <transition name="fade" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Fold, Expand, House, InfoFilled } from '@element-plus/icons-vue'
import { authApi } from '@/api/auth'
import { useUserStore } from '@/store/user'

// 侧边栏折叠状态
const isCollapse = ref(false)
const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

function handleLogout() {
  userStore.logout()
  router.push('/login')
}

onMounted(async () => {
  // 刷新后 token 还在但用户信息丢了：拉一次 me 恢复顶栏显示
  if (userStore.token && !userStore.userInfo.username) {
    try {
      userStore.setUserInfo(await authApi.me())
    } catch {
      // 拦截器已处理登录失效；这里不阻塞页面渲染
    }
  }
})

// 分组名 → 图标。集中在这里而不是散到各路由的 meta 里——那样「菜单长什么样」就没有一处能看全了
const MENU_GROUP_ICONS = {
  库表: 'Coin',
  SQL: 'MagicStick',
  知识: 'Collection',
  设置: 'Setting'
}

const isAdmin = computed(() => userStore.userInfo.role === 'admin')

// 从路由配置的 children 中筛选需要展示在菜单中的项（`meta.showInMenu !== false`），
// 拼接父路由与子路由得到完整路径，再按 `meta.group` 归到分组下。
//
// **顺序即声明顺序**：组按首次出现的先后排，组内同序。这样菜单顺序只有路由表一处定义。
const menuEntries = computed(() => {
  const entries = []
  const groups = new Map()
  router.options.routes.forEach((root) => {
    if (!root.children) return
    const parent = root.path.endsWith('/') ? root.path.slice(0, -1) : root.path
    root.children.forEach((child) => {
      if (!child.meta || child.meta.showInMenu === false) return
      if (child.meta.adminOnly && !isAdmin.value) return
      const item = { ...child, path: `${parent}/${child.path}` }
      const name = item.meta.group
      if (!name) {
        entries.push({ key: item.path, ...item })
        return
      }
      if (!groups.has(name)) {
        const entry = {
          key: `group:${name}`,
          title: name,
          icon: MENU_GROUP_ICONS[name] || 'Menu',
          children: []
        }
        groups.set(name, entry)
        entries.push(entry)
      }
      groups.get(name).children.push(item)
    })
  })
  return entries
})

// 当前激活的菜单项
const activeMenu = computed(() => route.path)
// 当前页面标题（用于面包屑）
const currentTitle = computed(() => route.meta.title || '')
</script>

<style scoped>
.layout-container {
  height: 100vh;
}

.layout-aside {
  background-color: #001529;
  transition: width 0.28s;
  overflow: hidden;
}

.logo {
  height: 60px;
  line-height: 60px;
  text-align: center;
  color: #fff;
  font-size: 18px;
  font-weight: 600;
  background-color: #002140;
  overflow: hidden;
  white-space: nowrap;
}

.el-menu {
  border-right: none;
}

.layout-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background-color: #fff;
  border-bottom: 1px solid #e6e6e6;
  box-shadow: 0 1px 4px rgba(0, 21, 41, 0.08);
}

.header-left {
  display: flex;
  align-items: center;
}

.collapse-btn {
  font-size: 20px;
  cursor: pointer;
  margin-right: 16px;
}

.header-right {
  display: flex;
  align-items: center;
}

.user-info {
  display: flex;
  align-items: center;
  cursor: pointer;
}

.username {
  margin-left: 8px;
  font-size: 14px;
}

.layout-main {
  background-color: #f0f2f5;
  padding: 16px;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
