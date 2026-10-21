import { createRouter, createWebHashHistory, RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  { path: '/', name: 'home', component: () => import('@/views/HomeView.vue') },
  { path: '/queue', name: 'queue', component: () => import('@/views/QueueView.vue') },
  { path: '/music', name: 'music', component: () => import('@/views/MusicView.vue') },
  { path: '/lyrics', name: 'lyrics', component: () => import('@/views/LyricsView.vue') },
  { path: '/sender', name: 'sender', component: () => import('@/views/SenderView.vue') },
  { path: '/ai-chat', name: 'ai-chat', component: () => import('@/views/AiChatView.vue') },
  // OBS 展示页：独立全屏，无导航（供浏览器源使用）
  { path: '/display/queue', name: 'display-queue', component: () => import('@/views/display/QueueDisplay.vue') },
  { path: '/display/music', name: 'display-music', component: () => import('@/views/display/MusicDisplay.vue') },
  { path: '/display/lyrics', name: 'display-lyrics', component: () => import('@/views/display/LyricsDisplay.vue') },
  { path: '/display/danmu', name: 'display-danmu', component: () => import('@/views/display/DanmuDisplay.vue') },
]

const router = createRouter({
  // 使用 hash 模式：生产环境由 Flask 单端口托管静态资源，无需服务端路由回退配置
  history: createWebHashHistory(),
  routes,
})

export default router
