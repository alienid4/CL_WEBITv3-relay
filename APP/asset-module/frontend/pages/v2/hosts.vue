<script setup lang="ts">
// 2.1 主機查詢（2.x 藍圖）：合併舊 2-2 資產查詢（逐筆）、2-3 主機清單（一台一列）、2-4 掃描結果、8-7 來源對照。
// 預設一台一列，「顯示每筆登記」切換成逐筆。頁籤：全部登記台數｜實體設備｜虛擬機｜管理口｜核心系統｜逾期未偵測｜未偵測到（僅 CIA 登記）｜未登錄 CIA｜已納管。
// 實體設備／虛擬機／管理口的單位與數字＝對帳橋試算（跟 1.1、9.2 同一份，2.003.1），一列＝對帳橋算一台的那個單位。
// 批次作業：批次納管（沿用 BatchOnboardModal，每台走 /api/onboard）、標記下線／報廢（沿用 OfflineMarkModal，原因必填、
// 記 CIA 待異動）、加入盤點（以所選主機所在機櫃開始一次機櫃盤點）、匯出。資料：/api/v2/hosts（v2_hosts.py）。
definePageMeta({ layout: 'v2' })
const { isViewer } = useAuth()     // Q91：只能看的帳號不顯示寫入按鈕（後端另外擋，這裡只是別讓人白按）
const NewAsset = defineAsyncComponent(() => import('~/pages/assets/new.vue'))
// 2.006.4：伺服器端分頁／排序（每頁 100 筆，找碴 C7／C8），只回畫面用到的欄位；項次＝伺服器給的固定編號 no（C19）；
// 資料不完整的列（括號名稱、名稱就是 IP、類型系統位置全無資料）排在最後（C6）。
interface Row { uid: string; no: number; role_label?: string; unit_records?: number; key: string; name: string; ip: string | null; serial: string; type: string; system: string; where: string; u_text?: string; room: string | null; rack: string | null; status_label: string; onboarded: boolean; has_cia: boolean; records: number; vmware?: boolean }
interface Res { tabs: { key: string; label: string; count: number }[]; tab: string; rows: Row[]; total: number; page: number; size: number; per_record: boolean; records?: any[]; bridge_tab: boolean; not_in_bridge_tabs: { role: string; label: string; rows: number }[]; bridge_rows: number; bridge_machines: number; filter?: { os_source: string | null; os_class: string | null; label: string }; filter_rows?: any[]; filter_total?: number }
const { apiFetch } = useApi()
const { guardedFetch } = useGuardedFetch()
const { showToast } = useToast()
const route = useRoute()
const router = useRouter()
const tab = computed({ get: () => String(route.query.tab || 'all'), set: (v: string) => router.replace({ query: { ...route.query, tab: v } }) })
const perRecord = computed({ get: () => route.query.per === '1', set: (v: boolean) => router.replace({ query: { ...route.query, per: v ? '1' : undefined } }) })
const q = ref(String(route.query.q || ''))
const res = ref<Res | null>(null)
const err = ref('')
const loading = ref(false)
const page = computed(() => Number(route.query.page) || 1)
const size = computed(() => Number(route.query.size) || 100)
const sortKey = computed(() => String(route.query.sort || 'no'))
const sortDir = computed<'asc' | 'desc'>(() => (route.query.desc === '1' ? 'desc' : 'asc'))
// 對帳頁 OS 那幾列下鑽（?os_source=facts|guessed|unknown、?os_class=…）：改列逐筆登記，筆數＝對帳頁該列數字
const osSource = computed(() => String(route.query.os_source || ''))
const osClass = computed(() => String(route.query.os_class || ''))
const filtering = computed(() => !!(osSource.value || osClass.value))
const hostQuery = (extra: Record<string, any> = {}) => ({ tab: tab.value, per_record: perRecord.value, q: String(route.query.q || '') || undefined,
  os_source: osSource.value || undefined, os_class: osClass.value || undefined,
  page: page.value, size: size.value, sort: sortKey.value, desc: sortDir.value === 'desc', ...extra })
