import { defineComponent, version } from 'vue'

export const AboutView = defineComponent({
  name: 'AboutView',
  setup() {
    return { vueVersion: version }
  },
  template: `
    <section>
      <h2>关于</h2>
      <ul>
        <li>vue：{{ vueVersion }}</li>
        <li>vue-router：5.4.0</li>
        <li>pinia：4.1.0</li>
      </ul>
    </section>
  `,
})
