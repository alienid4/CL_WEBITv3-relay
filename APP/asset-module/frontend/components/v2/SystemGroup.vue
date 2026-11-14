<script setup lang="ts">
// 10.1「系統組報告」（V49，2.048.16 起 V2 原生；取代嵌入的舊頁 pages/reports/system-group.vue）。⚠D3。同一批 API，行為不變：
// GET /api/reports/system-group（整份即時算）、/drill（加總與清單同一份後端計算，格子數字＝點進去的筆數，verify_numbers 釘住）、
// PUT /note（備註，一般帳號以上，後端 USER_WRITE）、POST /snapshots（存為本月定稿，同月重存＝覆蓋，一般帳號以上）。這裡不另算台數。
// 小頁籤在網址 ?tab=group&gt=plat|os|esxi|ibm|phys；下鑽也在網址：gk=platform|cluster＋下面 DK 的條件（null 一律送空字串，跟舊頁同一種送法）。
const { apiFetch } = useApi()
const { showToast } = useToast()
const route = useRoute()
const { canWrite } = useAuth()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const has = (k: string) => route.query[k] !== undefined

const report = ref<any>(null)
const err = ref('')
const loading = ref(false)
const begin = useLatest()
// 重抓失敗時上一份報告照樣顯示（錯誤寫在上方）
async function load() {
  const isLatest = begin()
  loading.value = true
  try { const r = await apiFetch<any>('/api/reports/system-group'); if (!isLatest()) return; report.value = r; err.value = '' } catch (e: any) { if (isLatest()) err.value = `報告載入失敗：${errMsg(e)}` } finally { if (isLatest()) loading.value = false }
}
onMounted(load)

const GT = [{ k: 'plat', label: '平台數量與生命週期' }, { k: 'os', label: '作業系統明細' }, { k: 'esxi', label: 'ESXi 數量' }, { k: 'ibm', label: 'IBM／虛擬化環境' }, { k: 'phys', label: '實體主機' }]
const gt = computed(() => (GT.some(t => t.k === q('gt')) ? q('gt') : 'plat'))
// 下鑽條件 → 網址參數（短名，避免跟頁面其他參數撞名）
const DK: Record<string, string> = { platform: 'gp', status: 'gs', bucket: 'gb', retired: 'gr', os_canonical: 'go', registered_only: 'gro',
  location: 'gl', service: 'gsv', vcenter: 'gv', cluster: 'gc', version: 'gver' }
const u = (extra: Record<string, any> = {}) => ({ path: '/v2/report', query: { tab: 'group', ...(gt.value !== 'plat' ? { gt: gt.value } : {}), ...extra } })
function dl(kind: 'platform' | 'cluster', f: Record<string, any>) {
  const query: Record<string, string> = { gk: kind }
  for (const [k, v] of Object.entries(f)) query[DK[k]] = v === null || v === undefined ? '' : String(v)
  return u(query)
}

const STATUS = ['已EOS', '一年內EOS', '尚未公布', '需確認', '支援中'] as const
const pl = computed(() => report.value?.platform_lifecycle)
const notes = computed<Record<string, string>>(() => report.value?.notes ?? {})
const platRows = computed(() => (pl.value?.rows ?? []).map((r: any, i: number) => ({ ...r, __no: i + 1, note: notes.value[`platform:${r.platform}`] ?? '' })))
const statusTotals = computed(() => Object.fromEntries(STATUS.map(s => [s, (pl.value?.rows ?? []).reduce((a: number, r: any) => a + (r[s] || 0), 0)])) as Record<string, number>)
const riskTotal = computed(() => statusTotals.value['已EOS'] + statusTotals.value['一年內EOS'])
const grandTotal = computed(() => (pl.value?.total ?? 0) + (pl.value?.excluded_total ?? 0) + (pl.value?.retired_excluded ?? 0))
const CARDS = [{ s: '已EOS', label: '已 EOS', tone: 'bad' }, { s: '一年內EOS', label: '一年內 EOS', tone: 'warn' }, { s: '需確認', label: '需確認', tone: 'plain' }, { s: '支援中', label: '支援中', tone: 'good' }] as const
const platCols = computed(() => [
  { k: 'platform', label: '平台類別' },
  { k: 'total', label: '總量', mono: true, link: (r: any) => (r.total ? dl('platform', { platform: r.platform }) : null) },
  ...STATUS.map(s => ({ k: s, label: s, mono: true, link: (r: any) => (r[s] ? dl('platform', { platform: r.platform, status: s }) : null) })),
  { k: 'note', label: '備註說明' }])
