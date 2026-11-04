<script setup lang="ts">
// 4-9 補登分派（v1.499.1）：每台 CIA 缺哪些、依使用單位／系統分組，一個單位匯出一份 Excel 發給他補。
// 數字全由後端 data_fix.py 算（verify_numbers 同一支）；這裡只篩選、排序、匯出。
const { apiFetch } = useApi()
const { showToast } = useToast()
const { exportTable, busy: exporting } = useTableExport()
const route = useRoute()
const router = useRouter()
const data = ref<any>(null)
const err = ref('')
async function load() {
  err.value = ''
  try {
    data.value = await apiFetch('/api/data-fix')
  } catch (e: any) {
    err.value = e?.data?.detail ?? e?.message ?? '載入失敗'
  }
}
await load()

type Tab = 'units' | 'systems' | 'machines' | 'settings'
const TABS: { k: Tab; label: string }[] = [
  { k: 'units', label: '依使用單位' }, { k: 'systems', label: '依系統' }, { k: 'machines', label: '逐台' },
  { k: 'settings', label: '應填項目' },
]
const tab = computed<Tab>({
  get: () => (TABS.some((t) => t.k === route.query.tab) ? route.query.tab as Tab : 'units'),
  set: (v) => { router.replace({ query: { ...route.query, tab: v } }) },
})
// 逐台的篩選（從卡片／分組格子點進來）
const fUnit = ref('')
const fSys = ref('')
const fItem = ref('')
const q = ref('')
function drill(opts: { unit?: string; sys?: string; item?: string }) {
  fUnit.value = opts.unit ?? ''
  fSys.value = opts.sys ?? ''
  fItem.value = opts.item ?? ''
  q.value = ''
  tab.value = 'machines'
}
const items = computed(() => (data.value?.items ?? []) as { key: string; label: string; required: boolean }[])
const reqItems = computed(() => items.value.filter((i) => i.required))
const NO_APID = '（未填 APID）'
const ANY = '__any'   // 「有任何一項缺漏」

function groupRows(src: any[], prefix: string) {
  return src.map((g, i) => {
    const r: any = { ...g, no: `${prefix}${i + 1}`, _i: i }
    for (const it of items.value) r[`m_${it.key}`] = g.missing_by_item[it.key]
    return r
  })
}
const unitRows = computed(() => groupRows(data.value?.units ?? [], 'U'))
const sysRows = computed(() => groupRows(data.value?.systems ?? [], 'S'))
const { sortKey: uKey, sortDir: uDir, toggle: uToggle, sorted: unitSorted } = useSort(unitRows, '')
const { sortKey: sKey, sortDir: sDir, toggle: sToggle, sorted: sysSorted } = useSort(sysRows, '')

const machineRows = computed(() => (data.value?.machines ?? []).map((m: any, i: number) => ({ ...m, no: `M${i + 1}`, _i: i })))
const machineF = computed(() => {
  const s = q.value.trim().toLowerCase()
  return machineRows.value.filter((m: any) =>
    (!fUnit.value || m.unit === fUnit.value)
    && (!fSys.value || (fSys.value === NO_APID ? !m.api_ids.length : m.api_ids.includes(fSys.value)))
    && (!fItem.value || (fItem.value === ANY ? m.missing.length > 0 : m.missing.includes(fItem.value)))
    && (!s || JSON.stringify(m).toLowerCase().includes(s)))
})
const { sortKey: mKey, sortDir: mDir, toggle: mToggle, sorted: machineSorted } = useSort(machineF, '')

const M_COLS: [string, string][] = [['hostname', '主機名'], ['ip', 'IP'], ['unit', '使用單位'], ['systems', '系統'],
  ['room', '機房'], ['rack', '機櫃'], ['model', '型號'], ['missing_text', '缺漏'], ['score', '分數']]
function exportMachines(title: string, rows: any[]) {
  exportTable(title, ['項次', ...M_COLS.map((c) => c[1])], rows.map((r) => [r.no, ...M_COLS.map((c) => r[c[0]] ?? '')]))
}
function exportUnit(unit: string) {
  const rows = machineRows.value.filter((m: any) => m.unit === unit && m.missing.length)
  exportMachines(`補登_${unit}`, rows)
}
function exportGroups(title: string, rows: any[], keyLabel: string) {
  exportTable(title, ['項次', keyLabel, '台數', '有缺漏（台）', '分數', ...reqItems.value.map((i) => i.label)],
    rows.map((r) => [r.no, r.key, r.machine_count, r.missing_machines, r.score, ...reqItems.value.map((i) => r.missing_by_item[i.key])]))
}

