// 9-3 收集憑證清單實掛：類型篩選、「被引用」展開並連到引用處、最近使用顯示、操作事件、畫面不出現密碼。
// V2Table／InfoNote／NuxtLink 為簡化替身；全假資料、無網路。
import { enableAutoUnmount, mount } from '@vue/test-utils'
import { computed, defineComponent, h, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Comp from '../components/v2/CredentialsTable.vue'

enableAutoUnmount(afterEach)

const cred = (o: any = {}) => ({
  id: 1, name: 'wug-a', kind: 'whatsup', username: 'svc_x', scope: null, note: null, updated_at: '2026-11-02 09:00:00', has_secret: true,
  refs: [], ref_count: 0, last_used_at: null, last_used_ok: null, ...o,
})
const CREDS = [
  cred({ id: 1, name: 'wug-a', refs: [{ type: 'whatsup', label: '外部整合：WhatsUp Gold', link: '/v2/settings?st=integrations&sub=whatsup' }], ref_count: 1,
         last_used_at: '2026-11-02 08:00:00', last_used_ok: false }),
  cred({ id: 2, name: 'st-1', kind: 'storage', scope: '10.99.0.' }),
  cred({ id: 3, name: 'ssh-1', kind: 'ssh', ref_count: 25, refs: [{ type: 'host', label: '主機 10.99.3.7（納管時指定）', link: '/v2/hosts?q=10.99.3.7' }] }),
]

// 替身表格：把每欄 text() 的結果畫出來，並提供具名插槽
const V2Table = defineComponent({
  props: ['rows', 'cols', 'prefix'],
  setup(p, { slots }) {
    return () => h('table', {}, p.rows.map((r: any) => h('tr', { class: 'row' }, [
      h('td', { class: 'no' }, `${p.prefix}${r.__no}`),
      ...p.cols.map((c: any) => h('td', { class: `c-${c.k}` },
        slots[`cell-${c.k}`] ? slots[`cell-${c.k}`]({ row: r }) : String(c.text ? c.text(r) : r[c.k] ?? ''))),
    ])))
  },
})
const mnt = () => mount(Comp, { props: { creds: CREDS }, global: { stubs: { InfoNote: { template: '<i class="note"><slot /></i>' }, NuxtLink: { props: ['to'], template: '<a class="lnk" :href="to"><slot /></a>' }, V2Table } } })

beforeEach(() => { for (const [k, v] of Object.entries({ ref, computed })) vi.stubGlobal(k, v) })

describe('收集憑證清單', () => {
  it('每列有固定項次，顯示最近使用（失敗會標出）與從未使用', () => {
    const w = mnt()
    expect(w.findAll('.no').map(x => x.text())).toEqual(['C1', 'C2', 'C3'])
    expect(w.find('.c-last_used_at').text()).toContain('（失敗）')
    expect(w.findAll('.c-last_used_at')[1].text()).toBe('從未使用')
  })

  it('依類型篩選後，項次不變', async () => {
    const w = mnt()
    await w.find('select.kind').setValue('storage')
    expect(w.findAll('.row')).toHaveLength(1)
    expect(w.find('.no').text()).toBe('C2')
    await w.find('select.kind').setValue('')
    expect(w.findAll('.row')).toHaveLength(3)
  })

  it('被引用可展開，連結指向引用處；超過明細上限會註明未列出的數量', () => {
    const w = mnt()
    const a = w.findAll('.c-ref_total')[0]
    expect(a.find('summary').text()).toBe('1 處')
    expect(a.find('a.lnk').attributes('href')).toBe('/v2/settings?st=integrations&sub=whatsup')
    expect(w.findAll('.c-ref_total')[1].text()).toBe('—')
    expect(w.findAll('.c-ref_total')[2].find('.more').text()).toContain('另有 24 處未列出')
  })

  it('操作事件帶出該列；畫面不含任何密碼欄位', async () => {
    const w = mnt()
    const first = w.findAll('.row')[0]
    await first.find('.b-test').trigger('click')
    await first.find('.b-edit').trigger('click')
    await first.find('.b-del').trigger('click')
    expect(w.emitted('test')![0][0]).toMatchObject({ name: 'wug-a' })
    expect(w.emitted('edit')![0][0]).toMatchObject({ name: 'wug-a' })
    expect(w.emitted('remove')![0]).toEqual(['wug-a'])
    expect(w.find('input[type=password]').exists()).toBe(false)
    expect(w.text()).toContain('已加密儲存')
  })
})