async function load() {
  loading.value = true
  try {
    res.value = await apiFetch<Res>('/api/v2/hosts', { query: hostQuery() })
    err.value = ''
  } catch (e: any) { err.value = `主機清單載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` } finally { loading.value = false }
}
await load()
watch(() => [route.query.tab, route.query.per, route.query.q], () => { selected.value = {}; picked.value = {} })
watch(() => [route.query.tab, route.query.per, route.query.q, route.query.page, route.query.size, route.query.sort, route.query.desc, route.query.os_source, route.query.os_class], load)
function search() { router.replace({ query: { ...route.query, q: q.value.trim() || undefined, page: undefined } }) }
function onSort(k: string) {
  const key = k === '__no' ? 'no' : k
  const desc = key === sortKey.value && sortDir.value === 'asc'
  router.replace({ query: { ...route.query, sort: key === 'no' ? undefined : key, desc: desc ? '1' : undefined, page: undefined } })
}
function onPage(p: number, s: number) { router.replace({ query: { ...route.query, page: p > 1 ? String(p) : undefined, size: s !== 100 ? String(s) : undefined } }) }
const server = computed(() => (res.value ? { total: filtering.value ? (res.value.filter_total ?? 0) : res.value.total, page: res.value.page, size: res.value.size, sortKey: sortKey.value === 'no' ? '__no' : sortKey.value, sortDir: sortDir.value } : null))
const TABS = computed(() => (res.value?.tabs ?? []).map(t => ({ k: t.key, label: `${t.label} ${t.count.toLocaleString()}` })))
const deco = (r: Row) => ({ ...r, __no: r.no, onb: r.onboarded ? '已納管' : '未納管', cia: r.has_cia ? '已登錄' : '未登錄 CIA' })
const rows = computed(() => (res.value?.rows ?? []).map(deco))
const filterRows = computed(() => (res.value?.filter_rows ?? []).map((r: any) => ({ ...r, __no: r.no })))
const exportFilterRows = async () => ((await apiFetch<Res>('/api/v2/hosts', { query: hostQuery({ page: 1, size: 0 }) })).filter_rows ?? []).map((r: any) => ({ ...r, __no: r.no }))
function clearFilter() { router.replace({ query: { ...route.query, os_source: undefined, os_class: undefined, page: undefined } }) }
const recRows = computed(() => (res.value?.records ?? []).map((r: any) => ({ ...r, __no: r.no })))
const selected = ref<Record<string, boolean>>({})
// 換頁後仍記得勾過的列（伺服器端分頁：其他頁的列不在 rows 裡）
const picked = ref<Record<string, any>>({})
watch([selected, rows], () => {
  const n = { ...picked.value }
  for (const r of rows.value) { if (selected.value[r.uid]) n[r.uid] = r; else delete n[r.uid] }
  picked.value = n
}, { deep: true })
const pickedRows = computed<any[]>(() => Object.values(picked.value))
const host = (r: any) => `/v2/machine/${encodeURIComponent(r.key)}`