// 趨勢（滿兩週才畫）
const trendPts = computed(() => {
  const t = data.value?.trend ?? []
  if (t.length < 2) return null
  const w = 320, h = 70, pad = 6
  const xs = (i: number) => pad + (i * (w - 2 * pad)) / (t.length - 1)
  const lo = Math.min(...t.map((x: any) => x.score)), hi = Math.max(...t.map((x: any) => x.score))
  const span = Math.max(hi - lo, 1)
  const ys = (v: number) => h - pad - ((v - lo) * (h - 2 * pad)) / span
  return { w, h, d: t.map((x: any, i: number) => `${i ? 'L' : 'M'}${xs(i).toFixed(1)},${ys(x.score).toFixed(1)}`).join(' '),
    dots: t.map((x: any, i: number) => ({ x: xs(i), y: ys(x.score), t: `${x.week}　${x.score}%` })) }
})

// 應填項目（管理員）
const reqDraft = ref<string[]>([])
watch(() => data.value, (d) => { reqDraft.value = (d?.items ?? []).filter((i: any) => i.required).map((i: any) => i.key) }, { immediate: true })
async function saveReq() {
  try {
    await apiFetch('/api/data-fix/required', { method: 'PUT', body: { items: reqDraft.value } })
    showToast('應填項目已儲存', 'success')
    await load()
  } catch (e: any) {
    showToast(e?.data?.detail ?? '儲存失敗', 'error')
  }
}
useHead({ title: '補登分派' })
</script>

