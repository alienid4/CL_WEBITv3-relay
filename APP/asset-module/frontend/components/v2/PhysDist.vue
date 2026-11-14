<script setup lang="ts">
// 10.2「各環境實體機分布」（V57，2.047.2 V2 原生；取代嵌入的舊元件 components/PhysicalDist.vue）。同一批 API：
// /api/reports/physical-distribution、/api/reports/physical-distribution/drill（加總與清單同一份後端計算，格子數字＝點進去的筆數）。
// 口徑：只算實體機；CIA 正式登記，排除退役、排除網路／儲存／BMC／未知（跟系統組月報表 1 同一套）。
// 畫面：三機房圓環＋分類、分公司、未歸類地點、未填、被排除的型號、不在 CIA 清單的帳外資產——每個數字點下去在下方列出是哪幾台（?pd=種類:值）。
interface RoomCategory { name: string; count: number }
interface Room { room: string; total: number; categories: RoomCategory[] }
interface Branch { name: string; count: number }
interface Data { rooms: Room[]; branches: Branch[]; unclassified?: Branch[]; unfilled_total?: number; total_physical: number; off_book: Record<string, number>; category_note: string; excluded_models: { device_model: string; count: number }[]; excluded_models_total: number }
interface DrillRow { asset_serial: string; hostname: string | null; ip: string | null; os_raw: string | null; os_canonical: string | null; location: string | null; environment: string | null; physical_location_raw: string | null; reason: string }
const { apiFetch } = useApi()
const route = useRoute()
const router = useRouter()
const data = ref<Data | null>(null)
const err = ref('')
const beginLoad = useLatest()      // 2.047.4（GPT 審查）：連按「重新整理」時只套用最新那一次
async function load() {
  const isLatest = beginLoad()
  try { const r = await apiFetch<Data>('/api/reports/physical-distribution'); if (!isLatest()) return; data.value = r; err.value = '' } catch (e: any) { if (isLatest()) err.value = `實體機分布載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` }
}
onMounted(load)
const PALETTE = ['--chart-1', '--chart-2', '--chart-3', '--chart-4', '--chart-5', '--chart-6']
const colorCache = new Map<string, string>()
function color(name: string) {
  if (name === '未分類') return 'var(--chart-gray)'
  if (!colorCache.has(name)) colorCache.set(name, `var(${PALETTE[colorCache.size % PALETTE.length]})`)
  return colorCache.get(name)!
}
const OFF_BOOK_LABEL: Record<string, string> = { DYN: '存活清單掃到、未登記', VC: 'vCenter 收到、未登記', AUTO: '納管流程建立' }
// ---- 下鑽（網址 ?pd=room:內湖機房／branch:分公司-台中／excluded_model:XXX；圓環扇區＝?pd=room:內湖機房&pc=分類，2.047.4）
const pd = computed(() => (typeof route.query.pd === 'string' ? route.query.pd : ''))
const pdParts = computed(() => { const i = pd.value.indexOf(':'); return i > 0 ? { k: pd.value.slice(0, i), v: pd.value.slice(i + 1) } : null })
const pc = computed(() => (typeof route.query.pc === 'string' ? route.query.pc : ''))
const openDrill = (k: string, v: string, cat?: string) => router.replace({ query: { ...route.query, pd: `${k}:${v}`, pc: cat } })
const closeDrill = () => router.replace({ query: { ...route.query, pd: undefined, pc: undefined } })
const drillRows = ref<DrillRow[]>([])
const drillErr = ref('')
const drillLoading = ref(false)
const beginDrill = useLatest()      // 快速換下鑽時只套用最新那一次
watch([pdParts, pc], async ([p, cat]) => {
  const isLatest = beginDrill()
  drillRows.value = []
  drillLoading.value = false
  if (!p || !['room', 'branch', 'excluded_model'].includes(p.k)) return
  drillLoading.value = true
  try {
    const r = await apiFetch<DrillRow[]>('/api/reports/physical-distribution/drill', { params: { [p.k]: p.v, ...(cat && p.k === 'room' ? { category: cat } : {}) } })
    if (!isLatest()) return
    drillRows.value = r
    drillErr.value = ''
  } catch (e: any) { if (isLatest()) drillErr.value = `明細載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` } finally { if (isLatest()) drillLoading.value = false }
}, { immediate: true })
const drillTitle = computed(() => {
  const p = pdParts.value
  if (!p) return ''
  return p.k === 'excluded_model' ? `已排除：${p.v}` : p.k === 'room' ? (p.v === '未填' ? '未填機房' : pc.value ? `${p.v}／${pc.value}` : `${p.v}（全部）`) : p.v
})
const roomRows = computed(() => (data.value?.rooms ?? []).map((r, i) => ({ ...r, __no: i + 1, cats: r.categories.map(c => `${c.name} ${c.count}`).join('、') })))
const placeRows = computed(() => {
  const d = data.value
  if (!d) return []
  const out: any[] = d.branches.map(b => ({ kind: '分公司', name: b.name, count: b.count, k: 'branch', v: b.name }))
  for (const b of d.unclassified ?? []) out.push({ kind: '未歸類地點', name: b.name, count: b.count, k: 'branch', v: b.name })
  if (d.unfilled_total) out.push({ kind: '未填機房', name: '（未填）', count: d.unfilled_total, k: 'room', v: '未填' })
  return out.map((r, i) => ({ ...r, __no: i + 1 }))
})
const exclRows = computed(() => (data.value?.excluded_models ?? []).map((m, i) => ({ ...m, __no: i + 1 })))
const offRows = computed(() => Object.entries(data.value?.off_book ?? {}).filter(([, n]) => n).map(([src, n], i) => ({ __no: i + 1, src: `${src}-`, label: OFF_BOOK_LABEL[src] ?? '', count: n })))
const sum = (xs: { count?: number; total?: number }[], k: 'count' | 'total') => xs.reduce((s, x) => s + (x[k] ?? 0), 0)
const drillCols = [{ k: 'hostname', label: '主機名', link: (r: any) => `/v2/machine/${encodeURIComponent(r.asset_serial)}`, text: (r: any) => r.hostname || r.asset_serial },
  { k: 'ip', label: 'IP', mono: true }, { k: 'physical_location_raw', label: '機房原值' }, { k: 'environment', label: '環境別' },
  { k: 'os_canonical', label: 'OS', text: (r: any) => r.os_canonical || '認不出' }, { k: 'reason', label: '判定依據' }]
