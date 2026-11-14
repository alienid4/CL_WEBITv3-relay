// 2.056.2（V56 第 3 批，⚠D4）：⚙ 備份與還原實掛——還原區只有功能開關 restore 開著才出現；
// 沒選檔或沒輸入「覆蓋還原」按鈕就是灰的、按了也不送；畫面不出現私鑰。InfoNote 為替身，全假資料。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Comp from '../components/v2/Backup.vue'

enableAutoUnmount(afterEach)
let restoreOn = false
const fetchCalls: string[] = []
const STATUS = { status: 'green', reasons: [], checked_at: 'x', db: { path: '/d', exists: true, integrity_ok: true, integrity_detail: '', journal_mode: 'wal', size_bytes: 1 },
  last_backup: null, local: { dir: '/b', count: 0, free_mb: 1, retention_days: 7 }, offsite: { configured: false, dir: null, count: 0 } }
beforeEach(() => {
  restoreOn = false; fetchCalls.length = 0
  for (const [k, v] of Object.entries({ ref, reactive, computed, watch, onMounted })) vi.stubGlobal(k, v)
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useRuntimeConfig', () => ({ public: { apiBase: '' } }))
  vi.stubGlobal('useFeatureFlags', () => ({ isEnabled: (k: string) => (k === 'restore' ? restoreOn : true), ensureLoaded: () => {} }))
  vi.stubGlobal('httpReason', async () => 'x')
  vi.stubGlobal('navigateTo', () => {})
  vi.stubGlobal('fetch', async (url: string) => { fetchCalls.push(url); return { ok: true, json: async () => ({}) } })
  vi.stubGlobal('useApi', () => ({ apiFetch: async (url: string) => (url === '/api/backup/status' ? STATUS
    : url === '/api/export-key' ? { is_recipient: true, private_key_path: '/k', my_public_key: 'PUB', my_fingerprint: 'fp', recipient_fingerprint: null, encryption_enabled: true, can_export: true } : {}) }))
})
afterEach(() => vi.unstubAllGlobals())
const mnt = async () => { const w = mount(Comp, { global: { stubs: { InfoNote: { template: '<i />' } } } }); await flushPromises(); return w }

describe('備份與還原', () => {
  it('功能開關 restore 關著：沒有還原區', async () => {
    const w = await mnt()
    expect(w.find('[data-test="restore"]').exists()).toBe(false)
    expect(w.text()).toContain('立即備份')
    expect(w.html()).not.toContain('-----BEGIN')
  })
  it('開著：沒選檔、沒打確認字，按鈕灰、不送', async () => {
    restoreOn = true
    const w = await mnt()
    const sec = w.find('[data-test="restore"]')
    expect(sec.exists()).toBe(true)
    const btn = sec.findAll('button').at(-1)!
    expect((btn.element as HTMLButtonElement).disabled).toBe(true)
    await sec.find('input[aria-label="確認文字"]').setValue('覆蓋還原')
    expect((btn.element as HTMLButtonElement).disabled).toBe(true)          // 還沒選檔
    await btn.trigger('click'); await flushPromises()
    expect(fetchCalls.some(u => u.includes('/api/backup/restore'))).toBe(false)
  })
  // 2.056.5：確認字在畫面上繞不過——就算有人把按鈕的 disabled 拿掉硬按，函式本身也先檢查確認字才送；
  // 確認字對才送，而且表單裡帶 confirm（伺服器也會再檢查一次）。
  it('選了檔、確認字不對：把 disabled 拿掉硬按也不送；確認字對才送且帶 confirm', async () => {
    restoreOn = true
    const bodies: any[] = []
    vi.stubGlobal('fetch', async (url: string, opts: any = {}) => { fetchCalls.push(url); bodies.push(opts.body); return { ok: true, json: async () => ({ ok: true }) } })
    const w = await mnt()
    const sec = w.find('[data-test="restore"]')
    const file = sec.find('input[type="file"]')
    Object.defineProperty(file.element, 'files', { value: [new File(['x'], 'up.db')] })
    await file.trigger('change')
    const btn = sec.findAll('button').at(-1)!
    expect((btn.element as HTMLButtonElement).disabled).toBe(true)              // 有檔、沒確認字：灰
    for (const word of ['', '覆蓋', '覆蓋還原 ', 'REPLACE']) {
      await sec.find('input[aria-label="確認文字"]').setValue(word)
      ;(btn.element as HTMLButtonElement).disabled = false                    // 模擬有人在開發者工具把 disabled 拿掉
      btn.element.dispatchEvent(new Event('click')); await flushPromises()
      expect(fetchCalls.some(u => u.includes('/api/backup/restore')), word).toBe(false)
    }
    await sec.find('input[aria-label="確認文字"]').setValue('覆蓋還原')
    expect((btn.element as HTMLButtonElement).disabled).toBe(false)
    await btn.trigger('click'); await flushPromises()
    expect(fetchCalls.filter(u => u.includes('/api/backup/restore')).length).toBe(1)
    const fd = bodies[fetchCalls.findIndex(u => u.includes('/api/backup/restore'))] as FormData
    expect(fd.get('confirm')).toBe('覆蓋還原')
    expect(fd.get('file')).toBeTruthy()
  })
})
