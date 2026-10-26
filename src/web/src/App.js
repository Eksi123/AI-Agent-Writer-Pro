import { defineComponent } from 'vue'
import { RouterLink, RouterView } from 'vue-router'

export const App = defineComponent({
  name: 'App',
  components: { RouterLink, RouterView },
  template: `
    <div class="layout">
      <header class="topbar">
        <h1>AI Writer Pro</h1>
        <nav>
          <RouterLink to="/">首页</RouterLink>
          <RouterLink to="/about">关于</RouterLink>
        </nav>
      </header>
      <main class="content">
        <RouterView />
      </main>
    </div>
  `,
})
