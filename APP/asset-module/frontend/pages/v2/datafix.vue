<script setup lang="ts">
// 9.1 待補資料（2.x 第三批）：依單位｜CIA 待更正｜實測不符｜聯絡資訊待補｜重複登記合併｜下線與報廢｜主機分類。
// 2026-10-28（找碴 C24／C28）：「空殼併回」改名「重複登記合併」；原 9.3 主機分類併進來當最後一個頁籤（/v2/classify 轉到這裡）。
// 合併舊 4-9 補登分派、系統校正清單、資料品質「實測與 CIA 不符」、CIA 待更正的各份匯出（資產欄位、機櫃位置
// ＝含機櫃盤點與位置重疊處理、CIA 補登）、聯絡資訊待補、掃描空殼併回，以及 2.1／主機履歷「標記下線／報廢」的清單。
// 每個數字都寫明單位：台＝同一台多筆登記只算一台；筆＝一筆登記或一筆待更正項目。
// 2.010.x：新增「資料品質」頁籤（舊版 /data-quality 整頁嵌入，含人員名稱合併；寫入動作沿用原畫面與 API）。
// 只讀與匯出；寫入動作只有「標記 CIA 已更正」（沿用既有 API，只記時間與人，不刪）。「重複登記合併」頁籤直接嵌入既有的預覽／併回／還原畫面（會改寫資產資料，2.007.58 起不用再跳舊版頁）。
definePageMeta({ layout: 'v2' })
const { isViewer } = useAuth()     // Q91：只能看的帳號不顯示寫入按鈕（後端另外擋，這裡只是別讓人白按）
const { apiFetch } = useApi()
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const route = useRoute()
const router = useRouter()
const apiBase = ((useRuntimeConfig().public as any).apiBase || '') as string
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')

const df = ref<any>(null); const cia = ref<any>(null); const pos = ref<any>(null); const nocia = ref<number | null>(null)
const mm = ref<any>(null); const ct = ref<any>(null); const sh = ref<any>(null); const cls = ref<any>(null)
// 主機分類（V46）：2.048.16 起 V2 原生（V2Classify），不再嵌入舊頁；設定分類後重抓頁籤數字
// 重複登記合併（V54）：2.048.10 起 V2 原生（V2ScanShells），不再嵌入舊頁；併回／還原後重抓頁籤數字
// 資料品質（V27）：2.048.18 起 V2 原生（V2DataQuality），不再嵌入舊頁
const IpRelations = defineAsyncComponent(() => import('~/components/v2/IpRelations.vue'))
const err = reactive<Record<string, string>>({})
async function load(k: string, force = false) {
  // 伺服器端產生頁面時，連續多個 await 會讓後面的請求失去 Nuxt 執行環境（[nuxt] instance unavailable）：
  // 同一個頁籤要的資料一律先全部發出、一次 await
  const jobs: Promise<void>[] = []
  const get = (url: string, query?: any) => apiFetch<any>(url, query ? { query } : undefined)
  if (k === 'units' && (force || !df.value)) jobs.push(get('/api/data-fix').then(v => { df.value = v }))
  if (k === 'classify' && (force || !cls.value)) jobs.push(get('/api/classify/summary').then(v => { cls.value = v }))
  if ((k === 'cia' || k === 'offline') && (force || !cia.value)) jobs.push(get('/api/cia-pending', { include_synced: true }).then(v => { cia.value = v }))
  if (k === 'cia' && (force || !pos.value)) {
    jobs.push(get('/api/racks/position/pending-cia').then(v => { pos.value = v }))
    jobs.push(get('/api/v2/hosts', { tab: 'no_cia' }).then(h => { nocia.value = h.tabs.find((t: any) => t.key === 'no_cia')?.count ?? null }))
  }
  if (k === 'mismatch' && (force || !mm.value)) jobs.push(get('/api/measured-mismatch').then(v => { mm.value = v }))
  if (k === 'contacts' && (force || !ct.value)) jobs.push(get('/api/contacts/todo').then(v => { ct.value = v }))
  if (k === 'shells' && (force || !sh.value)) jobs.push(get('/api/scan-shells').then(v => { sh.value = v }))
  try { await Promise.all(jobs); err[k] = '' } catch (e: any) { err[k] = `載入失敗：${errMsg(e)}` }
}

