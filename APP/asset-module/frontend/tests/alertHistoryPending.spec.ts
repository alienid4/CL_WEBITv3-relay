// 1.2「告警歷史」：請求還沒回來時不可顯示「還沒有告警」（GPT 審核 2026-11-18：載入中被當成空資料）。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h, onMounted, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Comp from '../components/v2/AlertHistory.vue'

enableAutoUnmount(afterEach)
let release: () => void = () => {}
const V2Table = defineComponent({ props: ['rows', 'empty'], setup(p) { return () => h('div', { class: 'tbl' }, (p.rows as any[]).length ? 'rows' : String(p.empty)) } })
beforeEach(() => {
  vi.stubGlobal('ref', ref); vi.stubGlobal('onMounted', onMounted)
  const gate = new Promise<void>(res => { release = res })
  vi.stubGlobal('useApi', () => ({ apiFetch: async (u: string) => { await gate; return u.endsWith('disk-alert') ? { history: [] } : { history: [] } } }))
})

describe('告警歷史載入中', () => {
  it('回來之前只寫載入中，不寫「還沒有磁碟告警」；回來之後才顯示空狀態', async () => {
    const w = mount(Comp, { global: { stubs: { InfoNote: true, NuxtLink: true, V2Table } } })
    await flushPromises()
    expect(w.find('[data-testid=ah-loading]').exists()).toBe(true)
    expect(w.text()).not.toContain('還沒有磁碟告警')
    release(); await flushPromises()
    expect(w.find('[data-testid=ah-loading]').exists()).toBe(false)
    expect(w.text()).toContain('還沒有磁碟告警')
  })
})