// 批次作業
const showOnboard = ref(false)
const showOffline = ref(false)
// VMware 設備（ESXi／vCenter）不可納管（天條）：不送進批次納管，按鈕旁講出排除了幾台（後端 batch_onboard 也會擋）
const vmwarePicked = computed(() => pickedRows.value.filter(r => r.vmware).length)
const onboardIps = computed(() => pickedRows.value.filter(r => !r.vmware).map(r => (r.ip || '').split(/[,，\s]+/)[0]).filter(Boolean))
const offlineSerials = computed(() => pickedRows.value.map(r => r.serial))
async function addAudit() {
  const racks = [...new Map(pickedRows.value.filter(r => r.room && r.rack).map(r => [`${r.room}|${r.rack}`, { room: r.room, rack: r.rack }])).values()]
  const skip = pickedRows.value.length - pickedRows.value.filter(r => r.room && r.rack).length
  if (!racks.length) { showToast('所選主機都沒有機櫃位置，無法加入機櫃盤點', 'warn'); return }
  if (!window.confirm(`以所選主機所在的 ${racks.length} 個機櫃開始一次機櫃盤點？${skip ? `\n（${skip} 台沒有機櫃位置，不列入）` : ''}`)) return
  try {
    const r = await guardedFetch<{ id: number; racks: number; boxes: number }>('/api/v2/inv/rack/tasks', { method: 'POST', body: { racks } })
    showToast(`已開始機櫃盤點 #${r.id}：${r.racks} 櫃、${r.boxes} 台`, 'success')
    navigateTo({ path: '/v2/inventory/rack', query: { task: String(r.id), tab: 'result' } })
  } catch (e: any) { showToast(`開始盤點失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error') }
}
function csvCell(v: any) {
  let s = v == null ? '' : String(v)
  if (typeof v === 'string' && /^[=+\-@\t\r]/.test(s)) s = "'" + s   // 防公式注入（審查 B1）：= + - @ Tab CR 開頭的一律當文字
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}
// 表格的「匯出」：同一支 API、同樣的頁籤／篩選／排序，size=0 拿全部（不只這一頁）
const exportAllRows = async () => ((await apiFetch<Res>('/api/v2/hosts', { query: hostQuery({ per_record: false, page: 1, size: 0 }) })).rows ?? []).map(deco)
const exporting = ref(false)
async function exportCsv() {
  // 項次沿用畫面上的固定編號（找碴 C19）；沒勾選＝整個頁籤（向後端拿全部，不只這一頁）
  let list: any[] = pickedRows.value
  if (!list.length) {
    exporting.value = true
    try { list = ((await apiFetch<Res>('/api/v2/hosts', { query: hostQuery({ per_record: false, page: 1, size: 0 }) })).rows ?? []).map(deco) } catch (e: any) { showToast(`匯出失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}`, 'error'); return } finally { exporting.value = false }
  }
  list = [...list].sort((a, b) => a.no - b.no)
  const pre = res.value?.bridge_tab ? (({ physical: 'P', vm: 'V', mgmt: 'M' } as Record<string, string>)[tab.value] || 'H') : 'H'
  const head = ['項次', '主機名稱', 'IP', '類型', '業務系統', '位置', '偵測狀態', '納管', 'CIA', '登記筆數', '資產編號']
  const lines = [head.join(',')].concat(list.map(r => [`${pre}${r.no}`, r.name, r.ip, r.type, r.system, r.where, r.status_label, r.onb, r.cia, r.records, r.serial].map(csvCell).join(',')))
  const blob = new Blob(['﻿' + lines.join('\n')], { type: 'text/csv;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `主機查詢_${tab.value}_${new Date().toISOString().slice(0, 10)}.csv`
  a.click()
  URL.revokeObjectURL(a.href)
}
</script>

<template>
  <V2Page v-model="tab" :tabs="TABS">
    <template #title><h1 class="h1">2.1 主機查詢</h1></template>
    <template #conclusion>
      <span v-if="res">{{ TABS.find(t => t.k === tab)?.label }} 台</span>
      <InfoNote>一台一列（同一台的多筆登記合併）；點主機名稱開啟主機履歷。資料不完整的列（名稱是括號說明或 IP、類型／業務系統／位置都無資料）排在最後。</InfoNote>
    </template>
    <template #actions>
      <NuxtLink :to="{ query: { ...route.query, new: '1' } }" class="btn">新增主機</NuxtLink>
    </template>
    <template #tab>
      <!-- 新增主機（2.007.59）：直接嵌入既有表單（手動新增、網段掃描納入）；功能與 API 不變，寫入邏輯全在後端 -->
      <div v-if="route.query.new === '1'" class="newbox">
        <div class="bar"><b>新增主機</b><NuxtLink :to="{ query: { ...route.query, new: undefined } }" class="btn2">收起</NuxtLink></div>
        <V2Embed><NewAsset /></V2Embed>
      </div>
      <div class="bar">
        <input v-model="q" class="in" placeholder="在此頁籤內篩選：主機名稱、IP、資產編號、業務系統、位置" @keydown.enter="search">
        <button type="button" class="btn2" @click="search">篩選</button>
        <label class="chk"><input v-model="perRecord" type="checkbox"> 顯示每筆登記</label>
        <span class="sp" />
        <span class="muted">已選 {{ pickedRows.length }} 台</span>
        <button v-if="!isViewer" type="button" class="btn2" :disabled="!onboardIps.length" @click="showOnboard = true">批次納管</button>
        <span v-if="vmwarePicked" class="warn">VMware 設備 {{ vmwarePicked }} 台不可納管，已排除</span>
        <button v-if="!isViewer" type="button" class="btn2" :disabled="!offlineSerials.length" @click="showOffline = true">標記下線／報廢</button>
        <button v-if="!isViewer" type="button" class="btn2" :disabled="!pickedRows.length" @click="addAudit">加入盤點</button>
        <button v-if="pickedRows.length" type="button" class="btn2" :disabled="exporting" @click="exportCsv">{{ exporting ? '匯出中…' : '匯出所選（CSV）' }}</button>
        <InfoNote>批次納管：每台各走一次一鍵納管（同樣的檢查、備份、紀錄）；VMware 設備（ESXi／vCenter）不可納管，勾了也不會送出。標記下線／報廢：原因必填，記一筆 CIA 待異動，不直接改台數。加入盤點：以所選主機所在的機櫃開始一次機櫃盤點（7.1）。</InfoNote>
      </div>
      <div v-if="res && !err" class="note">
        <InfoNote v-if="res.bridge_tab">此頁籤一列＝數量對帳算一台的單位，數字與 1.1 今日概況、9.2 數量對帳相同；LPAR、一體機、同箱元件依機身／框算一台。</InfoNote>
        <InfoNote v-else-if="tab === 'missing'">從未被任何來源偵測到、只有 CIA 登記的主機。可能已關機、掃描範圍未涵蓋，或 CIA 資料有誤。與「逾期未偵測」合計＝偵測狀態的疑似下線台數。</InfoNote>
        <InfoNote>全部登記台數＝資產庫每一台（現役，依同一台合併），包含數量對帳不算進實體設備、虛擬機、管理口的登記：{{ res.not_in_bridge_tabs.map(x => `${x.label} ${x.rows} 筆`).join('、') || '無' }}。
          因此全部登記台數不等於實體設備＋虛擬機＋管理口，也不是 1.1 的「主機」部數（主機只算作業系統實例）。實體設備、虛擬機、管理口的算法見 9.2 數量對帳（數量對帳共 {{ res.bridge_rows }} 筆登記、{{ res.bridge_machines }} 台）。</InfoNote>
      </div>
      <p v-if="err" class="err">{{ err }}</p>
      <p v-else-if="loading && !res" class="muted">載入中…</p>
      <template v-else-if="filtering">
        <div class="bar"><b>篩選：{{ res?.filter?.label }}　共 {{ (res?.filter_total ?? 0).toLocaleString() }} 筆登記</b>
          <button type="button" class="btn2" @click="clearFilter">清除篩選</button>
          <InfoNote>來自 9.2 數量對帳的 OS 分項，筆數與該處數字相同；一列是一筆登記（不是一台）。篩選中不分頁籤，也不套用上方的搜尋與勾選批次作業。「OS 空白」的母體含退役登記。</InfoNote></div>
        <V2Table :rows="filterRows" prefix="R" :server="server" unit="筆" :export-all="exportFilterRows" empty="沒有符合的登記" @sort="onSort" @page="onPage"
                 :cols="[{ k: 'machine', label: '主機', link: host }, { k: 'serial', label: '資產編號', mono: true }, { k: 'source', label: '來源' }, { k: 'hostname', label: '登記主機名稱' }, { k: 'ip', label: '登記 IP', mono: true }, { k: 'os', label: '作業系統' }, { k: 'status', label: '資產狀態' }, { k: 'api_id', label: '業務系統', mono: true }, { k: 'rack', label: '機櫃欄' }]" />
      </template>
      <V2Table v-else-if="!perRecord" v-model:selected="selected" :rows="rows" :prefix="res?.bridge_tab ? (({ physical: 'P', vm: 'V', mgmt: 'M' } as Record<string, string>)[tab] || 'H') : 'H'" selectable row-key="uid" :server="server" unit="台" :export-all="exportAllRows" empty="此頁籤沒有主機"
               @sort="onSort" @page="onPage"
               :cols="res?.bridge_tab
                 ? [{ k: 'name', label: '主機名稱', link: host }, { k: 'ip', label: 'IP', mono: true }, { k: 'role_label', label: '數量對帳分類' }, { k: 'unit_records', label: '此單位登記筆數', mono: true }, { k: 'type', label: '類型' }, { k: 'system', label: '業務系統' }, { k: 'where', label: '位置' }, { k: 'u_text', label: 'U 位' }, { k: 'status_label', label: '偵測狀態' }, { k: 'onb', label: '納管' }]
                 : [{ k: 'name', label: '主機名稱', link: host }, { k: 'ip', label: 'IP', mono: true }, { k: 'type', label: '類型' }, { k: 'system', label: '業務系統' }, { k: 'where', label: '位置' }, { k: 'status_label', label: '偵測狀態' }, { k: 'onb', label: '納管' }, { k: 'cia', label: 'CIA' }, { k: 'records', label: '登記筆數', mono: true }]" />
      <V2Table v-else :rows="recRows" prefix="R" :max="500" empty="此頁籤沒有登記"
               :cols="[{ k: 'machine', label: '主機', link: host }, { k: 'serial', label: '資產編號', mono: true }, { k: 'source', label: '來源' }, { k: 'hostname', label: '登記主機名稱' }, { k: 'ip', label: '登記 IP', mono: true }, { k: 'os', label: '作業系統' }, { k: 'status', label: '資產狀態' }, { k: 'api_id', label: '業務系統', mono: true }, { k: 'rack', label: '機櫃欄' }]" />
      <BatchOnboardModal v-if="showOnboard" :ips="onboardIps" @close="showOnboard = false" @done="showOnboard = false; load()" />
      <OfflineMarkModal v-if="showOffline" :serials="offlineSerials" :who="pickedRows.length === 1 ? pickedRows[0].name : ''" @close="showOffline = false" @done="showOffline = false; load()" />
    </template>
  </V2Page>
</template>

<style scoped>
.h1 { margin: 0; font-size: 22px; font-weight: 700; }
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 10px; font-size: 13px; }
.in { font-family: inherit; font-size: 13px; padding: 5px 10px; border: 1px solid var(--border-strong); border-radius: 8px; min-width: 320px; }
.chk { display: inline-flex; gap: 4px; align-items: center; }
.sp { flex: 1; }
.btn { font-family: inherit; font-size: 14px; padding: 8px 14px; border-radius: 10px; border: 1px solid var(--brand); background: var(--brand); color: var(--type-on); text-decoration: none; }
.btn2 { font-family: inherit; font-size: 13px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.btn2:disabled { opacity: .5; cursor: default; }
.muted { color: var(--muted); }
.note { margin: 0 0 8px; font-size: 13px; color: var(--ink-aux); }
.err { color: var(--bad); }
.warn { color: var(--warn-text); }
.newbox { border: 1px solid var(--border); border-radius: 12px; background: var(--sub); padding: 10px 14px; margin-bottom: 12px; }
</style>
