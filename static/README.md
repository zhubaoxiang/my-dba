# 我的数据库管理专家前端

基于 **Vue 3 + Vite + Element Plus + Pinia + Vue Router** 的前端脚手架，对接 Django + DRF 后端服务。

## 目录结构

```
static/
├── index.html                 # HTML 入口
├── package.json               # 依赖清单
├── vite.config.js             # Vite 配置（含开发代理）
├── .gitignore
├── public/
│   └── favicon.svg
└── src/
    ├── main.js                # 应用入口，注册 Element Plus / Pinia / Router
    ├── App.vue                # 根组件
    ├── assets/
    │   └── styles/
    │       └── index.css      # 全局样式
    ├── api/
    │   ├── request.js          # Axios 实例（请求/响应拦截器，对接后端统一响应）
    │   └── test.js             # 示例 API 模块
    ├── router/
    │   └── index.js           # 路由配置
    ├── store/
    │   ├── user.js             # 用户状态（Token、用户信息）
    │   └── app.js              # 应用状态（侧边栏、loading）
    ├── layout/
    │   └── index.vue          # 后台管理布局（侧边栏 + 顶栏 + 内容区）
    └── views/
        ├── home/index.vue     # 首页示例
        ├── about/index.vue    # 关于页
        └── error/404.vue      # 404 页面
```

## 快速开始

### 1. 安装依赖

```bash
cd static
npm install
```

### 2. 启动开发服务器

```bash
npm run dev
```

默认监听 `http://localhost:5173`，开发环境下 `/my-dba` 前缀的请求会代理到 `http://127.0.0.1:8000`（Django 后端）。

### 3. 构建生产包

```bash
npm run build
```

产物输出到 `static/dist/`，可由 Django 静态文件服务或 Nginx 托管。

### 4. 预览构建产物

```bash
npm run preview
```

## 后端对接说明

- **API 基础路径**：`/my-dba`（与后端 `src/config/urls.py` 的 `SYS_NAME` 保持一致）
- **认证方式**：JWT Token 通过请求头 `Token` 字段传递（与后端 `utils/authentication.py` 约定一致）
- **响应格式**：后端统一返回 `{ code, message, data }`，`code === 2000` 视为成功
  - `2000` 成功、`4000` 参数错误、`4003` 权限不足、`4004` 资源不存在、`4017` 业务条件未满足、`5000` 服务器错误

## 新增业务模块

1. 在 `src/views/` 下新建页面组件
2. 在 `src/router/index.js` 注册路由（配置 `meta.title`、`meta.icon`）
3. 在 `src/api/` 下新建对应 API 模块，复用 `request.js` 实例

## 环境变量

可通过 `.env.development` / `.env.production` 配置 `VITE_API_BASE_URL` 切换后端地址，默认 `/my-dba`。
