import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'path'

// Vite 构建配置：开发端口 5173，构建产物输出到 dist/
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src')
    }
  },
  server: {
    port: 5173,
    // 不自动打开浏览器：部分受限环境（Windows 沙箱/无权限）下 spawn 打开器会抛 EPERM 导致启动中断
    open: false,
    // 开发环境代理：将 /my-dba 开头的请求转发到 Django 后端
    proxy: {
      '/my-dba': {
        target: 'http://127.0.0.1:8899',
        changeOrigin: true
      }
    }
  },
  build: {
    outDir: 'dist',
    assetsDir: 'static',
    sourcemap: false
  }
})