<template>
  <div class="wrap">
    <V2Banner to="/v2/datafix" label="9.1 待補資料" />
    <div class="ey">WAR ROOM · 4-9</div>
    <h1>補登分派
      <InfoNote>
        單位是「台」，只看帳內（CIA）現役機器；退役、帳外（DYN／VC／AUTO）不算。<br>
        每台逐項檢查：機房、機櫃U（機櫃＋U 看得懂）、負責人（CIA「使用者」欄）、保管人、APID、型號（有填且不是籠統描述）、
        OS、環境、存在可信度（不只有 CIA 登記，還有掃描／匯入／收集任一證據）。VM 不看機櫃U與型號。<br>
        分數＝已填項 ÷ 應填項；應填項目在「應填項目」分頁設定。<br>
        分組用 CIA「使用單位」：盤點單位與擁有者全部是同一個值分不出誰；保管人是 SP 自己人；AP 部門掛在系統上，另有「依系統」分組。<br>
        每週第一次打開這頁時存一份快照，滿兩週就有趨勢線。
      </InfoNote>
    </h1>
    <p v-if="err" class="bad">載入失敗：{{ err }}</p>
    <template v-if="data">
      <div class="cards">
        <div class="card static"><span class="n">{{ data.score }}%</span><span class="l">整體分數（已填 {{ data.filled_total }}／應填 {{ data.required_total }}）</span></div>
        <button class="card" @click="drill({})"><span class="n">{{ data.machine_count }}</span><span class="l">機器（台）</span></button>
        <button class="card warn" @click="drill({ item: ANY })"><span class="n">{{ data.missing_machines }}</span><span class="l">有缺漏（台）</span></button>
        <button v-for="it in reqItems" :key="it.key" class="card" :class="{ warn: data.missing_by_item[it.key] }" @click="drill({ item: it.key })">
          <span class="n">{{ data.missing_by_item[it.key] }}</span><span class="l">缺 {{ it.label }}（台）</span>
        </button>
      </div>
      <div class="trend">
        <template v-if="trendPts">
          <svg :viewBox="`0 0 ${trendPts.w} ${trendPts.h}`" :width="trendPts.w" :height="trendPts.h" role="img" aria-label="每週分數趨勢">
            <path :d="trendPts.d" fill="none" stroke="var(--chart-1)" stroke-width="2" />
            <circle v-for="(p, i) in trendPts.dots" :key="i" :cx="p.x" :cy="p.y" r="3" fill="var(--chart-1)"><title>{{ p.t }}</title></circle>
          </svg>
          <span class="muted small">每週分數（{{ data.trend.length }} 週）</span>
        </template>
        <span v-else class="muted small">每週分數：目前 {{ data.trend.length }} 週，滿兩週出現趨勢線</span>
      </div>

      <div class="tabs">
        <button v-for="t in TABS" :key="t.k" class="tb" :class="{ on: tab === t.k }" @click="tab = t.k">{{ t.label }}</button>
      </div>

      <template v-if="tab === 'units' || tab === 'systems'">
        <div class="bar">
          <button class="btn" :disabled="exporting" @click="tab === 'units' ? exportGroups('補登_依使用單位', unitSorted, '使用單位') : exportGroups('補登_依系統', sysSorted, 'APID')">匯出 Excel</button>
        </div>
        <div class="tbl-wrap"><table>
          <thead><tr>
            <template v-if="tab === 'units'">
              <SortTh k="_i" :active="uKey" :dir="uDir" @sort="uToggle">項次</SortTh>
              <SortTh k="key" :active="uKey" :dir="uDir" @sort="uToggle">使用單位</SortTh>
              <SortTh k="machine_count" :active="uKey" :dir="uDir" @sort="uToggle">台數</SortTh>
              <SortTh k="missing_machines" :active="uKey" :dir="uDir" @sort="uToggle">有缺漏</SortTh>
              <SortTh k="score" :active="uKey" :dir="uDir" @sort="uToggle">分數</SortTh>
              <SortTh v-for="it in reqItems" :key="it.key" :k="`m_${it.key}`" :active="uKey" :dir="uDir" @sort="uToggle">{{ it.label }}</SortTh>
              <th />
            </template>
            <template v-else>
              <SortTh k="_i" :active="sKey" :dir="sDir" @sort="sToggle">項次</SortTh>
              <SortTh k="key" :active="sKey" :dir="sDir" @sort="sToggle">APID</SortTh>
              <SortTh k="machine_count" :active="sKey" :dir="sDir" @sort="sToggle">台數</SortTh>
              <SortTh k="missing_machines" :active="sKey" :dir="sDir" @sort="sToggle">有缺漏</SortTh>
              <SortTh k="score" :active="sKey" :dir="sDir" @sort="sToggle">分數</SortTh>
              <SortTh v-for="it in reqItems" :key="it.key" :k="`m_${it.key}`" :active="sKey" :dir="sDir" @sort="sToggle">{{ it.label }}</SortTh>
            </template>
          </tr></thead>
          <tbody>
            <tr v-for="r in (tab === 'units' ? unitSorted : sysSorted)" :key="r.no">
              <td class="mono">{{ r.no }}</td>
              <td>{{ r.key }}</td>
              <td class="num"><button class="cnt" @click="tab === 'units' ? drill({ unit: r.key }) : drill({ sys: r.key })">{{ r.machine_count }}</button></td>
              <td class="num">{{ r.missing_machines }}</td>
              <td class="num">{{ r.score }}%</td>
              <td v-for="it in reqItems" :key="it.key" class="num">
                <button v-if="r.missing_by_item[it.key]" class="cnt warnt" @click="tab === 'units' ? drill({ unit: r.key, item: it.key }) : drill({ sys: r.key, item: it.key })">{{ r.missing_by_item[it.key] }}</button>
                <span v-else class="muted">0</span>
              </td>
              <td v-if="tab === 'units'"><button class="lnk" :disabled="exporting || !r.missing_machines" @click="exportUnit(r.key)">匯出這個單位</button></td>
            </tr>
          </tbody>
        </table></div>
      </template>

      <template v-else-if="tab === 'machines'">
        <div class="bar">
          <select v-model="fUnit"><option value="">全部使用單位</option><option v-for="u in data.units" :key="u.key" :value="u.key">{{ u.key }}</option></select>
          <select v-model="fSys"><option value="">全部系統</option><option v-for="s in data.systems" :key="s.key" :value="s.key">{{ s.key }}</option></select>
          <select v-model="fItem"><option value="">全部項目</option><option :value="ANY">有任何缺漏</option><option v-for="it in reqItems" :key="it.key" :value="it.key">缺 {{ it.label }}</option></select>
          <input v-model="q" class="kw" placeholder="搜項次、主機名、IP…">
          <span class="muted small">{{ machineSorted.length }} 台</span>
          <button class="btn" :disabled="exporting || !machineSorted.length" @click="exportMachines(`補登_逐台${fUnit ? '_' + fUnit : ''}`, machineSorted)">匯出 Excel</button>
        </div>
        <div class="tbl-wrap"><table>
          <thead><tr>
            <SortTh k="_i" :active="mKey" :dir="mDir" @sort="mToggle">項次</SortTh>
            <SortTh v-for="c in M_COLS" :key="c[0]" :k="c[0]" :active="mKey" :dir="mDir" @sort="mToggle">{{ c[1] }}</SortTh>
          </tr></thead>
          <tbody>
            <tr v-if="!machineSorted.length"><td :colspan="M_COLS.length + 1" class="muted">沒有</td></tr>
            <tr v-for="m in machineSorted" :key="m.no">
              <td class="mono">{{ m.no }}</td>
              <td><NuxtLink :to="`/assets/${encodeURIComponent(m.serial)}`">{{ m.hostname || m.serial }}</NuxtLink></td>
              <td class="mono">{{ m.ip || '—' }}</td><td>{{ m.unit }}</td><td>{{ m.systems || '—' }}</td>
              <td>{{ m.room }}</td><td>{{ m.rack || '—' }}</td><td>{{ upText(m.model) || '—' }}</td>
              <td :class="{ warnt: m.missing.length }">{{ m.missing_text || '—' }}</td><td class="num">{{ m.score }}%</td>
            </tr>
          </tbody>
        </table></div>
      </template>

      <template v-else>
        <div class="setbox">
          <label v-for="it in items" :key="it.key" class="chk">
            <input v-model="reqDraft" type="checkbox" :value="it.key" :disabled="!data.can_edit"> {{ it.label }}
          </label>
          <button v-if="data.can_edit" class="btn pri" :disabled="!reqDraft.length" @click="saveReq">儲存</button>
        </div>
      </template>
    </template>
  </div>
