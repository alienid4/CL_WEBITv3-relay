// 開發協作原頁實掛：複製鈕、逐指令進度、整理結果的確認關卡、對照表隔離、進度匯出入、本機儲存例外。
// Nuxt 自動匯入以 stubGlobal 提供；V2Page／InfoNote 為替身；剪貼簿用 mock。無網路、無後端。
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DEVKIT_TASKS } from '../content/devkit'
import Page from '../pages/v2/devkit.vue'

const route = reactive<{ query: Record<string, string> }>({ query: {} })
const toasts: { m: string; t?: string }[] = []
let writeText: ReturnType<typeof vi.fn>
const KEY = 'webit.devkit.progress.v2'

const V2Page = defineComponent({
  props: ['modelValue', 'tabs', 'width'],
  setup(_p, { slots }) {
    return () => h('div', { class: 'v2page' }, [slots.title?.(), slots.conclusion?.(), h('div', { class: 'tabbody' }, slots.tab?.())])
  },
})
const stubs = { V2Page, InfoNote: { template: '<i class="note" />' } }

beforeEach(() => {
  route.query = {}
  toasts.length = 0
  localStorage.clear()
  writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
  vi.stubGlobal('ref', ref)
  vi.stubGlobal('computed', computed)
  vi.stubGlobal('watch', watch)
  vi.stubGlobal('onMounted', onMounted)
  vi.stubGlobal('definePageMeta', () => {})
  vi.stubGlobal('useRoute', () => route)
  vi.stubGlobal('useRouter', () => ({ replace: (o: { query: Record<string, string> }) => { route.query = { ...o.query } } }))
  vi.stubGlobal('useToast', () => ({ showToast: (m: string, t?: string) => toasts.push({ m, t }) }))
})
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })
enableAutoUnmount(afterEach)

async function mountPage() {
  const w = mount(Page, { global: { stubs } })
  await flushPromises()
  return w
}
const rowIds = (w: any) => w.findAll('ul.list .row .no').map((n: any) => n.text())
const stored = () => JSON.parse(localStorage.getItem(KEY)!)

