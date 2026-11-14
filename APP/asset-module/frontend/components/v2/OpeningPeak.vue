<script setup lang="ts">
// 1.1 今日概況右欄「今日開盤尖峰（09:00～09:30，收盤後回補）」（2.045.0／2.045.1）。資料＝/api/v2/today 的 opening（opening_peak.summary）。
// 來源＝跳板機收盤後從 Aria（vCOps）匯出的 ESXi 主機檔，走中繼匯入；本系統不連 Aria。今天還沒收到就只寫一句「尚未匯入」，不放數字。
// 收到後：資料截至、5 分鐘一點、涵蓋／應有（應有＝資產庫同範圍 ESXi；沒有基準就寫「本批列到 N 台（未核對資產）」）、
// 資產有但這批沒有的（可點）、對不到資產幾台（可點）、近 20 工作日基準有幾天；
// CPU Max 前 10 名（看全部＝/api/v2/opening 同一份計算），三欄 Max／Avg／Min（GPT 審 B2：拿掉會被誤讀成 09:30 值的 Current），
// 每台帶上面跑的 VM 數（點了列出 VM 與業務系統）與 AP 聯絡五欄（查 ESXi 與 VM 掛的 APID）。每張表可排序、可匯出、有項次。
// 2.059.22（使用者 11-10 卡片改版 8 項）：主機只顯示短名（完整名在提示／匯出）；首頁只留 主機、最高 %（時間）、業務系統，
// 其他欄在「看全部」；拿掉點數欄（點數不足才在最高 % 旁標）；基準欄名照實際天數；超過 80% 標色＋標題「超過 80%：N 台」；
// 標明範圍（例：板橋 vCenter（esxi169-*）），不讓人以為是全公司；第二段「VM 超過主機 CPU 10%」：這批沒帶 VM 寫「無 VM 資料」，不寫「沒有」。
import { ALERT_CONTACT_COLS } from '~/utils/alertContact'
const props = defineProps<{ data: any }>()
const { apiFetch } = useApi()
const { showToast } = useToast()
const all = ref<any[] | null>(null)
const onlyUnmatched = ref(false)
const showMissing = ref(false)
const vmHost = ref<any | null>(null)
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
async function fetchAll() { return (await apiFetch<{ rows: any[] }>('/api/v2/opening', { query: { day: props.data.day } })).rows }
async function toggleAll(um = false) {
  if (all.value && onlyUnmatched.value === um) { all.value = null; onlyUnmatched.value = false; return }
  try { all.value = all.value ?? await fetchAll(); onlyUnmatched.value = um }
  catch (e: any) { showToast(`讀取失敗：${errMsg(e)}`, 'error') }
}
const shown = computed(() => (all.value ? (onlyUnmatched.value ? all.value.filter(r => r.match !== 'matched') : all.value) : props.data.top?.rows ?? []))
const missingRows = computed(() => (props.data.missing ?? []).map((h: string, i: number) => ({ __no: i + 1, host: h })))
const n = (v: any) => (v == null ? '—' : v)
const shortName = (h: string) => String(h || '').split('.')[0]
const FULL_POINTS = 6                     // 09:00～09:30、5 分鐘一點：少於 6 點＝這台資料不完整
const hotPct = computed(() => props.data.hot_pct ?? 80)
const maxText = (r: any) => (r.has_data ? `${r.max}（${r.max_at}）${r.points < FULL_POINTS ? `・點數不足 ${r.points}` : ''}` : '無資料')
const baseLabel = computed(() => (Number(props.data.baseline_days || 0) >= 20 ? '近 20 日 Max 中位數' : `近 ${props.data.baseline_days || 0} 日 Max 中位數（目前 ${props.data.baseline_days || 0} 日）`))
const vmCount = (r: any): number => Number(r?.vm_count || 0)
function openVms(r: any) { vmHost.value = vmHost.value?.host === r.host ? null : r }
const hostCol = { k: 'host', label: 'ESXi 主機' }
const maxCol = { k: 'max', label: '最高 %（時間）', mono: true, text: maxText, tone: (r: any) => (r.has_data && r.max > hotPct.value ? 'test' : '') }
const sysCol = { k: 'c_system_name', label: '業務系統', text: (r: any) => r.c_system_name || '—' }
// 首頁精簡（使用者：顯示重點就好，其他放「看全部」）
const TOP_COLS = [hostCol, maxCol, sysCol]
const COLS = computed(() => [
  hostCol, maxCol,
  { k: 'avg', label: 'Avg %', mono: true, text: (r: any) => n(r.avg) },
  { k: 'min', label: 'Min %', mono: true, text: (r: any) => n(r.min) },
  ...(Number(props.data.baseline_days || 0) > 0 ? [
    { k: 'baseline', label: baseLabel.value, mono: true, text: (r: any) => r.baseline_text },
    { k: 'delta', label: '比中位數', mono: true, text: (r: any) => (r.delta == null ? '—' : `${r.delta > 0 ? '+' : ''}${r.delta}`) }] : []),
  { k: 'vm_count', label: 'VM 台數', mono: true },
  { k: 'match_text', label: '資產對應' },
  ...ALERT_CONTACT_COLS,
])
const vm = computed(() => props.data.vm ?? { rows: [], hosts_with_vms: 0, threshold: 10 })
const VMHOT_COLS = [
  { k: 'vm', label: 'VM', link: (r: any) => r.link }, { k: 'host', label: 'ESXi 主機', text: (r: any) => shortName(r.host) },
  { k: 'pct', label: '佔主機 CPU %', mono: true }, { k: 'max_at', label: '時間', mono: true },
  { k: 'system', label: '業務系統', text: (r: any) => r.system || '—' }, { k: 'dept', label: '部門' }, { k: 'owner', label: '負責人' },
]
const VM_COLS = [
  { k: 'vm', label: 'VM', link: (r: any) => r.link }, { k: 'ip', label: 'IP', mono: true }, { k: 'api_id', label: 'APID', mono: true },
]
</script>