</template>

<style scoped>
.wrap { padding: 16px; }
.ey { font-family: var(--disp); font-size: 11px; letter-spacing: 3px; color: var(--brand-dark); text-transform: uppercase; }
h1 { font-family: var(--disp); font-size: 24px; font-weight: 600; margin: 4px 0 12px; color: var(--ink); }
.bad { color: var(--bad); }
.warnt { color: var(--warn-text); }
.cards { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }
.card { display: flex; flex-direction: column; align-items: flex-start; padding: 10px 14px; min-width: 120px;
  border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); cursor: pointer; color: inherit; font: inherit; }
.card.static { cursor: default; }
.card .n { font-size: 22px; font-weight: 700; font-family: var(--disp); color: var(--brand); }
.card.warn .n { color: var(--warn-text); }
.card .l { font-size: 12px; color: var(--ink-aux); }
.trend { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.tabs { display: flex; gap: 4px; margin: 10px 0; border-bottom: 1px solid var(--border); flex-wrap: wrap; }
.tb { font: inherit; padding: 8px 14px; border: 0; background: none; cursor: pointer; color: var(--ink-aux); border-bottom: 2px solid transparent; }
.tb.on { color: var(--brand-dark); border-bottom-color: var(--brand); font-weight: 600; }
.bar { display: flex; gap: 8px; align-items: center; margin: 8px 0; flex-wrap: wrap; }
.bar select, .kw { padding: 5px 8px; border: 1px solid var(--border-strong); border-radius: 8px; font: inherit; background: var(--card); color: var(--ink); }
.kw { min-width: 200px; }
.btn { font: inherit; font-size: 12.5px; padding: 5px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; }
.btn.pri { background: var(--brand); border-color: var(--brand); color: #fff; }
.btn:disabled, .lnk:disabled { opacity: .5; }
.lnk { font: inherit; font-size: 12px; border: 0; background: none; color: var(--link); cursor: pointer; }
.cnt { font: inherit; border: 0; background: none; color: var(--link); cursor: pointer; padding: 0; text-decoration: underline; }
.cnt.warnt { color: var(--warn-text); }
.setbox { display: flex; gap: 14px; flex-wrap: wrap; align-items: center; padding: 12px; background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); }
.chk { font-size: 13px; color: var(--ink); }
.tbl-wrap { overflow-x: auto; background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 6px 8px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
td a { color: var(--link); }
.num { text-align: right; }
.mono { font-family: ui-monospace, Consolas, monospace; }
.muted { color: var(--muted); }
.small { font-size: 12px; }
</style>