describe('開發協作：收集任務', () => {
  it('清單帶固定項次與進度計數；預設選第一個，指令每條各一個等寬方塊，權限寫在畫面上', async () => {
    const w = await mountPage()
    expect(rowIds(w)).toEqual(['D1', 'D1b', 'D2', 'D1c', 'D1d', 'D1e', 'D3', 'D4', 'D5', 'D6', 'D7', 'D8', 'D9', 'D10'])
    expect(w.findAll('ul.list .row')[0].text()).toContain('0/3')
    const blocks = w.findAll('pre[data-cmd]')
    expect(blocks).toHaveLength(3)
    expect(blocks[0].text()).toBe(DEVKIT_TASKS[0].commands[0].text)
    expect(w.find('[data-perm]').text()).toContain('唯讀帳號')
    expect(w.findAll('button.copy')).toHaveLength(3)
  })

  it('(5) D1 每條指令旁有成功判準、失敗時、要帶回什麼', async () => {
    const w = await mountPage()
    const metas = w.findAll('dl.cmeta')
    expect(metas).toHaveLength(3)
    expect(metas[0].text()).toContain('成功判準')
    expect(metas[0].text()).toContain('停止')
    expect(metas[1].text()).toContain('失敗時')
    expect(metas[1].text()).toContain('繼續')
    expect(metas[2].text()).toContain('要帶回什麼')
  })

  it('(1)(6) D2：權限措辭、連線前檢查兩條可複製、取回與清理備註、失敗帶回說明', async () => {
    route.query = { t: 'D2' }
    const w = await mountPage()
    const perm = w.find('[data-perm]').text()
    expect(perm).toContain('查詢本身唯讀')
    expect(perm).toContain('建立輸出檔')
    expect(w.findAll('pre[data-pre]').map((p: any) => p.text())).toEqual(['uemcli -version', 'uemcli /sys/general show'])
    expect(w.findAll('pre[data-cmd]')[0].text()).toContain('/tmp/unity_hosts_$(date +%Y%m%d_%H%M).csv')
    expect(w.find('.detail').text()).toContain('失敗訊息怎麼收集帶回')
    expect(w.find('.notes').text()).toContain('scp')
    expect(w.find('.notes').text()).toContain('清理')
    expect(w.find('.detail').text()).not.toContain('未實測')
    await w.findAll('button.copy')[0].trigger('click')          // 連線前檢查第 1 條
    await flushPromises()
    expect(writeText).toHaveBeenCalledWith('uemcli -version')
  })

  it('按複製：寫入剪貼簿的是那一條指令原文，按鈕變「已複製」並跳成功提示', async () => {
    const w = await mountPage()
    await w.findAll('button.copy')[1].trigger('click')
    await flushPromises()
    expect(writeText).toHaveBeenCalledTimes(1)
    expect(writeText).toHaveBeenCalledWith(DEVKIT_TASKS[0].commands[1].text)
    expect(w.findAll('button.copy')[1].text()).toContain('已複製')
    expect(w.findAll('button.copy')[0].text()).toBe('複製')
    expect(toasts.some(x => x.t === 'success')).toBe(true)
  })

  it('剪貼簿被拒絕又沒有備援時，跳錯誤提示而不是假裝成功', async () => {
    writeText.mockRejectedValue(new Error('denied'))
    Object.defineProperty(document, 'execCommand', { value: () => false, configurable: true })
    const w = await mountPage()
    await w.findAll('button.copy')[0].trigger('click')
    await flushPromises()
    expect(toasts.some(x => x.t === 'error')).toBe(true)
    expect(w.findAll('button.copy')[0].text()).toBe('複製')
  })

  it('點左邊另一個任務會換右邊內容（網址帶 t）；placeholder 沒有指令也沒有進度按鈕', async () => {
    const w = await mountPage()
    await w.findAll('ul.list .row')[2].trigger('click')
    await flushPromises()
    expect(route.query.t).toBe('D2')
    expect(w.findAll('pre[data-cmd]')).toHaveLength(6)
    route.query = { t: 'D1b' }
    await flushPromises()
    expect(w.findAll('pre[data-cmd]')).toHaveLength(0)
    expect(w.find('.detail').text()).toContain('等第一步結果')
    expect(w.find('.detail .dact button').exists()).toBe(false)
  })

  it('標籤篩選與搜尋（網址帶 tag、q），無結果有說明與清除連結', async () => {
    const w = await mountPage()
    await w.findAll('button.chip').find((c: any) => c.text().startsWith('Unity'))!.trigger('click')
    await flushPromises()
    expect(route.query.tag).toBe('Unity')
    expect(rowIds(w)).toEqual(['D2', 'D3'])
    await w.find('input[type=search]').setValue('activemonitortype')
    await flushPromises()
    expect(w.find('.empty').text()).toContain('沒有符合條件的任務')
    await w.find('.empty button.link').trigger('click')
    await flushPromises()
    expect(rowIds(w)).toEqual(['D1', 'D1b', 'D2', 'D1c', 'D1d', 'D1e', 'D3', 'D4', 'D5', 'D6', 'D7', 'D8', 'D9', 'D10'])
  })
})