</script>

<template>
  <div>
    <p v-if="err" class="err">{{ err }}</p>
    <p v-else-if="!data" class="muted">載入中…</p>
    <template v-else-if="pdParts">
      <div class="bar"><button type="button" class="btn2" @click="closeDrill">‹ 返回</button><b>{{ drillTitle }}</b><span class="muted">{{ drillRows.length }} 台</span></div>
      <p v-if="drillErr" class="err">{{ drillErr }}</p>
      <V2Table v-else :rows="drillRows.map((r, i) => ({ ...r, __no: i + 1 }))" prefix="D" row-key="asset_serial" unit="台" :cols="drillCols"
               :export-name="`實體機分布_${drillTitle}`" :empty="drillLoading ? '載入中…' : '沒有符合的主機'" />
    </template>
    <template v-else>
      <div class="bar">
        <span>實體機總計 <b>{{ data.total_physical.toLocaleString() }}</b> 台（三機房 {{ sum(data.rooms, 'total') }}＋分公司 {{ sum(data.branches, 'count') }}＋未歸類 {{ sum(data.unclassified ?? [], 'count') }}＋未填 {{ data.unfilled_total ?? 0 }}）</span>
        <InfoNote>只算實體機（虛擬機不計入）；口徑＝CIA 正式登記，排除退役、排除網路／儲存／BMC／未知（跟系統組月報表 1 同一套排除規則）。{{ data.category_note }}</InfoNote>
        <button type="button" class="btn2" @click="load">重新整理</button>
      </div>
      <div class="rooms">
        <div v-for="r in data.rooms" :key="r.room" class="room">
          <b>{{ r.room }}</b>
          <DonutChart :segments="r.categories.map(c => ({ name: c.name, count: c.count, color: color(c.name) }))" center-label="台" @segment-click="(cat: string) => openDrill('room', r.room, cat)" />
          <button type="button" class="lb" @click="openDrill('room', r.room)">看全部 {{ r.total.toLocaleString() }} 台 ›</button>
        </div>
      </div>
      <h3 class="h3">機房</h3>
      <V2Table :rows="roomRows" prefix="R" row-key="room" unit="個機房" export-name="實體機分布_機房"
               :cols="[{ k: 'room', label: '機房' }, { k: 'total', label: '台數', mono: true }, { k: 'cats', label: '分類' }]">
        <template #cell-total="{ row }"><button type="button" class="lb" @click="openDrill('room', row.room)">{{ row.total.toLocaleString() }}</button></template>
      </V2Table>
      <h3 class="h3">分公司與其他地點<InfoNote>分公司一間一間列；地點欄原值認不出的列「未歸類地點」、保留原值等人確認，不算進分公司；地點空白的另列「未填機房」。要歸組請改 location_groups.json。</InfoNote></h3>
      <V2Table :rows="placeRows" prefix="B" row-key="__no" unit="處" export-name="實體機分布_分公司與其他地點" empty="目前沒有分公司的實體機登記"
               :cols="[{ k: 'kind', label: '類別' }, { k: 'name', label: '據點／地點原值' }, { k: 'count', label: '台數', mono: true }]">
        <template #cell-count="{ row }"><button type="button" class="lb" @click="openDrill(row.k, row.v)">{{ row.count.toLocaleString() }}</button></template>
      </V2Table>
      <template v-if="data.excluded_models_total">
        <h3 class="h3">另排除 {{ data.excluded_models_total.toLocaleString() }} 台「非機房伺服器」（未列入以上台數）<InfoNote>這些機器的 OS 欄位看起來像伺服器（例如 TippingPoint 寫 Linux），但實際是 PC／筆電／入侵偵測設備，不計入機房實體機。清單維護在 report_groups.json。</InfoNote></h3>
        <!-- 排除的是視角不是資料：這句留在畫面上（使用者要求，稽核缺失常來自「每個人都以為別人在管」） -->
        <p class="warn">⚠ 排除的是視角，不是資料——這些機器仍會 EOS、要打補丁，本表不列入不代表沒人管</p>
        <V2Table :rows="exclRows" prefix="X" row-key="device_model" unit="種" export-name="實體機分布_已排除型號"
                 :cols="[{ k: 'device_model', label: '型號' }, { k: 'count', label: '台數', mono: true }]">
          <template #cell-count="{ row }"><button type="button" class="lb" @click="openDrill('excluded_model', row.device_model)">{{ row.count }}</button></template>
        </V2Table>
      </template>
      <template v-if="offRows.length">
        <h3 class="h3">另有 {{ sum(offRows, 'count').toLocaleString() }} 台不在 CIA 清單上（未列入以上台數）<InfoNote>實際存在、但不在資產清單上的機器（DYN-／VC-／AUTO- 開頭），刻意獨立一區、不併進上面任何數字；要看是哪幾台到 2.1 主機查詢或 9.1 待補資料。</InfoNote></h3>
        <V2Table :rows="offRows" prefix="O" row-key="src" unit="類" export-name="實體機分布_帳外"
                 :cols="[{ k: 'src', label: '來源', mono: true }, { k: 'label', label: '意思' }, { k: 'count', label: '台數', mono: true }]" />
      </template>
    </template>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin: 6px 0 10px; font-size: 13px; }
.h3 { margin: 14px 0 6px; font-size: 15px; }
.rooms { display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 8px; }
.room { flex: 1 1 220px; display: flex; flex-direction: column; gap: 6px; align-items: center; padding: 10px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); }
.lb { background: none; border: 0; color: var(--link); cursor: pointer; font-family: inherit; font-size: 13px; padding: 0; }
.btn2 { font-family: inherit; font-size: 13px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); cursor: pointer; }
.warn { color: var(--warn-text); font-size: 13px; margin: 0 0 6px; }
.muted { color: var(--muted); font-size: 12px; }
.err { color: var(--bad); }
</style>
