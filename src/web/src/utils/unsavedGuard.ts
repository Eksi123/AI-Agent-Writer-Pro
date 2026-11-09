/**
 * 未保存改动的「切换前」拦截。
 *
 * 为什么必须在切换**之前**拦，而不能像早先那样在 watcher 里事后回滚：
 *   章正文的草稿存在 `editorStore.draft`（store 级），切走再切回来内容还在，
 *   所以事后 `openChapter(previousId)` 回滚是可行的；但**大纲正文是
 *   `OutlineEditor.vue` 的组件内局部 `ref`**（`content` / `savedContent`），
 *   大纲也没有自动保存，只有 Ctrl+S 或工具栏才落盘。一旦 `activeOutline`
 *   变 false，组件立即卸载、局部状态随之销毁，此时再回滚只会重新挂载并从
 *   服务端重载——**用户的稿子已经没了**。故统一改为：先问，再改状态。
 *
 * 拦截点放在**唯一的导航入口**（`reader.openChapter` / `reader.openOutline` /
 * `project.selectNovel`），而不是散落在各个点击处：这样任何新增入口都自动受保护，
 * 不会漏。store 本身不 import element-plus，弹窗逻辑全部留在这里。
 */
import { ElMessageBox } from 'element-plus'

import { useEditorStore } from '@/stores/editor'

/** OutlineEditor 的落盘函数：成功返回 true，失败返回 false。 */
export type OutlineSaver = () => Promise<boolean | void>

let outlineSaver: OutlineSaver | null = null
/** 弹窗互斥：多个导航入口在同一 tick 内接连触发时，只允许弹一次。 */
let prompting = false

/** OutlineEditor 挂载时登记自己的 `save()`，卸载时注销（传 null）。 */
export function registerOutlineSaver(fn: OutlineSaver | null): void {
  outlineSaver = fn
}

/** 当前是否存在未保存改动（章正文 / 大纲分别看）。 */
export function unsavedTargets(): { chapter: boolean; outline: boolean } {
  const editorStore = useEditorStore()
  return { chapter: editorStore.dirty, outline: editorStore.dirtyOutline }
}

/**
 * 直接触发已登记的大纲落盘，供「页面隐藏 / 关闭」这类无法与用户交互的时机使用
 * （`WorkspaceView.saveOnHide`）。未登记或保存失败都返回 false，绝不抛错——
 * 卸载路径上抛异常只会把真正的保存动作也一起打断。
 */
export async function saveOutlineNow(): Promise<boolean> {
  if (!outlineSaver) return false
  try {
    return (await outlineSaver()) !== false
  } catch {
    return false
  }
}

/**
 * 切换前询问用户。返回 `true` 表示可以继续切换（已保存，或用户选择放弃修改），
 * `false` 表示应当**留在原地**（用户关闭弹窗，或保存失败）。
 *
 * 保存失败（例如章正文撞上 409 版本冲突）时返回 false：宁可拦住不让切，
 * 也不能把冲突横幅连同未保存的稿子一起丢掉。
 */
export async function confirmLeaveUnsaved(): Promise<boolean> {
  const { chapter, outline } = unsavedTargets()
  if (!chapter && !outline) return true
  // 已经在弹窗中 → 视为「留在原地」，避免叠出第二个对话框
  if (prompting) return false

  prompting = true
  try {
    const what = [chapter ? '章节正文' : '', outline ? '大纲' : ''].filter(Boolean).join('、')
    let action: 'confirm' | 'cancel' | 'close' = 'confirm'
    try {
      await ElMessageBox.confirm(`${what}还有未保存的修改，直接切换会丢失。要先保存吗？`, '未保存的修改', {
        confirmButtonText: '保存并切换',
        cancelButtonText: '放弃修改',
        // 'cancel' = 点了「放弃修改」，'close' = 右上角 X / ESC
        distinguishCancelAndClose: true,
        type: 'warning',
      })
    } catch (caught) {
      action = (caught as 'cancel' | 'close') || 'close'
    }

    if (action === 'cancel') {
      // 「放弃修改」：必须就地清掉脏标志。否则 `project.selectNovel` 内部接着调用的
      // `reader.openOutline()` 会再拦一次 → 同一个操作弹两个框；而大纲正文是组件内
      // 局部状态，切走后本就随卸载销毁，标志留着也没有意义。
      const store = useEditorStore()
      if (chapter) store.dirty = false
      if (outline) store.dirtyOutline = false
      return true
    }
    if (action === 'close') return false

    // 「保存并切换」：两类脏数据都要落盘，任一失败就留在原地
    const editorStore = useEditorStore()
    let ok = true
    if (chapter) {
      const saved = await editorStore.save()
      ok = ok && saved
      // save() 成功后 dirty 已被置 false，这里再显式清一次大纲/章标志无副作用，
      // 主要是为了不让 Vue 的 watcher 异步同步（dirtyOutline）赶不上下一次判定。
      if (saved) editorStore.dirty = false
    }
    if (outline) {
      if (!outlineSaver) {
        // 理论上不会发生（大纲脏 ⇒ OutlineEditor 已挂载并登记）。
        // 没有落盘手段时不得假装成功，否则等于静默丢稿。
        ok = false
      } else {
        const saved = await outlineSaver()
        const good = saved !== false
        ok = ok && good
        // 同理：`dirtyOutline` 由 OutlineEditor 的 watch(dirty) 异步回写，
        // 显式清一次，保证紧随其后的导航判定看到的是最新状态。
        if (good) editorStore.dirtyOutline = false
      }
    }
    return ok
  } finally {
    prompting = false
  }
}