describe('開發協作：(4) 逐指令進度', () => {
  it('勾「已取得輸出」：該條也算已執行、任務變進行中並顯示還缺哪幾條；全勾＝已收集；存成結構（含時間與版本）', async () => {
    const w = await mountPage()
    await w.findAll('input[data-got]')[0].setValue(true)
    expect(w.find('[data-prog] input[data-ran]').element).toHaveProperty('checked', true)
    expect(w.find('.detail .dact .st').text()).toBe('進行中')
    expect(w.find('.missing').text()).toBe('還缺第 2、3 條')
    expect(w.findAll('ul.list .row')[0].text()).toContain('1/3')
    const s = stored()
    expect(s.v).toBe(2)
    expect(s.updated).toMatch(/^\d{4}-\d\d-\d\dT/)
    expect(s.tasks.D1.ver).toBe(1)
    expect(s.tasks.D1.cmds['0']).toMatchObject({ ran: true, got: true })
    await w.findAll('input[data-got]')[1].setValue(true)
    await w.findAll('input[data-got]')[2].setValue(true)
    expect(w.find('.detail .dact .st').text()).toBe('已收集')
    expect(w.find('.missing').exists()).toBe(false)
    expect(w.find('.num').text()).toContain('待收集 12')           // D2、D1c、D1d、D1e、D3、D4、D5、D6、D7、D8、D9、D10 還是待收集
    expect(w.findAll('.num')[2].text()).toContain('已收集 1')
  })

  it('備註存起來，重新載入還在', async () => {
    const w = await mountPage()
    const note = w.findAll('input[data-note]')[1]
    await note.setValue('找不到表 10:31')
    await note.trigger('change')
    expect(stored().tasks.D1.cmds['1'].note).toBe('找不到表 10:31')
    const w2 = await mountPage()
    expect((w2.findAll('input[data-note]')[1].element as HTMLInputElement).value).toBe('找不到表 10:31')
  })

  it('全部標為已取得／全部取消', async () => {
    const w = await mountPage()
    const btns = () => w.findAll('.detail .dact button')
    await btns().find((b: any) => b.text() === '全部標為已取得')!.trigger('click')
    expect(w.find('.detail .dact .st').text()).toBe('已收集')
    await btns().find((b: any) => b.text() === '全部取消')!.trigger('click')
    expect(w.find('.detail .dact .st').text()).toBe('待收集')
  })

  it('舊格式（單一勾選）能遷移不報錯：D1 顯示已收集；D2 因指令改版顯示「指令已更新」', async () => {
    localStorage.setItem('webit.devkit.done.v1', JSON.stringify({ D1: true, D2: true }))
    const w = await mountPage()
    expect(w.find('.detail .dact .st').text()).toBe('已收集')
    expect((w.findAll('input[data-note]')[0].element as HTMLInputElement).value).toContain('舊版進度')
    route.query = { t: 'D2' }
    await flushPromises()
    expect(w.find('.stale').text()).toContain('指令已更新')
    expect(w.find('.detail .dact .st').text()).toBe('待收集')
  })

  it('頁首的計數可點，點了就篩出那些任務（下鑽）', async () => {
    const w = await mountPage()
    await w.find('.num').trigger('click')
    await flushPromises()
    expect(route.query.st).toBe('待收集')
    expect(rowIds(w)).toEqual(['D1', 'D2', 'D1c', 'D1d', 'D1e', 'D3', 'D4', 'D5', 'D6', 'D7', 'D8', 'D9', 'D10'])
  })

  it('localStorage 讀寫都丟例外、或內容是壞掉的 JSON，頁面照常運作', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked') })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
    const w = await mountPage()
    expect(rowIds(w)).toEqual(['D1', 'D1b', 'D2', 'D1c', 'D1d', 'D1e', 'D3', 'D4', 'D5', 'D6', 'D7', 'D8', 'D9', 'D10'])
    await w.findAll('input[data-got]')[0].setValue(true)           // 存不進去也不能炸
    expect(w.find('.detail .dact .st').text()).toBe('進行中')
    vi.restoreAllMocks()
    localStorage.setItem(KEY, '{壞掉')
    const w2 = await mountPage()
    expect(rowIds(w2)).toEqual(['D1', 'D1b', 'D2', 'D1c', 'D1d', 'D1e', 'D3', 'D4', 'D5', 'D6', 'D7', 'D8', 'D9', 'D10'])
  })
})

