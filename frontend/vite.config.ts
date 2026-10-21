import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

// 构建产物输出到 backend/static，由 Flask 统一托管（生产模式单端口）
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    }
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:5000',
      '/events': 'http://127.0.0.1:5000',
      '/loadSettings': 'http://127.0.0.1:5000',
      '/changeValue': 'http://127.0.0.1:5000',
      '/setting': 'http://127.0.0.1:5000',
      '/connect_services': 'http://127.0.0.1:5000',
      '/disconnect_services': 'http://127.0.0.1:5000',
      '/connection_status': 'http://127.0.0.1:5000',
      '/get_queue': 'http://127.0.0.1:5000',
      '/open-queue': 'http://127.0.0.1:5000',
      '/close-queue': 'http://127.0.0.1:5000',
      '/clear-queue': 'http://127.0.0.1:5000',
      '/update_queue_list': 'http://127.0.0.1:5000',
      '/open-music': 'http://127.0.0.1:5000',
      '/close-music': 'http://127.0.0.1:5000',
      '/nextMusic': 'http://127.0.0.1:5000',
      '/catch_playing': 'http://127.0.0.1:5000',
      '/update_music_list': 'http://127.0.0.1:5000',
      '/findPath': 'http://127.0.0.1:5000',
      '/get_cookies': 'http://127.0.0.1:5000',
      '/savecookies': 'http://127.0.0.1:5000',
      '/start_sender': 'http://127.0.0.1:5000',
      '/stop_sender': 'http://127.0.0.1:5000',
      '/sender_status': 'http://127.0.0.1:5000',
      '/ai': 'http://127.0.0.1:5000'
    }
  },
  build: {
    outDir: '../backend/static',
    emptyOutDir: true,
    // 风险3：主包分包——按依赖库拆分 vendor chunk，减小首屏主包体积
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          if (id.includes('element-plus') || id.includes('@element-plus')) return 'element-plus'
          if (id.includes('vue') || id.includes('pinia') || id.includes('vue-router') || id.includes('axios')) return 'vue-vendor'
          return 'vendor'
        }
      }
    }
  }
})
