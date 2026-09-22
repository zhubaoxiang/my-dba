import { createRouter, createWebHistory } from 'vue-router'
import Layout from '@/layout/index.vue'

// 路由表：所有页面通过 Layout 包裹，菜单项由子路由 meta.showInMenu 控制
const routes = [
  {
    path: '/',
    component: Layout,
    redirect: '/home',
    children: [
      {
        path: 'home',
        name: 'Home',
        component: () => import('@/views/home/index.vue'),
        meta: { title: '首页', icon: 'House', showInMenu: true }
      },
      {
        path: 'datasource',
        name: 'DatasourceManagement',
        component: () => import('@/views/datasource/index.vue'),
        meta: { title: '数据源管理', icon: 'Coin', showInMenu: true }
      },
      {
        path: 'datasource/tables',
        name: 'DatasourceTables',
        component: () => import('@/views/datasource/tables.vue'),
        meta: { title: '库表分析', icon: 'Grid', showInMenu: true }
      },
      {
        path: 'datasource/issues',
        name: 'DatasourceIssues',
        component: () => import('@/views/datasource/issues.vue'),
        meta: { title: '问题清单', icon: 'Warning', showInMenu: true }
      },
      {
        path: 'user',
        name: 'UserManagement',
        component: () => import('@/views/development/index.vue'),
        meta: { title: '用户管理', icon: 'User', showInMenu: true }
      },
      {
        path: 'system',
        name: 'SystemManagement',
        component: () => import('@/views/development/index.vue'),
        meta: { title: '系统管理', icon: 'Setting', showInMenu: true }
      },
      {
        path: 'about',
        name: 'About',
        component: () => import('@/views/about/index.vue'),
        meta: { title: '关于', icon: 'InfoFilled', showInMenu: false }
      }
    ]
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'NotFound',
    component: () => import('@/views/error/404.vue'),
    meta: { title: '页面不存在', showInMenu: false }
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router