const showExcluded = computed(() => q('gx') === '1')
const exclRows = computed(() => [
  ...(pl.value?.excluded ?? []).map((e: any, i: number) => ({ __no: i + 1, name: e.platform, count: e.count, f: { bucket: e.platform } })),
  { __no: (pl.value?.excluded ?? []).length + 1, name: '已退役', count: pl.value?.retired_excluded ?? 0, f: { retired: 'true' } }])

// 作業系統版本明細（只列已EOS／一年內）：預設前 10 名，其餘摺成一行講出來，可展開
const verRows = computed(() => (report.value?.os_version_breakdown ?? []).map((v: any, i: number) => ({ ...v, __no: i + 1, eos_t: v.eos_date || v.eos_status })))
const showAllVer = computed(() => q('gva') === '1')
const verShown = computed(() => (showAllVer.value ? verRows.value : verRows.value.slice(0, 10)))
const verHidden = computed(() => verRows.value.slice(10))

// ESXi：叢集分類（列）× 機房（欄）
const esxiLocs = computed<string[]>(() => (report.value?.cluster_summary?.locations ?? []).map((l: any) => l.location))
const esxiRows = computed(() => {
  const locs = report.value?.cluster_summary?.locations ?? []
  const svcs: string[] = []
  for (const l of locs) for (const r of l.rows) if (!svcs.includes(r.service)) svcs.push(r.service)
  return svcs.map((svc, i) => {
    const row: Record<string, any> = { __no: i + 1, service: svc, __total: 0 }
    for (const l of locs) { const n = l.rows.find((x: any) => x.service === svc)?.esxi_count ?? 0; row[`L:${l.location}`] = n; row.__total += n }
    return row
  })
})
const esxiColTotal = (loc: string) => (report.value?.cluster_summary?.locations ?? []).find((l: any) => l.location === loc)?.total ?? 0
const esxiGrand = computed(() => esxiLocs.value.reduce((a, l) => a + esxiColTotal(l), 0))
const esxiCols = computed(() => [{ k: 'service', label: '叢集分類＼機房' },
  ...esxiLocs.value.map(loc => ({ k: `L:${loc}`, label: loc, mono: true, link: (r: any) => (r[`L:${loc}`] ? dl('cluster', { location: loc, service: r.service }) : null) })),
  { k: '__total', label: '合計', mono: true, link: (r: any) => (r.__total ? dl('cluster', { service: r.service }) : null) }])

const clusterRows = computed(() => (report.value?.virtualization_env?.clusters ?? []).map((c: any, i: number) => ({ ...c, __no: i + 1,
  cluster_t: c.cluster || '（未歸叢集）', version_t: `${c.version || '—'}${c.mixed_version ? '（混版）' : ''}`, eos_t: c.eos_status ? `${c.eos_status}${c.eos_date ? ` ${c.eos_date}` : ''}` : '' })))
const clusterCols = [{ k: 'location', label: 'Location' }, { k: 'vcenter', label: 'vCenter IP', mono: true }, { k: 'environment', label: '環境' },
  { k: 'cluster_t', label: 'Cluster' }, { k: 'count', label: '臺數', mono: true, link: (c: any) => dl('cluster', { vcenter: c.vcenter, cluster: c.cluster, version: c.version }) },
  { k: 'version_t', label: '版本', mono: true }, { k: 'eos_t', label: 'EOS' }]
const hostRows = computed(() => (report.value?.virtualization_env?.physical_hosts ?? []).map((h: any, i: number) => ({ ...h, __no: i + 1,
  location_t: h.location || '未填', environment_t: h.environment || '未填', hostname_t: h.found ? (h.hostname || '（無主機名）') : '⚠ 查無登記', os_t: h.os_canonical || h.os_raw || '' })))