<template>
  <div class="op">
    <p v-if="data.status !== 'ok'" class="na-line">{{ data.label }}</p>
    <template v-else>
      <div class="q">
        <span>資料截至 <b class="mono">{{ data.as_of || '—' }}</b></span><span>{{ data.resolution }}</span>
        <span>範圍 <b>{{ data.scope_label || data.scope }}</b></span>
        <span>涵蓋 <b class="mono">{{ data.covered }}</b> 台・{{ data.expected_label }}</span>
        <span v-if="data.missing && data.missing.length">本批沒有 <button type="button" class="lb" @click="showMissing = !showMissing">{{ data.missing.length }}</button> 台</span>
        <span v-if="data.unmatched">對不到資產 <button type="button" class="lb" @click="toggleAll(true)">{{ data.unmatched }}</button> 台</span>
        <span>基準：{{ data.baseline_label }}</span>
        <span v-if="data.note" class="warnt">{{ data.note }}</span>
        <InfoNote>來源：跳板機收盤後從 Aria Operations（vCOps）匯出、經中繼送進來的檔案（本系統不連 Aria，也不連任何主機）；這是收盤後回補的開盤數據，不是盤中即時值。範圍 {{ data.scope || '—' }}（跳板機設定）。應有＝資產庫裡同範圍的 ESXi（vCenter／RVTools 匯入的 Host 欄＋登記成 VMware 平台的資產）；查不到基準時寫「本批列到幾台（未核對資產）」。涵蓋＝應有之中 09:00～09:30 至少有一個 CPU 值的台數。Max／Avg／Min 只算 09:00 起、09:30 以前的點，Aria 缺值的點跳過、不補 0。近 20 日 Max 中位數＝這台之前最多 20 個有資料的工作日，每天 CPU Max 的中位數，不足 20 天標出天數。VM 台數＝最近一次 vCenter 匯入裡跑在這台上的 VM，點列看是哪些、掛哪個業務系統；AP 聯絡五欄照這台和它上面 VM 的業務系統查，都沒有就照實寫「未提供」。</InfoNote>
      </div>
      <V2Table v-if="showMissing" :rows="missingRows" prefix="N" :max="20" empty="沒有" :cols="[{ k: 'host', label: '資產庫有、這批沒有的 ESXi' }]" />
      <div class="th"><b>{{ all ? (onlyUnmatched ? '對不到資產的主機' : `全部 ${all.length} 台`) : 'CPU 最高前 10 名' }}</b>
        <span :class="{ warnt: data.hot }">超過 {{ hotPct }}%：{{ data.hot ?? 0 }} 台</span>
        <button v-if="data.top.total > data.top.rows.length || all" type="button" class="lb" @click="toggleAll(false)">{{ all && !onlyUnmatched ? '只看前 10 名' : `看全部 ${data.top.total} 台` }}</button>
      </div>
      <V2Table :rows="shown" prefix="P" :max="all ? 50 : 10" empty="沒有主機" :cols="all ? COLS : TOP_COLS">
        <template #cell-host="{ row }"><NuxtLink v-if="row.link" :to="row.link" :title="row.host">{{ shortName(row.host) }}</NuxtLink><span v-else :title="row.host">{{ shortName(row.host) }}</span></template>
        <template #cell-vm_count="{ row }"><button v-if="vmCount(row)" type="button" class="lb" @click="openVms(row)">{{ vmCount(row) }}</button><span v-else>0</span></template>
      </V2Table>
      <div v-if="vmHost" class="tb">
        <div class="th"><b>{{ vmHost.host }} 上面的 VM（{{ vmHost.vm_count }} 台）</b><button type="button" class="lb" @click="vmHost = null">收起</button></div>
        <V2Table :rows="vmHost.vms" prefix="V" :max="50" empty="沒有 VM" :cols="VM_COLS" />
      </div>
      <div class="th"><b>VM 超過主機 CPU {{ vm.threshold }}%</b>
        <span v-if="!vm.hosts_with_vms" class="warnt">無 VM 資料（這批沒有帶 VM）</span>
        <span v-else>{{ vm.rows.length }} 台<span v-if="vm.missing_hosts?.length" class="warnt">・{{ vm.missing_hosts.length }} 台主機讀不到 VM</span></span>
        <InfoNote>VM 時段最高 MHz ÷ 主機實體 CPU 容量（不四捨五入比門檻）。這批沒帶 VM 時是「無 VM 資料」，不代表沒有 VM 超過；個別主機讀不到 VM 的另外標出。</InfoNote>
      </div>
      <V2Table v-if="vm.hosts_with_vms" :rows="vm.rows" prefix="M" :max="30" :cols="VMHOT_COLS" :empty="`沒有 VM 超過 ${vm.threshold}%`" />
    </template>
  </div>
</template>

<style scoped>
.op { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.na-line { margin: 0; padding: 8px 10px; border-radius: 8px; background: var(--warn-soft); color: var(--warn-text); font-size: 13px; }
.q { display: flex; flex-wrap: wrap; gap: 4px 12px; font-size: 13px; color: var(--ink-soft); align-items: center; }
.mono { font-family: var(--disp); }
.warnt { color: var(--warn-text); }
.th { display: flex; gap: 10px; align-items: center; font-size: 14px; }
.lb { background: none; border: 0; color: var(--brand); cursor: pointer; font: inherit; font-size: 13px; padding: 0; text-decoration: underline; }
</style>
