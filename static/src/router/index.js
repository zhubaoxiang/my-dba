import { createRouter, createWebHistory } from 'vue-router'
import Layout from '@/layout/index.vue'

// 路由表：所有页面通过 Layout 包裹，菜单项由子路由 meta.showInMenu 控制
//
// **菜单按能力线分组**：`meta.group` 相同的项在侧边栏里归到同一个可展开的分组下
// （分组名与图标见 layout/index.vue 的 MENU_GROUP_ICONS）。没有 `group` 的项直接平铺在顶层。
//
// **这里的声明顺序就是菜单顺序**——组内也是。两条排序原则：
//
// 1. **跨族的不收进组里**，直接一级。被多条线依赖的东西挂在其中一条下面，等于把公共的
//    东西收进了某一家的抽屉：
//      - 「数据源管理」被库表与 SQL 两条线依赖（SQL 生成的入参里它是必填）
//      - 「分析规则」同时管库表规则与 SQL 规则（两个 Tab）
//      - 「知识问答」是日常用的入口，不该收进组里
// 2. 组内按**概念/工作顺序**排，不按点击频率。库表那组只剩「看」的动作——接入与规则
//    都已经在一级了。
// 3. 一级项里，「分析规则」放在**紧挨系统组的上方**——它是设置类的，与系统那组连成一片；
//    「数据源管理」则靠上，它是资源入口。
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
        path: 'knowledge/qa',
        name: 'KnowledgeQa',
        component: () => import('@/views/knowledge/qa.vue'),
        meta: { title: '知识问答', icon: 'ChatDotRound', showInMenu: true }
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
        meta: { title: '库表分析', icon: 'Grid', showInMenu: true, group: '库表' }
      },
      {
        path: 'datasource/issues',
        name: 'DatasourceIssues',
        component: () => import('@/views/datasource/issues.vue'),
        meta: { title: '问题清单', icon: 'Warning', showInMenu: true, group: '库表' }
      },
      {
        path: 'sqlanalysis',
        name: 'SqlAnalysis',
        component: () => import('@/views/sqlanalysis/index.vue'),
        meta: { title: 'SQL 分析', icon: 'MagicStick', showInMenu: true, group: 'SQL' }
      },
      {
        path: 'nl2sql',
        name: 'Nl2Sql',
        component: () => import('@/views/nl2sql/index.vue'),
        meta: { title: 'SQL 生成', icon: 'EditPen', showInMenu: true, group: 'SQL' }
      },
      {
        path: 'datasource/rules',
        name: 'AnalysisRules',
        component: () => import('@/views/datasource/rules.vue'),
        meta: { title: '分析规则', icon: 'SetUp', showInMenu: true }
      },
      {
        path: 'knowledge/bases',
        name: 'KnowledgeBase',
        component: () => import('@/views/knowledge/bases.vue'),
        meta: { title: '知识库', icon: 'Collection', showInMenu: true, group: '系统' }
      },
      {
        path: 'knowledge/provider',
        name: 'ModelProvider',
        component: () => import('@/views/knowledge/provider.vue'),
        meta: { title: '模型配置', icon: 'Cpu', showInMenu: true, group: '系统' }
      },
      {
        path: 'user',
        name: 'UserManagement',
        component: () => import('@/views/development/index.vue'),
        meta: { title: '用户管理', icon: 'User', showInMenu: true, group: '系统' }
      },
      {
        path: 'system',
        name: 'SystemManagement',
        component: () => import('@/views/development/index.vue'),
        meta: { title: '系统管理', icon: 'Setting', showInMenu: true, group: '系统' }
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