describe('開發協作：(8) 只在此瀏覽器、匯出入進度', () => {
  it('畫面寫明換電腦或清除資料就會消失，並顯示最後更新時間（沒記錄時寫尚未記錄）', async () => {
    const w = await mountPage()
    expect(w.find('.local').text()).toContain('換電腦或清除瀏覽資料就會消失')
    expect(w.find('.local').text()).toContain('尚未記錄')
    await w.findAll('input[data-got]')[0].setValue(true)
    expect(w.find('.local').text()).not.toContain('尚未記錄')
    expect(w.find('.local').text()).toMatch(/\d{4}-\d\d-\d\d \d\d:\d\d/)
  })

  it('匯入進度：讀檔合併；格式不對跳錯誤、不動現有進度', async () => {
    const w = await mountPage()
    const file = (txt: string) => ({ text: async () => txt })
    const input = w.find('input[type=file]')
    const pick = async (txt: string) => {
      Object.defineProperty(input.element, 'files', { value: [file(txt)], configurable: true })
      await input.trigger('change')
      await flushPromises()
    }
    await pick('不是 json')
    expect(toasts.some(x => x.t === 'error' && x.m.includes('匯入失敗'))).toBe(true)
    expect(w.find('.detail .dact .st').text()).toBe('待收集')
    const exported = JSON.stringify({ v: 2, updated: '2026-11-01T00:00:00.000Z', tasks: { D1: { ver: 1, cmds: { 0: { ran: true, got: true, note: '匯入的' } } } } })
    await pick(exported)
    expect(w.find('.detail .dact .st').text()).toBe('進行中')
    expect((w.findAll('input[data-note]')[0].element as HTMLInputElement).value).toBe('匯入的')
    expect(stored().tasks.D1.cmds['0'].note).toBe('匯入的')
  })

  it('匯出進度會下載 .json 檔（不帶 BOM）', async () => {
    const blobs: Blob[] = []
    URL.createObjectURL = (b: Blob) => { blobs.push(b); return 'blob:x' }
    URL.revokeObjectURL = () => {}
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})      // 不真的導航
    const w = await mountPage()
    await w.findAll('input[data-got]')[0].setValue(true)
    await w.findAll('.local button').find((b: any) => b.text().includes('匯出進度'))!.trigger('click')
    expect(blobs).toHaveLength(1)
    const text = await blobs[0].text()
    expect(text.startsWith('\ufeff')).toBe(false)
    expect(JSON.parse(text).tasks.D1.cmds['0'].got).toBe(true)
  })
})