const hostCols = [{ k: 'location_t', label: 'Location' }, { k: 'ip', label: 'IP', mono: true }, { k: 'environment_t', label: '環境' }, { k: 'service', label: '服務' },
  { k: 'hostname_t', label: '主機名' }, { k: 'product', label: '作業系統' }, { k: 'os_t', label: '版本', mono: true }, { k: 'eos_status', label: 'EOS狀態' }, { k: 'eos_date', label: 'EOS日期', mono: true }]

// ===== 下鑽 =====
const drillQ = computed(() => {
  const kind = q('gk')
  if (kind !== 'platform' && kind !== 'cluster') return null
  const f: Record<string, string> = {}
  for (const [k, p] of Object.entries(DK)) if (has(p)) f[k] = q(p)
  return { kind, f }
})
const drillTitle = computed(() => {
  const d = drillQ.value
  if (!d) return ''
  const f = d.f
  if (d.kind === 'platform') {
    if (f.retired === 'true') return '已退役（不列入）'
    if (f.bucket !== undefined) return `不列入本報告：${f.bucket}`
    if (f.os_canonical !== undefined) return f.os_canonical
    if (f.platform !== undefined) return f.status ? `${f.platform} ／ ${f.status}` : `${f.platform}（全部）`
    return `全平台 ／ ${f.status ?? ''}`
  }
  if (f.location !== undefined) return `${f.location} ／ ${f.service ?? ''}`
  return `${f.cluster || '（未歸叢集）'} 的 ESXi（${f.version || '版本未知'}）`
})
const drillRows = ref<any[]>([])
const drillErr = ref('')
const drillLoading = ref(false)
const beginDrill = useLatest()
watch(drillQ, async (d) => {
  const isLatest = beginDrill()
  drillRows.value = []; drillErr.value = ''; drillLoading.value = false
  if (!d) return
  drillLoading.value = true
  try {
    const r = await apiFetch<any[]>('/api/reports/system-group/drill', { query: { table: d.kind, ...d.f } })
    if (!isLatest()) return
    drillRows.value = r.map((x: any, i: number) => ({ ...x, __no: i + 1, eos_t: x.eos_date || x.eos_status, os_raw_t: x.os_raw || '（空）', os_c_t: x.os_canonical || '認不出' }))
  } catch (e: any) { if (isLatest()) drillErr.value = `清單載入失敗：${errMsg(e)}` } finally { if (isLatest()) drillLoading.value = false }
}, { immediate: true })
const machine = (s: string | null) => (s ? `/v2/machine/${encodeURIComponent(s)}` : null)
const drillCols = computed(() => (drillQ.value?.kind === 'platform'
  ? [{ k: 'hostname', label: '主機名', text: (r: any) => r.hostname || r.asset_serial, link: (r: any) => machine(r.asset_serial) }, { k: 'ip', label: 'IP', mono: true },
     { k: 'os_raw_t', label: 'OS 原始值' }, { k: 'os_c_t', label: '正規化' }, { k: 'eos_t', label: 'EOS' }, { k: 'reason', label: '判定依據' }]
  : [{ k: 'host', label: 'ESXi', mono: true }, { k: 'cluster', label: 'Cluster' }, { k: 'vcenter', label: 'vCenter', mono: true }, { k: 'version', label: '版本' }, { k: 'reason', label: '判定依據' }]))
const backTo = computed(() => u())

// ===== 備註（可編輯，跨月保留；一般帳號以上）=====
const editingKey = ref<string | null>(null)
const editingText = ref('')
function startEdit(key: string) { editingKey.value = key; editingText.value = notes.value[key] ?? '' }
async function saveNote() {
  if (!editingKey.value) return
  const key = editingKey.value
  try {
    await apiFetch('/api/reports/system-group/note', { method: 'PUT', body: { row_key: key, note: editingText.value } })
    if (report.value) report.value.notes = { ...report.value.notes, [key]: editingText.value }
    editingKey.value = null
    showToast('備註已存，下個月開這頁還會在', 'success')
  } catch (e: any) { showToast(`備註存檔失敗：${errMsg(e)}`, 'error', 15000) }
}

