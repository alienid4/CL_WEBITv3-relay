<script setup lang="ts">
// 9.4 →「彙整表比對」頁籤（2.060.20，S10）：伺服器資料彙整表（主機組最上游的表）跟 CIA／系統實掃比對。
// 卡片＝各類台數（＝下面清單同一份計算 /api/srv-sheet/rows），點卡片就篩清單；欄位不一致可再依欄位篩。
// 不逐台問人：清單可排序、可匯出 Excel 批次確認。只當證據，不改任何資產資料。匯入在 ⚙ 資料匯入。
const { apiFetch } = useApi()
const route = useRoute()
const router = useRouter()
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')
const kind = computed(() => q('sk') || 'no_cia')
const field = computed(() => q('sf') || '')
const go = (sk: string, sf = '') => router.replace({ query: { ...route.query, tab: 'srv', sk, sf: sf || undefined } })

const sum = ref<any>(null)
const data = ref<any>(null)
const err = ref('')
const busy = ref(false)
async function load() {
  busy.value = true; err.value = ''
  try {
    const [s, d] = await Promise.all([apiFetch<any>('/api/srv-sheet/summary'),
      apiFetch<any>('/api/srv-sheet/rows', { query: { kind: kind.value, field: field.value || undefined } })])
    sum.value = s; data.value = d
  } catch (e: any) { err.value = `載入失敗：${e?.data?.detail ?? e?.message ?? '未知錯誤'}` } finally { busy.value = false }
}
onMounted(load)
watch([kind, field], load)

const CARDS = [['no_cia', '表有、CIA 無', 'bad'], ['diff', '欄位不一致', 'warn'], ['self', '同列矛盾', 'warn'], ['off', '已關機', ''], ['no_ip', 'IP 空白或格式錯', ''], ['all', '全部', '']] as const
const cols = computed(() => {
  const base: any[] = [
    { k: 'ip', label: 'IP', mono: true, link: (r: any) => (r.machine_key ? `/v2/machine/${encodeURIComponent(r.machine_key)}` : null) },
    { k: 'host', label: '表的主機名' },
    { k: 'purpose', label: '表的用途' },
    { k: 'env', label: '表的環境別' },
    { k: 'ap_owner', label: 'AP 負責人' },
    { k: 'system_status', label: '系統偵測狀態' },
    { k: 'kind_text', label: '差異類別' },
  ]
  if (kind.value === 'diff') base.push({ k: 'diff_text', label: '哪裡不一致' }, { k: 'cia_host', label: 'CIA 主機名' }, { k: 'scan_host', label: '實掃主機名' },
    { k: 'cia_os', label: 'CIA 作業系統' }, { k: 'scan_os', label: '實掃作業系統' }, { k: 'cia_env', label: 'CIA 環境別' }, { k: 'cia_owner', label: 'CIA 負責人' }, { k: 'cia_room', label: 'CIA 機房' })
  else if (kind.value === 'self') base.push({ k: 'self_text', label: '矛盾在哪' }, { k: 'asset_name', label: '資訊資產名稱' })
  else if (kind.value === 'off') base.push({ k: 'off_date', label: '關機日期', mono: true }, { k: 'off_handler', label: '處理人員' })
  else base.push({ k: 'os', label: '表的作業系統' }, { k: 'location', label: '表的機房機櫃' }, { k: 'marked_at', label: '標記時間', mono: true })
  return base
})
const exportCols = [{ k: 'row_no', label: '彙整表列號' }, { k: 'ips', label: '全部 IP' }, { k: 'asset_name', label: '資訊資產名稱' }, { k: 'infra_owner', label: 'Infra 負責人' },
  { k: 'ap_unit', label: 'AP 負責單位' }, { k: 'machine_name', label: '系統主機名稱' }, { k: 'diff_text', label: '欄位不一致' }, { k: 'self_text', label: '同列矛盾' },
  { k: 'cia_host', label: 'CIA 主機名' }, { k: 'cia_os', label: 'CIA 作業系統' }, { k: 'cia_env', label: 'CIA 環境別' }, { k: 'cia_owner', label: 'CIA 負責人' }, { k: 'cia_room', label: 'CIA 機房' },
  { k: 'cia_purpose', label: 'CIA 用途' }, { k: 'scan_host', label: '實掃主機名' }, { k: 'scan_os', label: '實掃作業系統' }, { k: 'off_date', label: '關機日期' }, { k: 'off_handler', label: '關機處理人員' },
  { k: 'os', label: '表的作業系統' }, { k: 'location', label: '表的機房機櫃' }, { k: 'marked_at', label: '標記時間' }]
