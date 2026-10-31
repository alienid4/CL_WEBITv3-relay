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
    // 同一個失敗不要連跳兩則：apiFetch 遇到「權限不足」會先跳一則（白話原因），呼叫端的 catch 又跳「XX失敗：同一句原因」。
    // 目前畫面上已有內含關係的提示（一句包著另一句）→ 只留比較完整的那一則。
    const nested = (a: string, b: string) => Math.min(a.length, b.length) >= 8 && (a.includes(b) || b.includes(a))
    const dup = state.items.find((t) => t.message === message || nested(t.message, message))
    if (dup) {
      if (dup.message.includes(message)) return          // 已有更完整（或一樣）的，新的不用再跳
      state.items.splice(state.items.indexOf(dup), 1)    // 新的比較完整：換掉舊的
    }
    const id = ++seq
    state.items.push({ id, message, type })
    setTimeout(() => {
      const i = state.items.findIndex((t) => t.id === id)
      if (i >= 0) state.items.splice(i, 1)
    }, ms)
  }
  return { toasts: state.items, showToast }
}