// ===== 月份定稿 =====
// 本地時間的年月（toISOString 是 UTC，台灣每月 1 日 08:00 前會變成上個月）
const _now = new Date()
const period = ref(`${_now.getFullYear()}-${String(_now.getMonth() + 1).padStart(2, '0')}`)
const saving = ref(false)
async function saveSnapshot() {
  saving.value = true
  try {
    // 2.058.23（V2 決定 #10）：同月重存會蓋掉上一份定稿（給主管看的月底數字，蓋錯找不回來）——已有就先確認
    let prev: { period: string; created_by: string | null; created_at: string } | undefined
    try {
      prev = (await apiFetch<{ period: string; created_by: string | null; created_at: string }[]>('/api/reports/system-group/snapshots')).find(s => s.period === period.value)
    } catch (e: any) {
      if (!confirm(`查不到 ${period.value} 是否已有定稿（${errMsg(e)}）。\n\n仍要存？若已有，會被蓋掉。`)) return
    }
    if (prev && !confirm(`${period.value} 已有定稿（${prev.created_by || '—'}，${prev.created_at}）。\n\n要蓋掉嗎？蓋掉後舊的那份找不回來。`)) return
    await apiFetch('/api/reports/system-group/snapshots', { method: 'POST', body: { period: period.value } })
    showToast(`${period.value} 已存檔。之後數字再變，這份不會跟著變`, 'success', 8000)
  } catch (e: any) { showToast(`存檔失敗：${errMsg(e)}`, 'error', 15000) } finally { saving.value = false }
}
</script>