const KIND_NAME: Record<string, string> = { no_cia: '表有CIA無_待補登', diff: '欄位不一致', self: '同列矛盾', off: '已關機', no_ip: 'IP空白', all: '全部' }
const b = computed(() => sum.value?.batch)
</script>

<template>
  <div>
    <p v-if="err" class="err">{{ err }}</p>
    <div class="bar">
      <span v-if="b" class="muted">伺服器資料彙整表：{{ String(b.imported_at).slice(0, 16) }} 匯入（{{ b.file_name }}，{{ b.rows }} 列，最新標記 {{ b.data_date || '—' }}）</span>
      <span v-else class="warnt">還沒匯入伺服器資料彙整表</span>
      <NuxtLink class="lnk" to="/v2/settings?st=import&src=srv_sheet">⚙ 資料匯入</NuxtLink>
      <InfoNote>主機組流程是「先寫彙整表 → 建主機 → 上線 → 盤點 → CIA」，彙整表最上游、CIA 最下游。所以「表有、CIA 無」＝上線後漏登 CIA 的最強證據（已關機待處理的不算）。
        欄位不一致：主機名、作業系統（大類與 Windows 年份）、環境別、負責人、用途、機房，兩邊都有值而且確定不同才算，查不到的不算。實掃＝納管後系統實際收到的。
        同列矛盾：環境別寫正式（或系統等級是第一～三類）、用途或資訊資產名稱卻有測試／POC／UAT 等字樣；或環境別與系統等級互相矛盾。一列可能同時屬於好幾類（例如 CIA 無又同列矛盾），所以各卡加總不等於「全部」。只當證據，不改任何資產資料；確認後只寫回 CIA。</InfoNote>
    </div>
    <div v-if="sum" class="cards">
      <button v-for="[k, lab, tone] in CARDS" :key="k" type="button" class="card" :class="[tone, { on: kind === k && !field }]" :data-testid="`srv-card-${k}`" @click="go(k)">
        <span class="lab">{{ lab }}</span><b class="mono">{{ sum.counts?.[k] ?? 0 }}</b>
        <span v-if="k === 'no_cia'" class="sub">待補登 CIA・系統有偵測到 {{ sum.counts?.no_cia_in_system ?? 0 }}</span>
        <span v-else-if="k === 'diff' || k === 'self'" class="sub">待確認來源資料</span>
      </button>
    </div>
    <div v-if="sum && kind === 'diff'" class="bar">
      <button type="button" class="chip" :class="{ on: !field }" @click="go('diff')">全部欄位</button>
      <button v-for="(lab, f) in sum.fields" :key="f" type="button" class="chip" :class="{ on: field === f }" @click="go('diff', String(f))">{{ lab }} {{ sum.field_counts?.[f] ?? 0 }}</button>
    </div>
    <p v-if="busy && !data" class="muted">讀取中…</p>
    <V2Table v-if="data" :rows="data.rows" :cols="cols" :export-cols="exportCols" prefix="S" row-key="__no" unit="列"
             :export-name="`彙整表比對_${KIND_NAME[kind] ?? kind}${field ? '_' + (sum?.fields?.[field] ?? field) : ''}`" :empty="b ? '這一類沒有' : '還沒匯入彙整表'" />
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 4px 0 10px; font-size: 13px; }
.muted { color: var(--muted); font-size: 13px; }
.warnt { color: var(--warn-text); }
.err { color: var(--bad); }
.lnk { color: var(--link); font-size: 13px; }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px; margin-bottom: 10px; }
.card { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; padding: 10px 14px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); cursor: pointer; font-family: inherit; text-align: left; }
.card:hover { border-color: var(--border-strong); }
.card.on { border-color: var(--brand); background: var(--mint); }
.card .lab { font-size: 12px; color: var(--ink-soft); }
.card b { font-family: var(--disp); font-size: 22px; color: var(--ink); }
.card.bad b { color: var(--bad); }
.card.warn b { color: var(--warn-text); }
.card .sub { font-size: 11px; color: var(--muted); }
.mono { font-family: var(--disp); }
.chip { font-size: 12px; padding: 3px 10px; border-radius: 16px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink-aux); cursor: pointer; font-family: inherit; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
</style>
