// 開發協作 D1c（WhatsUp REST 探測腳本）原頁實掛：醒目的「請在非開盤時間執行」、可下載（含 BOM 的 .ps1）、複製、D1d 備用標示、腳本內容守門。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DEVKIT_TASKS } from '../content/devkit'
import Page from '../pages/v2/devkit.vue'

const route = reactive<{ query: Record<string, string> }>({ query: {} })
const V2Page = defineComponent({ props: ['modelValue', 'tabs', 'width'], setup(_p, { slots }) { return () => h('div', [slots.title?.(), slots.conclusion?.(), h('div', { class: 'tabbody' }, slots.tab?.())]) } })
const stubs = { V2Page, InfoNote: { template: '<i class="note" />' } }
const blobs: Blob[] = []
let writeText: ReturnType<typeof vi.fn>

beforeEach(() => {
  route.query = {}
  localStorage.clear()
  blobs.length = 0
  writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
  ;(URL as any).createObjectURL = (b: Blob) => { blobs.push(b); return 'blob:x' }
  ;(URL as any).revokeObjectURL = () => {}
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  for (const [k, v] of Object.entries({ ref, computed, watch, onMounted })) vi.stubGlobal(k, v)
  vi.stubGlobal('definePageMeta', () => {})
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useRouter', () => ({ replace: (o: { query: Record<string, string> }) => { route.query = { ...o.query } } }))
  vi.stubGlobal('useToast', () => ({ showToast: () => {} }))
})
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })
enableAutoUnmount(afterEach)

async function mountPage() {
  const w = mount(Page, { global: { stubs } })
  await flushPromises()
  return w
}
const d1c = DEVKIT_TASKS.find(t => t.id === 'D1c')!

describe('D1c WhatsUp REST 探測腳本', () => {
  it('醒目寫「請在非開盤時間執行」；有下載腳本鈕；腳本整段顯示、可複製', async () => {
    route.query = { t: 'D1c' }
    const w = await mountPage()
    expect(w.find('[data-warn]').text()).toContain('非開盤時間')
    expect(w.find('[data-optional]').exists()).toBe(false)
    expect(w.find('[data-dl-script]').text()).toContain('wug_probe.ps1')
    expect(w.find('pre[data-cmd]').element.textContent).toBe(d1c.commands[0].text)
    await w.find('button.copy:not(.dl)').trigger('click')
    expect(writeText).toHaveBeenCalledWith(d1c.commands[0].text)
  })

  it('下載的是 .ps1 純文字，開頭有 BOM（Windows PowerShell 5.1 才不會把中文讀成亂碼），內容就是畫面上那支腳本', async () => {
    route.query = { t: 'D1c' }
    const w = await mountPage()
    await w.find('[data-dl-script]').trigger('click')
    expect(blobs).toHaveLength(1)
    const text = await blobs[0].text()
    expect(text.charCodeAt(0)).toBe(0xFEFF)
    expect(text.slice(1)).toBe(d1c.commands[0].text)
  })

  it('D1d 不需要（使用者 11-10）：標「不需要」，沒有醒目警告也沒有下載鈕', async () => {
    route.query = { t: 'D1d' }
    const w = await mountPage()
    expect(w.find('[data-optional]').text()).toContain('不需要')
    expect(w.find('[data-warn]').exists()).toBe(false)
    expect(w.find('[data-dl-script]').exists()).toBe(false)
  })
})

describe('D1c 腳本內容守門（前端這份就是會被下載的那份）', () => {
  const t = d1c.commands[0].text
  it('只有登入一次 POST、其餘 GET；沒有危險寫法；帳密只用 Read-Host', () => {
    expect((t.match(/HttpMethod\]::Post/g) ?? []).length).toBe(2)
    expect(t).toMatch(/'\/api\/v1\/token' -Form \$form/)
    for (const bad of ['Invoke-Expression', 'Bypass', '-EncodedCommand', 'FromBase64String', 'Set-ItemProperty', 'Remove-Item', 'HKLM:', 'HKCU:', 'Out-File', 'Set-Content', 'Invoke-WebRequest', 'ConvertTo-SecureString', '-AsPlainText']) {
      expect(t).not.toContain(bad)
    }
    expect(t).toContain("Read-Host 'WhatsUp Gold password' -AsSecureString")
  })
  it('開盤時間要輸入 YES、呼叫上限 40、每次至少 1 秒、不重導向', () => {
    expect(t).toContain("-cne 'YES'")
    expect(t).toMatch(/\$script:MaxCalls = 40\n/)
    expect(t).toContain('Start-Sleep -Seconds 1')
    expect(t).toContain('AllowAutoRedirect = $false')
  })
  it('沒有 IP 字面值、反引號、${、反斜線（放進 TS 樣板字串不會被改寫）', () => {
    expect(t).not.toMatch(/\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b/)
    expect(t).not.toContain('`')
    expect(t).not.toContain('${')
    expect(t).not.toContain('\\')
  })
})