// ===== 依單位 =====
const itemLabel = computed<Record<string, string>>(() => Object.fromEntries((df.value?.items ?? []).map((i: any) => [i.key, i.label])))
const byKey = computed<Record<string, any>>(() => Object.fromEntries((df.value?.machines ?? []).map((m: any) => [m.key, m])))
const unitRows = computed(() => (df.value?.units ?? []).map((u: any) => {
  const ms = u.machines.map((k: string) => byKey.value[k]).filter(Boolean)
  return { unit: u.key, n: ms.length, missing: ms.filter((m: any) => m.missing.length).length, items: ms.reduce((a: number, m: any) => a + m.missing.length, 0) }
}))
const itemRows = computed(() => Object.entries(df.value?.missing_by_item ?? {}).map(([k, n]) => ({ key: k, label: itemLabel.value[k] ?? k, n })))
const drill = computed(() => {
  if (!df.value) return null
  if (q('unit')) {
    const u = df.value.units.find((x: any) => x.key === q('unit'))
    const ms = (u?.machines ?? []).map((k: string) => byKey.value[k]).filter((m: any) => m && (q('all') || m.missing.length))
    return { label: `${q('unit')}${q('all') ? '' : ' 有缺漏'}`, rows: ms }
  }
  if (q('item')) return { label: `缺：${itemLabel.value[q('item')] ?? q('item')}`, rows: df.value.machines.filter((m: any) => m.missing.includes(q('item'))) }
  if (q('sys')) {
    // V64：依系統下鑽（同一份 /api/data-fix 的 systems 分組；一台屬多個系統時各系統都算）
    const s = (df.value.systems ?? []).find((x: any) => x.key === q('sys'))
    const ms = (s?.machines ?? []).map((k: string) => byKey.value[k]).filter((m: any) => m && (q('all') || m.missing.length))
    return { label: `${q('sys')}${q('all') ? '' : ' 有缺漏'}`, rows: ms }
  }
  return null
})
// ===== V64（2.043.10）：舊版 /data-fix 的「依系統」分組、每週分數趨勢、一鍵匯出這個單位 =====
const sysRows = computed(() => (df.value?.systems ?? []).map((s: any) => ({ sys: s.key, n: s.machine_count, missing: s.missing_machines, items: s.missing_total, score: s.score })))
const unitsBy = computed(() => (q('ub') === 'sys' ? 'sys' : 'unit'))
const { exportTable: dfExport } = useTableExport()
// 單位列上的「匯出」＝帶 ?export=1 進那個單位的有缺漏清單，進去就自動匯出一次（不用先點進去再按匯出）
watch(() => [q('export'), drill.value?.label], () => {
  if (q('export') !== '1' || !drill.value || isViewer.value) return
  const rows = drillRows.value
  dfExport(`補登_${drill.value.label}`, ['項次', '主機名稱', 'IP', '使用單位', '機房', '業務系統', '缺漏項目', '完整度 %'],
    rows.map((m: any, i: number) => [`V${i + 1}`, m.name, m.ip ?? '', m.unit ?? '', m.room ?? '', m.systems ?? '', m.missing_text ?? '', m.score ?? '']))
  router.replace({ query: { ...route.query, export: undefined } })
})   // 不用 immediate：依單位的資料只在瀏覽器載入（CLIENT_ONLY），載完 drill 才有值、這時才觸發
const trend = computed(() => (df.value?.trend ?? []) as { week: string; score: number }[])
const trendPts = computed(() => {
  const t = trend.value
  if (t.length < 2) return null
  const w = 320, h = 70, pad = 6
  const xs = (i: number) => pad + (i * (w - 2 * pad)) / (t.length - 1)
  const lo = Math.min(...t.map(x => x.score)), hi = Math.max(...t.map(x => x.score))
  const span = Math.max(hi - lo, 1)
  const ys = (v: number) => h - pad - ((v - lo) * (h - 2 * pad)) / span
  return { w, h, d: t.map((x, i) => `${i ? 'L' : 'M'}${xs(i).toFixed(1)},${ys(x.score).toFixed(1)}`).join(' '),
    dots: t.map((x, i) => ({ x: xs(i), y: ys(x.score), t: `${x.week}　${x.score}%` })) }
})
const drillRows = computed(() => (drill.value?.rows ?? []).map((m: any) => ({ ...m, name: m.hostname || m.serial })))