describe('開發協作：整理結果（去識別）', () => {
  const INPUT = 'host 10.99.1.5 mac AA:BB:CC:00:11:22\nagain 10.99.1.5 and 10.99.1.6\nver 5.5.0.0 time 12:30:45 fe80::1c2:3d4'
  async function run(w: any, text = INPUT) {
    await w.find('#dk-raw').setValue(text)
    await w.find('button.primary').trigger('click')
    await flushPromises()
  }
  const out = (w: any) => (w.find('#dk-out').element as HTMLTextAreaElement).value

  it('按「去識別」：結果換成代號、顯示替換個數、IPv6 提示、人工確認清單、對照表', async () => {
    route.query = { tab: 'sanitize' }
    const w = await mountPage()
    await run(w)
    expect(out(w)).toBe('host 192.0.2.1 mac 02:00:00:00:00:00\nagain 192.0.2.1 and 192.0.2.2\nver 5.5.0.0 time 12:30:45 fe80::1c2:3d4')
    expect(w.find('.sum').text()).toBe('已替換 2 個 IP、1 個 MAC（共 4 處）')
    expect(w.find('[data-v6]').text()).toContain('偵測到 1 個 IPv6，未替換，請手動處理')
    expect(w.find('[data-review]').text()).toContain('5.5.0.0')
    expect(w.find('[data-map]').text()).toContain('對照表留在本機，不要寄出')
  })

  it('沒貼東西就按去識別：提示、不產生結果', async () => {
    route.query = { tab: 'sanitize' }
    const w = await mountPage()
    await w.find('button.primary').trigger('click')
    expect(toasts.some(x => x.t === 'warn')).toBe(true)
    expect(w.find('.sum').exists()).toBe(false)
    expect(w.find('[data-map]').exists()).toBe(false)
  })

  it('(2) 沒勾「我已檢查」前，複製結果與下載都是停用；列出九類未處理資料；勾了才能用', async () => {
    route.query = { tab: 'sanitize' }
    const w = await mountPage()
    expect(w.find('[data-sens]').exists()).toBe(false)
    await run(w, 'x 10.99.9.9')
    const sens = w.find('[data-sens]').text()
    for (const k of ['主機名', '網域', 'IQN', 'WWPN／WWN', '設備序號', 'LUN 名稱', '共享／掛載路徑', '帳號', 'IPv6']) expect(sens).toContain(k)
    expect(w.find('[data-copy-result]').attributes('disabled')).toBeDefined()
    expect(w.find('[data-dl-result]').attributes('disabled')).toBeDefined()
    await w.find('[data-copy-result]').trigger('click')
    expect(writeText).not.toHaveBeenCalled()
    await w.find('input[data-checked]').setValue(true)
    expect(w.find('[data-copy-result]').attributes('disabled')).toBeUndefined()
    await w.find('[data-copy-result]').trigger('click')
    await flushPromises()
    expect(writeText).toHaveBeenCalledWith('x 192.0.2.1')
  })

  it('(2) 每次貼上新內容、每次重新去識別，確認都會重置', async () => {
    route.query = { tab: 'sanitize' }
    const w = await mountPage()
    await run(w, 'x 10.99.9.9')
    await w.find('input[data-checked]').setValue(true)
    expect(w.find('[data-copy-result]').attributes('disabled')).toBeUndefined()
    await w.find('#dk-raw').setValue('y 10.99.9.10')                 // 貼上新內容
    expect(w.find('[data-copy-result]').attributes('disabled')).toBeDefined()
    expect((w.find('input[data-checked]').element as HTMLInputElement).checked).toBe(false)
    await w.find('button.primary').trigger('click')
    await w.find('input[data-checked]').setValue(true)
    await w.find('button.primary').trigger('click')                 // 同樣內容重新去識別
    expect(w.find('[data-copy-result]').attributes('disabled')).toBeDefined()
  })

  it('(3) 對照表：不提供下載；預設不顯示內容；複製要先輸入「對照表」三字；複製一次後要重新確認；視覺與結果區隔離', async () => {
    route.query = { tab: 'sanitize' }
    const w = await mountPage()
    await run(w, 'a 10.99.1.1 b aa:bb:cc:00:11:22')
    const map = w.find('[data-map]')
    expect(map.text()).not.toContain('下載')
    expect(map.findAll('button').some((b: any) => b.text().includes('下載'))).toBe(false)
    expect(map.find('textarea').exists()).toBe(false)               // 預設不顯示內容
    expect(w.find('.pane.res').element.contains(map.element)).toBe(false)   // 不在結果那一塊裡
    expect(map.classes()).toContain('map')
    expect(w.find('.pane.res').classes()).toContain('res')
    // 沒輸入就不能複製
    expect(w.find('[data-copy-map]').attributes('disabled')).toBeDefined()
    await w.find('[data-copy-map]').trigger('click')
    expect(writeText).not.toHaveBeenCalled()
    await w.find('input[data-map-word]').setValue('對照')
    expect(w.find('[data-copy-map]').attributes('disabled')).toBeDefined()
    await w.find('input[data-map-word]').setValue('對照表')
    expect(w.find('[data-copy-map]').attributes('disabled')).toBeUndefined()
    await w.find('[data-copy-map]').trigger('click')
    await flushPromises()
    expect(writeText).toHaveBeenCalledTimes(1)
    expect(writeText.mock.calls[0][0]).toContain('10.99.1.1\t->\t192.0.2.1')
    expect(toasts.some(x => x.m.includes('請勿寄出'))).toBe(true)
    expect(w.find('[data-copy-map]').attributes('disabled')).toBeDefined()   // 用過一次就要重新輸入
    // 顯示
    await w.find('[data-show-map]').trigger('click')
    expect((w.find('.map textarea').element as HTMLTextAreaElement).value).toContain('aa:bb:cc:00:11:22\t->\t02:00:00:00:00:00')
  })

  it('分兩批貼上沿用同一份對照表；清除後重新編號', async () => {
    route.query = { tab: 'sanitize' }
    const w = await mountPage()
    await run(w, '10.99.1.1')
    await run(w, '10.99.1.2 10.99.1.1')
    expect(out(w)).toBe('192.0.2.2 192.0.2.1')
    expect(w.find('.sum').text()).toContain('已替換 2 個 IP')
    await w.findAll('.mact button').find((b: any) => b.text().includes('清除'))!.trigger('click')
    expect(w.find('[data-map]').exists()).toBe(false)
    await run(w, '10.99.1.2')
    expect(out(w)).toBe('192.0.2.1')
  })
})
