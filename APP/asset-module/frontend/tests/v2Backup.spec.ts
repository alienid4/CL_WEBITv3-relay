// 2.056.2（V56 第 3 批，⚠D4）：⚙ 備份與還原實掛——還原區只有功能開關 restore 開著才出現；
// 沒選檔或沒輸入「覆蓋還原」按鈕就是灰的、按了也不送；畫面不出現私鑰。InfoNote 為替身，全假資料。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Comp from '../components/v2/Backup.vue'

enableAutoUnmount(afterEach)
let restoreOn = false
const puts: any[] = []
const LIM = (min: number, max: number, d: number) => ({ min, max, default: d })
const RET = { patch_backup_keep: 3, db_backup_keep: 3, relay_in_days: 7, disk_alert_pct: 85, backup_timer_enabled: true, backup_timer_decided: false,
  relay_in_last_purge: '', limits: { patch_backup_keep: LIM(1, 30, 3), db_backup_keep: LIM(1, 30, 3), relay_in_days: LIM(1, 90, 7), disk_alert_pct: LIM(50, 99, 85) } }
const fetchCalls: string[] = []
const STATUS = { status: 'green', reasons: [], checked_at: 'x', db: { path: '/d', exists: true, integrity_ok: true, integrity_detail: '', journal_mode: 'wal', size_bytes: 1 },
  last_backup: null, local: { dir: '/b', count: 0, free_mb: 1, retention_days: 7 }, offsite: { configured: false, dir: null, count: 0 } }
beforeEach(() => {
  restoreOn = false; fetchCalls.length = 0; puts.length = 0
  for (const [k, v] of Object.entries({ ref, reactive, computed, watch, onMounted })) vi.stubGlobal(k, v)
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
  vi.stubGlobal('useRuntimeConfig', () => ({ public: { apiBase: '' } }))
  vi.stubGlobal('useFeatureFlags', () => ({ isEnabled: (k: string) => (k === 'restore' ? restoreOn : true), ensureLoaded: () => {} }))
  vi.stubGlobal('httpReason', async () => 'x')
  vi.stubGlobal('navigateTo', () => {})
  vi.stubGlobal('fetch', async (url: string) => { fetchCalls.push(url); return { ok: true, json: async () => ({}) } })
  vi.stubGlobal('useApi', () => ({ apiFetch: async (url: string, opts?: any) => (url === '/api/backup/status' ? STATUS
    : url === '/api/backup/retention' ? (opts?.method === 'PUT' ? (puts.push(opts.body), { ...RET, ...opts.body }) : RET)
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
  // 2.07x：保留與自動清理（份數 1～30、天數 1～90、門檻 50～99；超出範圍按鈕灰、不送）
  it('保留設定：預設值顯示、超出範圍不送、合法才 PUT', async () => {
    const w = await mnt()
    const sec = w.find('[data-test="retention-fold"]')
    expect(sec.exists()).toBe(true)
    expect((sec.find('[data-test="ret-db_backup_keep"]').element as HTMLInputElement).value).toBe('3')
    expect((sec.find('[data-test="ret-relay_in_days"]').element as HTMLInputElement).value).toBe('7')
    const save = sec.find('[data-test="ret-save"]')
    await sec.find('[data-test="ret-db_backup_keep"]').setValue('31')
    expect((save.element as HTMLButtonElement).disabled).toBe(true)
    expect(sec.text()).toContain('1～30')
    await sec.find('[data-test="ret-db_backup_keep"]').setValue('5')
    await sec.find('[data-test="ret-timer"]').setValue(false)
    expect((save.element as HTMLButtonElement).disabled).toBe(false)
    await save.trigger('click'); await flushPromises()
    expect(puts).toHaveLength(1)
    expect(puts[0]).toMatchObject({ db_backup_keep: 5, patch_backup_keep: 3, relay_in_days: 7, disk_alert_pct: 85, backup_timer_enabled: false })
  })
})