// ===== CIA 待更正 =====
const ciaOpen = computed(() => (cia.value?.items ?? []).filter((r: any) => !r.synced_at))
const ciaRows = computed(() => ciaOpen.value.map((r: any) => ({ ...r, name: r.hostname || r.asset_serial, chg: `${r.old_value ?? '（空）'} → ${r.new_value ?? '（空）'}`, at: (r.changed_at || '').slice(0, 16) })))
const posRows = computed(() => (pos.value?.rows ?? []).map((r: any) => ({ serial: r['資產序號'], name: r['主機名稱'], kind: r['更正類型'], from: `${r['原機房'] ?? ''} ${r['原Rack'] ?? ''} ${r['原U位'] ?? ''}`.trim(), to: `${r['新機房'] ?? ''} ${r['新Rack'] ?? ''} ${r['新U位'] ?? ''}`.trim(), by: r['盤點人'] || '', on: r['現場盤點日期'] || '' })))
const ciaPicked = ref<Record<string, boolean>>({})
const ciaPickedIds = computed(() => Object.keys(ciaPicked.value).filter(k => ciaPicked.value[k]).map(Number))
async function markSynced() {
  if (!ciaPickedIds.value.length || !window.confirm(`將選取的 ${ciaPickedIds.value.length} 筆標記為「CIA 已更正」？（只記錄時間與人，不刪除）`)) return
  try {
    const r = await guardedFetch<any>('/api/cia-pending/synced', { method: 'POST', body: { ids: ciaPickedIds.value } })
    showToast(`已標記 ${r.marked} 筆`, 'success'); ciaPicked.value = {}; await load('cia', true)
  } catch (e: any) { showToast(`標記失敗：${errMsg(e)}`, 'error') }
}

// ===== 實測不符 =====
const mmKind = computed(() => (q('mk') === 'suspect' ? 'suspect' : q('mk') === 'history' ? 'history' : 'corrected'))
// N1（2.043.6）：舊系統校正清單的「欄位」chips（?mf=）、「需人處理」（?mn=1）、對照／需誰處理欄、要被改的原值標色，都搬過來
const mmAll = computed(() => (mm.value?.[mmKind.value] ?? []) as any[])
const mmField = computed(() => q('mf'))
const mmNeeds = computed(() => q('mn') === '1')
const mmRows = computed(() => mmAll.value.filter((r: any) => (!mmField.value || r.field_key === mmField.value) && (!mmNeeds.value || !!r.needs))
  .map((r: any) => ({ ...r, name: r.hostname || r.asset_serial, at2: (r.at || '').slice(0, 16), detail: r.note || '', vs: r.against || 'CIA', who: r.needs || '—' })))
const mmFieldCount = (k: string) => mmAll.value.filter((r: any) => r.field_key === k).length
const mmNeedCount = computed(() => mmAll.value.filter((r: any) => r.needs).length)
const mmQ = (o: { mf?: string; mn?: boolean }) => u('mismatch', `${mmKind.value !== 'corrected' ? '&mk=' + mmKind.value : ''}${o.mf ? '&mf=' + encodeURIComponent(o.mf) : ''}${o.mn ? '&mn=1' : ''}`)
const mmHosts = (k: string) => new Set((mm.value?.[k] ?? []).map((r: any) => r.asset_serial)).size

// ===== 聯絡資訊待補 =====
const ctBy = computed(() => (q('cb') === 'model' ? 'model' : 'system'))
const ctRows = computed(() => (ctBy.value === 'model' ? ct.value?.models : ct.value?.systems)?.map((r: any) => ({ ...r, key: r.api_id ?? r.model_key, label: r.api_id ? `${r.api_id} ${r.name ?? ''}`.trim() : r.model })) ?? [])
const ctDrill = computed(() => {
  if (!q('ck') || !ct.value) return null
  const src = ctBy.value === 'model' ? ct.value.models : ct.value.systems
  const g = src.find((r: any) => (r.api_id ?? r.model_key) === q('ck'))
  return g ? { label: g.api_id ? `${g.api_id} ${g.name ?? ''}` : g.model, rows: g.machines.map((m: any) => ({ ...m, name: m.hostname || m.serial })) } : null
})

// ===== 下線與報廢 =====
const offRows = computed(() => (cia.value?.items ?? []).filter((r: any) => r.field === 'asset_status').map((r: any) => ({ ...r, name: r.hostname || r.asset_serial, chg: `${r.old_value ?? '（空）'} → ${r.new_value ?? '（空）'}`, at: (r.changed_at || '').slice(0, 16), synced: r.synced_at ? `已更正 ${r.synced_at.slice(0, 10)}` : '待 CIA 更正' })))