<template>
  <div>
    <template v-if="drillQ">
      <div class="bar"><NuxtLink :to="backTo">‹ 返回</NuxtLink><b>{{ drillTitle }}</b><span class="muted">{{ drillLoading ? '載入中…' : drillErr ? '—' : `${drillRows.length.toLocaleString()} 筆` }}</span></div>
      <p v-if="drillErr" class="err">{{ drillErr }}</p>
      <V2Table v-else :rows="drillRows" prefix="D" :row-key="drillQ.kind === 'platform' ? 'asset_serial' : 'host'" :max="200" :cols="drillCols"
               :export-name="`系統組報告_${drillTitle}`" :empty="drillLoading ? '載入中…' : '沒有符合的資料'" />
    </template>
    <template v-else>
      <div class="bar">
        <input v-model="period" type="month" class="mon" aria-label="定稿月份">
        <button v-if="canWrite" type="button" class="btn2" :disabled="saving" @click="saveSnapshot">{{ saving ? '存檔中…' : '存為本月定稿' }}</button>
        <InfoNote>存成那個月的定稿：之後數字再變，那份不會跟著變。同一個月重存會蓋掉上一份（會先問）。畫面上的數字一律是即時算的。</InfoNote>
        <button type="button" class="btn2" :disabled="loading" @click="load">{{ loading ? '載入中…' : '重新整理' }}</button>
      </div>
      <p v-if="err" class="err">{{ err }}</p>
      <p v-if="!report && !err" class="muted">載入中…</p>
      <template v-if="report">
        <div class="fresh" :class="{ warn: !report.meta.rvtools_exported_at }">{{ report.meta.rvtools_note }}　·　產生於 {{ report.meta.generated_at }}</div>
        <div class="chips">
          <NuxtLink v-for="t in GT" :key="t.k" class="chip" :class="{ on: gt === t.k }" :to="{ path: '/v2/report', query: { tab: 'group', ...(t.k !== 'plat' ? { gt: t.k } : {}) } }">{{ t.label }}</NuxtLink>
        </div>

        <template v-if="gt === 'plat'">
          <h3 class="h3">平台數量與生命週期
            <InfoNote><b>已 EOS</b>：官方支援已結束。<b>一年內 EOS</b>：一年內到期。<b>尚未公布</b>：查到此產品、但官方還沒公布 EOS 日期。
              <b>需確認</b>：EOS 對照表查無此產品，要人工確認。<b>支援中</b>：仍在支援期內。數字點下去列出是哪幾台、每台為什麼被分到這一類。</InfoNote></h3>
          <div class="cards">
            <NuxtLink v-for="c in CARDS" :key="c.s" class="scard" :class="c.tone" :to="dl('platform', { status: c.s, registered_only: 'true' })">
              <span class="sv">{{ statusTotals[c.s].toLocaleString() }}</span><span class="sl">{{ c.label }}</span>
            </NuxtLink>
            <div class="scard risk"><span class="sv">{{ riskTotal.toLocaleString() }}</span><span class="sl">應優先處理（已+一年內）</span></div>
          </div>
          <V2Table :rows="platRows" prefix="P" row-key="platform" unit="類" export-name="系統組報告_平台數量與生命週期" :cols="platCols">
            <template #cell-note="{ row }">
              <span v-if="editingKey === `platform:${row.platform}`" class="ned">
                <input v-model="editingText" class="ni" aria-label="備註" @keyup.enter="saveNote">
                <button type="button" class="lb" @click="saveNote">存</button><button type="button" class="lb" @click="editingKey = null">取消</button>
              </span>
              <button v-else-if="canWrite" type="button" class="note" :class="{ empty: !row.note }" title="加備註" @click="startEdit(`platform:${row.platform}`)">{{ row.note || '＋' }}</button>
              <span v-else>{{ row.note || '—' }}</span>
            </template>
          </V2Table>
          <p class="excsum">合計 <NuxtLink class="nb" :to="dl('platform', { registered_only: 'true' })">{{ pl.total.toLocaleString() }}</NuxtLink> 台・不列入本報告 <b>{{ pl.excluded_total.toLocaleString() }}</b> 台・已退役 <b>{{ pl.retired_excluded.toLocaleString() }}</b> 台・總計 <b>{{ grandTotal.toLocaleString() }}</b> 台
            <template v-if="pl.duplicates_merged">・已收起 <b>{{ pl.duplicates_merged.toLocaleString() }}</b> 筆重複登記
              <InfoNote>同一台機器被 CIA／dynassets／RVTools 各登記一筆時，這裡照主機算，跟納管漏斗同一個口徑。資料沒有被合併也沒有刪除，只是統計上收成一台。2026-09-17 以前這張表照筆數算，跟更早的報告比會偏高。</InfoNote></template>
            <NuxtLink :to="u(showExcluded ? {} : { gx: '1' })">{{ showExcluded ? '收合' : '看明細' }}</NuxtLink></p>
          <V2Table v-if="showExcluded" :rows="exclRows" prefix="X" row-key="name" unit="類" export-name="系統組報告_不列入本報告"
                   :cols="[{ k: 'name', label: '不列入本報告' }, { k: 'count', label: '臺數', mono: true, link: (r: any) => (r.count ? dl('platform', r.f) : null) }]" />
        </template>

        <template v-else-if="gt === 'os'">
          <h3 class="h3">作業系統版本明細（已EOS／一年內到期）</h3>
          <V2Table :rows="verShown" prefix="V" row-key="os_canonical" unit="個版本" export-name="系統組報告_作業系統版本明細" empty="無"
                   :cols="[{ k: 'os_canonical', label: '版本', mono: true }, { k: 'count', label: '臺數', mono: true, link: (r: any) => dl('platform', { os_canonical: r.os_canonical, registered_only: 'true' }) }, { k: 'eos_t', label: 'EOS日期', mono: true }]" />
          <p v-if="!showAllVer && verHidden.length" class="tip">還有 {{ verHidden.length }} 個版本未列出，共 {{ verHidden.reduce((a: number, v: any) => a + v.count, 0) }} 台——
            <NuxtLink :to="u({ gva: '1' })">顯示全部</NuxtLink></p>
          <p v-else-if="showAllVer && verRows.length > 10" class="tip"><NuxtLink :to="u()">收合回前10名</NuxtLink></p>
        </template>

        <template v-else-if="gt === 'esxi'">
          <h3 class="h3">叢集分類與 ESXi 數量<InfoNote>{{ report.cluster_summary.basis }}</InfoNote></h3>
          <p v-if="report.cluster_summary.unmapped_vcenters.length" class="warn">有 vCenter 對不到機房：{{ report.cluster_summary.unmapped_vcenters.join('、') }}
            <InfoNote>請在 report_groups.json 補上，否則這批 ESXi 會歸在「未對應」。</InfoNote></p>
          <V2Table :rows="esxiRows" prefix="E" row-key="service" unit="類" export-name="系統組報告_ESXi數量" :cols="esxiCols" />
          <p class="excsum">各機房合計：<span v-for="loc in esxiLocs" :key="loc">{{ loc }} <NuxtLink v-if="esxiColTotal(loc)" class="nb" :to="dl('cluster', { location: loc })">{{ esxiColTotal(loc) }}</NuxtLink><b v-else>0</b>・</span>總計 <b>{{ esxiGrand }}</b> 台</p>
        </template>

        <template v-else-if="gt === 'ibm'">
          <h3 class="h3">虛擬化與 AIX、IBM i 環境資訊<InfoNote>同一個叢集混跑兩個版本時拆成兩列，各自算自己的臺數與 EOS（版本欄標「混版」），點臺數就是剛好那幾台。</InfoNote></h3>
          <V2Table :rows="clusterRows" prefix="C" unit="列" export-name="系統組報告_虛擬化環境" :cols="clusterCols" />
        </template>

        <template v-else>
          <h3 class="h3">實體主機（AIX／IBM i）<InfoNote>固定名單（設定在系統裡，不進版控）；IP 在資產庫對不到時照樣列出，標「查無登記」。</InfoNote></h3>
          <V2Table :rows="hostRows" prefix="H" row-key="ip" unit="台" export-name="系統組報告_實體主機" empty="無" :cols="hostCols" />
        </template>
      </template>
    </template>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin: 6px 0 10px; font-size: 13px; }
