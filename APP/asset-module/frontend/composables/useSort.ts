import { computed, ref, unref } from 'vue'
import type { ComputedRef, Ref } from 'vue'

/**
 * 前端表格排序（天條：只要是表格，每一欄都要能排）。
 *
 * 用在「資料已經全部在前端」的表格。⚠️ 伺服器端分頁的表格**不可以**用這支——
 * 那只會排到目前這一頁，看起來像排好了其實是騙人的（比不能排更糟）。
 * 那種要把 sort_by/order 送後端（/api/assets 等已支援）。
 */

/** 空值一律排最後（不隨升冪/降冪翻面）——空白浮到最上面沒有任何資訊價值。 */
function isEmpty(v: any) {
  return v === null || v === undefined || v === ''
}

const IPV4 = /^\d{1,3}(\.\d{1,3}){3}$/

function ipToNum(ip: string) {
  return ip.split('.').reduce((n, p) => n * 256 + Number(p), 0)
}

function compare(a: any, b: any): number {
  if (isEmpty(a) && isEmpty(b)) return 0
  if (isEmpty(a)) return 1 // 空值永遠在後
  if (isEmpty(b)) return -1

  // IP 要按數值比，不然 192.168.1.9 會排在 10.99.0.110 後面
  if (typeof a === 'string' && typeof b === 'string' && IPV4.test(a) && IPV4.test(b)) {
    return ipToNum(a) - ipToNum(b)
  }
  const na = Number(a)
  const nb = Number(b)
  if (!Number.isNaN(na) && !Number.isNaN(nb) && a !== true && b !== true) return na - nb

  // 中文用 localeCompare，numeric:true 讓「第2項/第10項」這種也排得對
  return String(a).localeCompare(String(b), 'zh-Hant', { numeric: true })
}

export function useSort<T extends Record<string, any>>(
  rows: Ref<T[]> | ComputedRef<T[]>,
  initialKey = '',
) {
  const sortKey = ref(initialKey)
  const sortDir = ref<'asc' | 'desc'>('asc')

  function toggle(key: string) {
    if (sortKey.value === key) {
      sortDir.value = sortDir.value === 'asc' ? 'desc' : 'asc'
    } else {
      sortKey.value = key
      sortDir.value = 'asc'
    }
  }

  const sorted = computed(() => {
    const key = sortKey.value
    const src = unref(rows) ?? []
    if (!key) return src
    const dir = sortDir.value === 'asc' ? 1 : -1
    // 空值那條規則不能被 dir 翻面，所以在 compare 內判斷、這裡只乘非空的比較結果
    return [...src].sort((a, b) => {
      const ea = isEmpty(a[key])
      const eb = isEmpty(b[key])
      if (ea && eb) return 0
      if (ea) return 1
      if (eb) return -1
      return compare(a[key], b[key]) * dir
    })
  })

  return { sortKey, sortDir, toggle, sorted }
}
