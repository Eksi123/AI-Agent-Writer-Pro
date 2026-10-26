import { createRouter, createWebHashHistory } from 'vue-router'
import { HomeView } from './views/HomeView.js'
import { AboutView } from './views/AboutView.js'

// hash 历史：静态托管无需服务端 rewrite 规则
export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', name: 'home', component: HomeView },
    { path: '/about', name: 'about', component: AboutView },
  ],
})
