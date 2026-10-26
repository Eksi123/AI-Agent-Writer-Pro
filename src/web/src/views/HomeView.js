import { defineComponent } from 'vue'
import { useAppStore } from '../stores/app.js'

export const HomeView = defineComponent({
  name: 'HomeView',
  setup() {
    const store = useAppStore()
    return { store }
  },
  template: `
    <section>
      <h2>首页</h2>
      <p>Vue 框架已就绪（无构建运行，vendor 目录提供官方发行版）。</p>
      <p>
        访问计数：{{ store.visitCount }}（双倍：{{ store.doubled }}）
        <button @click="store.visit()">+1</button>
      </p>
    </section>
  `,
})