.h3 { margin: 14px 0 6px; font-size: 15px; display: flex; align-items: center; gap: 6px; }
.mon { font-family: inherit; font-size: 13px; padding: 3px 8px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); }
.btn2 { font-family: inherit; font-size: 13px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.btn2:disabled { opacity: .45; cursor: default; }
.lb { font-family: inherit; font-size: 12px; padding: 2px 8px; margin-left: 4px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); cursor: pointer; }
.fresh { margin: 4px 0 8px; padding: 6px 12px; border-radius: 10px; font-size: 12px; background: var(--mint); color: var(--ink-soft); }
.fresh.warn { background: var(--warn-soft); color: var(--warn-text); }
.chips { display: flex; gap: 6px; flex-wrap: wrap; margin: 6px 0; }
.chip { font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); text-decoration: none; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.cards { display: flex; flex-wrap: wrap; gap: 10px; margin: 4px 0 10px; }
.scard { display: flex; flex-direction: column; gap: 2px; min-width: 116px; padding: 10px 14px; border-radius: var(--radius); border: 1px solid var(--border); background: var(--card); text-decoration: none; color: var(--ink); }
a.scard:hover { border-color: var(--brand); }
.scard .sv { font-family: var(--disp); font-size: 22px; font-weight: 700; }
.scard .sl { font-size: 12px; color: var(--muted); }
.scard.bad .sv { color: var(--bad); } .scard.warn .sv { color: var(--warn-text); } .scard.good .sv { color: var(--brand-dark); }
.scard.risk { background: var(--sub); border-style: dashed; } .scard.risk .sv { color: var(--bad); }
.excsum { margin: 10px 0 6px; font-size: 13px; color: var(--ink-soft); display: flex; gap: 4px; align-items: center; flex-wrap: wrap; }
.excsum b { color: var(--ink); font-family: var(--disp); }
.excsum a { color: var(--link); margin-left: 8px; }
.excsum a.nb { margin-left: 0; font-family: var(--disp); font-weight: 700; }
.tip { font-size: 12px; color: var(--ink-soft); }
.tip a { color: var(--link); }
.note { font-family: inherit; border: 0; background: none; cursor: pointer; color: var(--ink-soft); font-size: 12px; padding: 0; text-align: left; }
.note.empty { color: var(--border-strong); font-size: 14px; }
.ni { font-family: inherit; font-size: 12px; padding: 2px 6px; border-radius: 6px; border: 1px solid var(--border-strong); min-width: 180px; }
.warn { color: var(--warn-text); background: var(--warn-soft); border-radius: 10px; padding: 6px 12px; font-size: 13px; display: flex; gap: 6px; align-items: center; }
.muted { color: var(--muted); font-size: 12px; }
.err { color: var(--bad); }
</style>
