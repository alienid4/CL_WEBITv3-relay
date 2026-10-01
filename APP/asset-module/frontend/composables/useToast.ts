// 輕量 toast：async-feedback 規範要求成功/失敗都要有可見回饋。M1 前端原本沒有 toast，
// 這裡補一個最小可用版本（模組級 reactive 單例 + layouts/default.vue 的容器渲染）。
// 只在 client 端由使用者動作觸發，不涉及 SSR 狀態。
import { reactive } from 'vue'

export type ToastType = 'success' | 'error' | 'warn' | 'info'
interface ToastItem {
  id: number
  message: string
  type: ToastType
}

const state = reactive<{ items: ToastItem[] }>({ items: [] })
let seq = 0

export function useToast() {
  function showToast(message: string, type: ToastType = 'info', ms = 3400) {
    const id = ++seq
    state.items.push({ id, message, type })
    setTimeout(() => {
      const i = state.items.findIndex((t) => t.id === id)
      if (i >= 0) state.items.splice(i, 1)
    }, ms)
  }
  return { toasts: state.items, showToast }
}