const TABS = computed(() => [
  { k: 'units', label: '依單位' },
  { k: 'cia', label: `CIA 待更正${cia.value && pos.value ? ` ${ciaOpen.value.length + posRows.value.length}` : ''}` },
  { k: 'mismatch', label: `實測不符${mm.value ? ` ${mm.value.corrected.length + mm.value.suspect.length}` : ''}` },
  { k: 'contacts', label: `聯絡資訊待補${ct.value ? ` ${ct.value.missing_count}` : ''}` },
  { k: 'shells', label: `重複登記合併${sh.value ? ` ${sh.value.counts.mergeable + sh.value.counts.review}` : ''}` },
  { k: 'offline', label: `下線與報廢${cia.value ? ` ${offRows.value.length}` : ''}` },
  { k: 'classify', label: `主機分類${cls.value ? ` 未分類 ${cls.value.unclassified}` : ''}` },
  { k: 'ipshare', label: '共用 IP／VIP' },
  { k: 'quality', label: '資料品質' }])
const tab = computed({ get: () => (TABS.value.some(t => t.k === q('tab')) ? q('tab') : 'units'), set: (v: string) => router.replace({ query: { tab: v } }) })
// 依單位（約 3 千台的缺漏明細）與空殼併回（約 2 千筆）資料量大，只在瀏覽器載入
const CLIENT_ONLY = ['units', 'shells']
if (!CLIENT_ONLY.includes(tab.value)) await load(tab.value)
onMounted(() => { if (CLIENT_ONLY.includes(tab.value)) load(tab.value) })
watch(tab, (t) => load(t))
const u = (t: string, extra = '') => `/v2/datafix?tab=${t}${extra}`
// 必填項目設定（2.007.54，舊版「補登分派 → 應填項目」翻新；管理員，後端另外擋）
const reqOpen = ref(false)
const reqBusy = ref(false)
const reqDraft = ref<string[]>([])
function openReq() { reqDraft.value = (df.value?.items ?? []).filter((i: any) => i.required).map((i: any) => i.key); reqOpen.value = !reqOpen.value }
async function saveReq() {
  reqBusy.value = true
  try {
    await guardedFetch('/api/data-fix/required', { method: 'PUT', body: { items: reqDraft.value } })
    showToast('必填項目已儲存', 'success')
    reqOpen.value = false
    df.value = await apiFetch<any>('/api/data-fix')
  } catch (e: any) { showToast(`儲存失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error') } finally { reqBusy.value = false }
}
</script>

<template>
  <V2Page v-model="tab" :tabs="TABS" width="wide">
    <template #title><h1 class="h1">9.1 待補資料</h1></template>
    <template #conclusion>
      <InfoNote>台＝同一台多筆登記只算一台；筆＝一筆登記或一筆待更正項目。</InfoNote>
    </template>
    <template #tab>
      <p v-if="err[tab]" class="err">{{ err[tab] }}</p>

      <!-- 依單位 -->
      <template v-if="tab === 'units' && df">
        <template v-if="drill">
          <div class="bar"><NuxtLink :to="u('units')">‹ 返回</NuxtLink><b>{{ drill.label }}</b><span class="muted">{{ drillRows.length }} 台</span></div>
          <V2Table :rows="drillRows" prefix="V" :max="5000" :cols="[{ k: 'name', label: '主機名稱', link: (r: any) => `/v2/machine/${encodeURIComponent(r.key)}` }, { k: 'ip', label: 'IP', mono: true }, { k: 'unit', label: '使用單位' }, { k: 'room', label: '機房' }, { k: 'systems', label: '業務系統' }, { k: 'missing_text', label: '缺漏項目' }, { k: 'score', label: '完整度 %', mono: true }]" />
        </template>
        <template v-else>
          <dl class="kv">
            <dt>必填完整度</dt><dd><b>{{ df.score }}%</b>（已填 {{ df.filled_total }}／應填 {{ df.required_total }} 項）</dd>
            <dt>有缺漏</dt><dd>{{ df.missing_machines }} 台／共 {{ df.machine_count }} 台</dd>
          </dl>
          <form v-if="reqOpen" class="reqform" @submit.prevent="saveReq">
            <b>每台要檢查哪些項目（勾選＝必填）</b>
            <label v-for="i in df.items" :key="i.key" class="rq"><input v-model="reqDraft" type="checkbox" :value="i.key"> {{ i.label }}<span v-if="i.physical_only" class="muted">（只看實體機）</span></label>
            <span class="rqbtn"><button type="submit" class="btn2 on" :disabled="reqBusy">{{ reqBusy ? '儲存中…' : '儲存' }}</button> <button type="button" class="btn2" @click="reqOpen = false">取消</button></span>
          </form>
          <div class="bar"><button v-if="df.can_edit && !isViewer" type="button" class="btn2" @click="openReq">設定必填項目</button><InfoNote>依使用單位分派補登：每台檢查必填項目（機房、機櫃 U、負責人、保管人、業務系統、型號、作業系統、環境別、存在可信度），缺一項就列入。必填項目按「設定必填項目」調整。</InfoNote></div>
          <div class="bar trend">
            <template v-if="trendPts">
              <svg :viewBox="`0 0 ${trendPts.w} ${trendPts.h}`" :width="trendPts.w" :height="trendPts.h" role="img" aria-label="每週分數趨勢">
                <path :d="trendPts.d" fill="none" stroke="var(--chart-1)" stroke-width="2" />
                <circle v-for="(p, i) in trendPts.dots" :key="i" :cx="p.x" :cy="p.y" r="3" fill="var(--chart-1)"><title>{{ p.t }}</title></circle>
              </svg>
              <span class="muted">每週分數（{{ trend.length }} 週）</span>
            </template>
            <span v-else class="muted">每週分數：{{ trend.length }} 週</span>
            <InfoNote>每週（ISO 週）第一次打開這頁時記一次必填完整度，滿兩週畫趨勢線；滑到點上看那一週的分數。</InfoNote>
          </div>
          <div class="bar">
            <NuxtLink class="chip" :class="{ on: unitsBy === 'unit' }" :to="u('units')">依使用單位 {{ unitRows.length }}</NuxtLink>
            <NuxtLink class="chip" :class="{ on: unitsBy === 'sys' }" :to="u('units', '&ub=sys')">依系統 {{ sysRows.length }}</NuxtLink>
          </div>
          <V2Table v-if="unitsBy === 'unit'" :rows="unitRows" prefix="U" :cols="[{ k: 'unit', label: '使用單位' }, { k: 'n', label: '台數', mono: true, link: (r: any) => u('units', `&unit=${encodeURIComponent(r.unit)}&all=1`) }, { k: 'missing', label: '有缺漏（台）', mono: true, link: (r: any) => u('units', `&unit=${encodeURIComponent(r.unit)}`) }, { k: 'items', label: '缺漏項目（項）', mono: true }, ...(isViewer ? [] : [{ k: 'exp', label: '匯出這個單位', text: (r: any) => (r.missing ? '匯出' : '—'), link: (r: any) => (r.missing ? u('units', `&unit=${encodeURIComponent(r.unit)}&export=1`) : null), note: '一鍵匯出這個單位有缺漏的主機（Excel），直接交給該單位補登。' }])]" />
          <V2Table v-else :rows="sysRows" prefix="S" :cols="[{ k: 'sys', label: '業務系統（APID）', mono: true }, { k: 'n', label: '台數', mono: true, link: (r: any) => u('units', `&sys=${encodeURIComponent(r.sys)}&all=1`), note: '一台屬多個系統時，每個系統都算一次，所以各系統台數加總可能大於總台數。' }, { k: 'missing', label: '有缺漏（台）', mono: true, link: (r: any) => u('units', `&sys=${encodeURIComponent(r.sys)}`) }, { k: 'items', label: '缺漏項目（項）', mono: true }, { k: 'score', label: '完整度 %', mono: true }, ...(isViewer ? [] : [{ k: 'exp', label: '匯出這個系統', text: (r: any) => (r.missing ? '匯出' : '—'), link: (r: any) => (r.missing ? u('units', `&ub=sys&sys=${encodeURIComponent(r.sys)}&export=1`) : null), note: '一鍵匯出這個系統有缺漏的主機（Excel），直接交給系統負責人補登。' }])]" />
          <h3 class="h3">依缺漏項目</h3>
          <V2Table :rows="itemRows" prefix="I" :cols="[{ k: 'label', label: '項目' }, { k: 'n', label: '缺漏（台）', mono: true, link: (r: any) => (r.n ? u('units', `&item=${r.key}`) : null) }]" />
        </template>
      </template>

      <!-- CIA 待更正 -->
      <template v-else-if="tab === 'cia' && cia && pos">
        <dl class="kv">
          <dt>資產欄位異動</dt><dd>{{ ciaOpen.length }} 筆 <a class="lnk" :href="`${apiBase}/api/cia-pending/export`">匯出</a></dd>
          <dt>機櫃位置更正</dt><dd>{{ posRows.length }} 筆 <a class="lnk" :href="`${apiBase}/api/racks/position/pending-cia.xlsx`">匯出</a></dd>
          <dt>CIA 補登</dt><dd>{{ nocia ?? '—' }} 台 <a class="lnk" :href="`${apiBase}/api/existence/cia-pending/export`">匯出</a>　<NuxtLink class="lnk" to="/v2/hosts?tab=no_cia">清單（2.1 未登錄 CIA）</NuxtLink>　<NuxtLink class="lnk" to="/v2/ciareg">分給負責人補（9.4）</NuxtLink></dd>
          <dt>實測不符</dt><dd><NuxtLink class="lnk" :to="u('mismatch')">實測不符頁籤</NuxtLink>　<a class="lnk" :href="`${apiBase}/api/measured-mismatch/cia-report`">匯出 CIA 回報表</a></dd>
        </dl>
        <InfoNote>資產欄位異動含 2.1／主機履歷的「標記下線／報廢」；機櫃位置更正含 5.1 位置更正、位置重疊處理與 7.1 機櫃盤點的差異處理（一筆＝一台設備的一次更正）；CIA 補登＝計入台數但 CIA 未登記的主機（與 2.1「未登錄 CIA」同一個數字）。CIA 窗口改好後在下表勾選「標記 CIA 已更正」。</InfoNote>
        <h3 class="h3">資產欄位異動 {{ ciaRows.length }} 筆</h3>
        <div class="bar"><button v-if="!isViewer" type="button" class="btn" :disabled="!ciaPickedIds.length" @click="markSynced">標記 CIA 已更正（{{ ciaPickedIds.length }}）</button></div>
        <V2Table v-model:selected="ciaPicked" :rows="ciaRows" prefix="A" row-key="id" selectable empty="沒有待更正"
                 :cols="[{ k: 'name', label: '主機名稱', link: (r: any) => `/v2/machine/${encodeURIComponent(r.asset_serial)}` }, { k: 'asset_serial', label: '資產編號', mono: true }, { k: 'field_label', label: '欄位' }, { k: 'chg', label: '異動' }, { k: 'reason', label: '原因' }, { k: 'changed_by', label: '異動人' }, { k: 'at', label: '時間', mono: true }]" />
        <h3 class="h3">機櫃位置更正 {{ posRows.length }} 筆</h3>
        <V2Table :rows="posRows" prefix="B" :max="3000" empty="沒有待更正"
                 :cols="[{ k: 'name', label: '主機名稱', link: (r: any) => (r.serial ? `/v2/machine/${encodeURIComponent(r.serial)}` : null) }, { k: 'serial', label: '資產編號', mono: true }, { k: 'kind', label: '更正類型' }, { k: 'from', label: '原位置' }, { k: 'to', label: '新位置' }, { k: 'by', label: '盤點人' }, { k: 'on', label: '盤點日期', mono: true }]" />
      </template>

      <!-- 實測不符 -->
      <template v-else-if="tab === 'mismatch' && mm">
        <div class="bar">
          <NuxtLink class="chip" :class="{ on: mmKind === 'corrected' }" :to="u('mismatch')">已系統校正 {{ mm.corrected.length }} 筆（{{ mmHosts('corrected') }} 台）</NuxtLink>
          <NuxtLink class="chip" :class="{ on: mmKind === 'suspect' }" :to="u('mismatch', '&mk=suspect')">疑似不符 {{ mm.suspect.length }} 筆（{{ mmHosts('suspect') }} 台）</NuxtLink>
          <NuxtLink class="chip" :class="{ on: mmKind === 'history' }" :to="u('mismatch', '&mk=history')">已不適用 {{ mm.history.length }} 筆</NuxtLink>
          <a class="btn2" :href="`${apiBase}/api/measured-mismatch/cia-report`">匯出 CIA 回報表</a>
          <InfoNote>已納管主機收集到的（或 RVTools 回報的）值與 CIA 登記不同。已系統校正：畫面與分類已用實測值，CIA 登記值未改，交 CIA 窗口更正。疑似不符：只有存活掃描的證據，不自動校正。一筆＝一台的一個欄位。</InfoNote>
        </div>
        <div class="bar">
          <span>欄位</span>
          <NuxtLink class="chip" :class="{ on: !mmField && !mmNeeds }" :to="mmQ({})">全部 {{ mmAll.length }}</NuxtLink>
          <NuxtLink class="chip" :class="{ on: mmNeeds }" :to="mmQ({ mf: mmField, mn: !mmNeeds })">需人處理 {{ mmNeedCount }}</NuxtLink>
          <NuxtLink v-for="f in (mm.fields ?? []).filter((x: any) => mmFieldCount(x.key))" :key="f.key" class="chip" :class="{ on: mmField === f.key }" :to="mmQ({ mf: mmField === f.key ? '' : f.key, mn: mmNeeds })">{{ f.label }} {{ mmFieldCount(f.key) }}</NuxtLink>
          <InfoNote>「對照」寫 RVTools 的是 vCenter 那邊舊，不是 CIA 要改；標色的原值＝要被改掉的值（需誰處理有寫的那幾筆）。</InfoNote>
        </div>
        <V2Table :rows="mmRows" prefix="X" :max="3000" empty="沒有資料"
                 :cols="[{ k: 'name', label: '主機名稱', link: (r: any) => `/v2/machine/${encodeURIComponent(r.asset_serial)}` }, { k: 'field', label: '欄位' }, { k: 'vs', label: '對照' }, { k: 'cia', label: '原值', tone: (r: any) => (r.needs && r.cia ? 'prod' : '') }, { k: 'measured', label: mmKind === 'suspect' ? '實測值' : '校正值' }, { k: 'source', label: '依據' }, { k: 'confidence', label: '信心' }, { k: 'action', label: '要修改什麼' }, { k: 'who', label: '需誰處理' }, { k: 'detail', label: '說明' }, { k: 'at2', label: mmKind === 'history' ? '關閉時間' : '最後確認', mono: true }]" />
      </template>

      <!-- 聯絡資訊待補 -->
      <template v-else-if="tab === 'contacts' && ct">
        <template v-if="ctDrill">
          <div class="bar"><NuxtLink :to="u('contacts', `&cb=${ctBy}`)">‹ 返回</NuxtLink><b>{{ ctDrill.label }}</b><span class="muted">{{ ctDrill.rows.length }} 台</span></div>
          <V2Table :rows="ctDrill.rows" prefix="V" :max="3000" :cols="[{ k: 'name', label: '主機名稱', link: (r: any) => `/v2/machine/${encodeURIComponent(r.key)}` }, { k: 'ip', label: 'IP', mono: true }, { k: 'os', label: '作業系統' }, { k: 'room', label: '機房' }, { k: 'model', label: '型號' }, { k: 'env', label: '環境' }, { k: 'systems', label: '業務系統' }]" />
        </template>
        <template v-else>
          <div class="bar">
            <span>待補 <b>{{ ct.missing_count }}</b> 台／帳內現役 {{ ct.in_book_total }} 台</span>
            <NuxtLink class="chip" :class="{ on: ctBy === 'system' }" :to="u('contacts')">依業務系統 {{ ct.systems.length }}</NuxtLink>
            <NuxtLink class="chip" :class="{ on: ctBy === 'model' }" :to="u('contacts', '&cb=model')">依型號（實體機 {{ ct.physical_missing }} 台）{{ ct.models.length }}</NuxtLink>
            <NuxtLink class="btn2" to="/v2/settings?st=contacts">填寫聯絡資訊（⚙ 系統設定）</NuxtLink>
            <InfoNote>沒有任何聯絡或合約資料（廠商、維護電話、合約迄日都沒有）的帳內現役主機。依業務系統：一台掛多個系統時各系統都算一次，所以各系統加總會大於待補台數；依型號：只算實體機，各型號加總＝實體機待補台數。</InfoNote>
          </div>
          <V2Table :rows="ctRows" prefix="C" :max="2000" :cols="[{ k: 'label', label: ctBy === 'model' ? '型號' : '業務系統' }, { k: 'count', label: '待補（台）', mono: true, link: (r: any) => u('contacts', `&cb=${ctBy}&ck=${encodeURIComponent(r.key)}`) }]" />
        </template>
      </template>

      <!-- 重複登記合併（原「空殼併回」）：預覽、勾選、確認、併回、還原整套直接嵌入既有畫面（2.007.58，功能與 API 不變；邏輯全在後端 dyn_shell） -->
      <V2ScanShells v-else-if="tab === 'shells'" @changed="load('shells', true)" />

      <!-- 共用 IP／VIP（嵌入共用元件） -->
      <ClientOnly v-else-if="tab === 'ipshare'"><V2Embed><IpRelations /></V2Embed></ClientOnly>

      <ClientOnly v-else-if="tab === 'quality'"><V2DataQuality /></ClientOnly>

      <!-- 下線與報廢 -->
      <template v-else-if="tab === 'offline' && cia">
        <div class="bar">
          <span>{{ offRows.length }} 筆（待 CIA 更正 {{ offRows.filter((r: any) => !r.synced_at).length }} 筆）</span>
          <NuxtLink class="btn2" to="/v2/hosts">到 2.1 主機查詢標記</NuxtLink>
          <InfoNote>在 2.1 主機查詢（批次）或主機履歷按「標記下線／報廢」的紀錄。標記不直接改台數，記一筆 CIA 待異動；CIA 窗口更正後到「CIA 待更正」頁籤標記已更正。</InfoNote>
        </div>
        <V2Table :rows="offRows" prefix="O" empty="沒有下線或報廢紀錄"
                 :cols="[{ k: 'name', label: '主機名稱', link: (r: any) => `/v2/machine/${encodeURIComponent(r.asset_serial)}` }, { k: 'asset_serial', label: '資產編號', mono: true }, { k: 'chg', label: '狀態異動' }, { k: 'reason', label: '原因' }, { k: 'changed_by', label: '標記人' }, { k: 'at', label: '時間', mono: true }, { k: 'synced', label: 'CIA' }]" />
      </template>

      <!-- 主機分類（原 9.3，2026-10-28 併入；2.048.16 起 V2 原生 V2Classify，同一批 API） -->
      <template v-else-if="tab === 'classify'">
        <div v-if="cls" class="bar">已分類 <b>{{ cls.classified }}</b>／{{ cls.total }} 筆（{{ cls.percent }}%），未分類 {{ cls.unclassified }} 筆
          <InfoNote>一筆＝一筆資產登記。此分類只用於報表（核心交易／非核心／測試三類與分色），與 CIA 資訊資產清冊無關。</InfoNote></div>
        <div class="bar" data-testid="classify-scope-note">母體說明
          <InfoNote>此頁母體是報表基準（report_baseline）：排除退役、帳外資產與被排除的平台，並以機器鍵去重；筆數口徑與 2.1 主機查詢（對帳橋口徑）刻意不同，兩邊總數不必相等。</InfoNote></div>
        <ClientOnly>
          <V2Classify @changed="load('classify', true)" />
          <template #fallback><p class="muted">載入中…</p></template>
        </ClientOnly>
      </template>
      <p v-else-if="!err[tab]" class="muted">載入中…</p>
    </template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.reqform { display: flex; gap: 6px 16px; flex-wrap: wrap; align-items: center; margin: 4px 0 10px; padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px; background: var(--sub); font-size: 13px; }
.rq { display: inline-flex; gap: 4px; align-items: center; color: var(--ink-soft); }
.rqbtn { margin-left: auto; }
.btn2.on { background: var(--brand); border-color: var(--brand); color: var(--type-on); }
.h3 { margin: 16px 0 6px; font-size: 15px; }
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
.chip { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); text-decoration: none; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.btn { border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); border-radius: 8px; padding: 5px 12px; font-size: 13px; font-family: inherit; cursor: pointer; }
.btn:disabled { opacity: .45; cursor: default; }
.btn2 { border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); border-radius: 8px; padding: 4px 12px; font-size: 13px; text-decoration: none; }
.kv { display: grid; grid-template-columns: 120px minmax(0, 1fr); gap: 6px 16px; margin: 0 0 8px; font-size: 14px; }
.kv dt { color: var(--ink-aux); } .kv dd { margin: 0; }
.lnk { color: var(--link); margin-left: 6px; }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
</style>
