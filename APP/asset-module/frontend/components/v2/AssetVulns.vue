<script setup lang="ts">
import { sortRows } from '~/composables/useSort'
// 資產明細「弱點」分頁（V20 第 1 批，2.048.27，從舊 /assets/[serial] 搬出來，行為照舊）：還沒結的＋修過的。
// 三種狀況要分得出來，不能都顯示成「沒弱點」：1. 全站還沒匯入過弱點報告 2. 有報告但這台不在裡面 3. 在裡面。
// 第 2 種最危險：一台從沒被掃過的機器會看起來很乾淨。拿不到也不可以顯示成 0。
interface VulnRow {
  id: number; plugin_id: string | null; host: string; protocol: string | null
  port: string | null; name: string | null; severity: string | null
  due_date: string | null; overdue: string | null; unit: string | null
  owner: string | null; closed: number; closed_at: string | null; sheet: string | null
}
interface VulnResp {
  has_report: boolean; in_report: boolean
  batch: { id: number; file_name: string | null; imported_by: string | null; imported_at: string | null; rows: number; matched: number } | null
  open: VulnRow[]; closed: VulnRow[]
}
const props = defineProps<{ serial: string }>()
const emit = defineEmits<{ state: [s: { loaded: boolean; err: string; open: number; closed: number }] }>()
const { apiFetch } = useApi()

const vulns = ref<VulnResp | null>(null)
const loaded = ref(false)
const err = ref('')
onMounted(async () => {
  try {
    vulns.value = await apiFetch<VulnResp>('/api/vulns/by-asset', { query: { asset_serial: props.serial } })
  } catch (e: any) {
    const st = e?.statusCode ?? e?.response?.status
    const msg = e?.data?.detail ?? e?.message ?? String(e)
    err.value = `弱點載入失敗：${st ? `HTTP ${st}：${msg}` : msg}`
  } finally {
    loaded.value = true
  }
})
// 舊頁預設排序：未結案照修補期限、已結案照結案日期（都由早到晚）；項次照這個順序固定
const withPort = (v: VulnRow) => ({ ...v, port_text: `${v.protocol || ''}${v.port ? '/' + v.port : ''}` })
const openRows = computed(() => sortRows((vulns.value?.open ?? []).map(withPort), 'due_date', 'asc').map((v, i) => ({ ...v, __no: i + 1 })))
const closedRows = computed(() => sortRows((vulns.value?.closed ?? []).map(withPort), 'closed_at', 'asc').map((v, i) => ({ ...v, __no: i + 1 })))
watch([loaded, err, () => openRows.value.length, () => closedRows.value.length],
  ([l, e, o, c]) => emit('state', { loaded: l as boolean, err: e as string, open: o as number, closed: c as number }), { immediate: true })

const SEV = { k: 'severity', label: '嚴重度' }
const OPEN_COLS = [SEV, { k: 'name', label: '弱點' }, { k: 'port_text', label: '埠', mono: true }, { k: 'due_date', label: '修補期限', mono: true },
  { k: 'overdue', label: '逾期' }, { k: 'owner', label: '負責人' }, { k: 'sheet', label: '來源表' }]
const CLOSED_COLS = [SEV, { k: 'name', label: '弱點' }, { k: 'port_text', label: '埠', mono: true }, { k: 'closed_at', label: '結案日期', mono: true },
  { k: 'owner', label: '負責人' }, { k: 'sheet', label: '來源表' }]
</script>

<template>
  <div>
    <p v-if="!loaded" class="muted">載入中…</p>
    <p v-else-if="err" class="err">{{ err }}</p>
    <p v-else-if="!vulns?.has_report" class="muted">
      全站還沒匯入過弱點報告。<NuxtLink class="lnk" to="/v2/patch">去 8.1 弱點管理匯入 →</NuxtLink>
    </p>
    <p v-else-if="!vulns.in_report" class="muted">
      有弱點報告，但<b>這一台不在報告裡</b>（≠沒弱點）（{{ vulns.batch?.file_name }}・{{ vulns.batch?.imported_at }}）
      <InfoNote>這不等於它沒弱點，而是它沒被掃到（或弱點單上的主機名跟清冊對不起來）。</InfoNote>
    </p>
    <template v-else>
      <p class="muted small">
        資料來源：{{ vulns.batch?.file_name }}・{{ vulns.batch?.imported_at }}
        <template v-if="vulns.batch?.imported_by">・由 {{ vulns.batch.imported_by }} 匯入</template>
        <InfoNote>結案與否照弱點報告裡的「結案狀態」欄（人填的、有人負責的事實），不從「這次掃不到了」反推——掃不到也可能是這次根本沒掃這一台。</InfoNote>
      </p>
      <h4 class="h4">還沒結案 {{ openRows.length }}</h4>
      <p v-if="!openRows.length" class="muted">這一台在這份報告裡沒有未結案的弱點。</p>
      <V2Table v-else :rows="openRows" :cols="OPEN_COLS" row-key="id" :max="500" :export-name="`${serial}_弱點未結案`">
        <template #cell-severity="{ row }"><span class="sev" :class="`s-${(row.severity || '').toLowerCase()}`">{{ row.severity || '—' }}</span></template>
        <template #cell-overdue="{ row }"><span :class="{ bad: row.overdue && row.overdue !== '未逾期' }">{{ row.overdue || '—' }}</span></template>
      </V2Table>
      <h4 class="h4">已結案（修過的）{{ closedRows.length }}</h4>
      <p v-if="!closedRows.length" class="muted">這份報告裡這一台還沒有結案的弱點。</p>
      <V2Table v-else :rows="closedRows" :cols="CLOSED_COLS" row-key="id" :max="500" :export-name="`${serial}_弱點已結案`">
        <template #cell-severity="{ row }"><span class="sev" :class="`s-${(row.severity || '').toLowerCase()}`">{{ row.severity || '—' }}</span></template>
      </V2Table>
    </template>
  </div>
</template>

<style scoped>
.h4 { margin: 12px 0 6px; font-size: 14px; }
.sev { display: inline-block; font-size: 11.5px; padding: 1px 8px; border-radius: 10px; background: var(--sub); color: var(--ink-soft); }
.sev.s-critical, .sev.s-high { background: var(--bad-soft); color: var(--bad); }
.sev.s-medium { background: var(--warn-soft); color: var(--warn-text); }
.sev.s-low { background: var(--mint); color: var(--brand-dark); }
.bad { color: var(--bad); }
.lnk { color: var(--link); }
.small { font-size: 12px; }
.muted { color: var(--muted); font-size: 13px; display: flex; gap: 4px; align-items: center; flex-wrap: wrap; }
.err { color: var(--bad); font-size: 13px; }
</style>
