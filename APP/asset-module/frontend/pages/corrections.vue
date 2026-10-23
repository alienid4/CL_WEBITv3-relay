<script setup lang="ts">
// 系統校正清單（v1.473.0，2026-10-20）：已納管主機的實測值跟 CIA 不符時，畫面與分類用實測值，
// 這裡列出「被系統校正過的是哪些」，交給 CIA 更正。CIA 登記值不改。
// 三個頁簽＝後端 /api/measured-mismatch 同一份：系統校正（CIA 待更正）／疑似不符（只有掃描）／CIA 已更正（歷程）。
interface Row {
  no: number; asset_serial: string; hostname: string | null; field_key: string; field: string
  cia: string; measured: string; source: string; confidence: string; kind: string
  at: string | null; via_serial: string | null; first_seen?: string; against?: string; needs?: string
  action?: string; note?: string
}
interface Resp { corrected: Row[]; suspect: Row[]; history: Row[]; fields: { key: string; label: string }[]
  sync_error: string | null }

const { apiFetch } = useApi()
const route = useRoute()
const rt = useRuntimeConfig()
const data = ref<Resp | null>(null)
const loadError = ref('')
const loading = ref(true)
type Tab = 'corrected' | 'suspect' | 'history'
const tab = ref<Tab>((['corrected', 'suspect', 'history'].includes(String(route.query.tab))
  ? String(route.query.tab) : 'corrected') as Tab)
const field = ref('')

onMounted(async () => {
  try { data.value = await apiFetch<Resp>('/api/measured-mismatch') } catch (e: any) {
    loadError.value = `載入失敗：${e?.data?.detail ?? e?.message ?? e}`
  } finally { loading.value = false }
})

const TABS: { k: Tab; label: string }[] = [
  { k: 'corrected', label: '系統校正（CIA 待更正）' },
  { k: 'suspect', label: '疑似不符（不自動校正）' },
  { k: 'history', label: 'CIA 已更正／不再適用' },
]
const tabRows = computed<Row[]>(() => (data.value?.[tab.value] ?? []))
const rows = computed<Row[]>(() => tabRows.value.filter((r) =>
  (!field.value || r.field_key === field.value) && (!onlyNeeds.value || !!r.needs)))
const { sortKey, sortDir, toggle, sorted } = useSort(rows, 'no')
// 需要人處理的（CIA 要改）：只有「要被改掉的那個值」（原值）紅字，其餘一般字
// （使用者 2026-10-21 第二次：「太紅了，171T 是需要矯正的，把它變紅就好」）。
const needCount = computed(() => tabRows.value.filter((r) => r.needs).length)
const onlyNeeds = ref(false)
const fieldCounts = computed(() => {
  const m: Record<string, number> = {}
  for (const r of tabRows.value) m[r.field_key] = (m[r.field_key] ?? 0) + 1
  return m
})
const KIND: Record<string, string> = { corrected: '⟲ 系統校正', suspect: '疑似不符', cia_fixed: 'CIA 已更正', gone: '不再適用' }
const exportHref = computed(() => `${rt.public.apiBase}/api/measured-mismatch/export?kind=${tab.value}`)
const ciaReportHref = computed(() => `${rt.public.apiBase}/api/measured-mismatch/cia-report`)
</script>

<template>
  <div class="page">
    <div class="hd">
      <h1>系統校正清單</h1>
      <InfoNote>
        已納管主機登入收集到的（確定）或 RVTools 回報的作業系統（高），跟 CIA 登記不一樣時，
        畫面主值、分類、EOS 改用實測值，並標「⟲ 系統校正」。<b>CIA 登記值沒有被改</b>——這份清單交給 CIA 窗口更正；
        CIA 重匯後值一樣了，那筆會移到「CIA 已更正」。只有存活掃描講不一樣的列「疑似不符」，不自動校正。
        <br><br><b>優先序（PRECEDENCE1）</b>
        <table class="ptbl"><tbody>
          <tr><td>1</td><td>登入實測（SSH／WinRM）</td><td>確定：主機名、IP、作業系統</td></tr>
          <tr><td>2</td><td>RVTools（VMware Tools）</td><td>高：主機名；作業系統只在 CIA 空白或平台家族不同時（設定檔類型常不準）</td></tr>
          <tr><td>3</td><td>掃描 DYN（觀察到的）</td><td>中：主機名（rDNS）；存活 IP、SSH banner、開放埠只提示</td></tr>
          <tr><td>4</td><td>CIA 登記</td><td>以上都沒有才用</td></tr>
        </tbody></table>
        推測（TTL 猜 OS、閘道位址、MAC 廠商）不會蓋 CIA，只列「疑似不符」。
        CPU／記憶體：登入實測贏 RVTools（VM 改過大小，RVTools 是舊的；記憶體差 15% 內不算）；「對照」欄寫 RVTools 的是 vCenter 那邊舊，不是 CIA 要改。
        「CIA 回報表」只收對照＝CIA 的更正＋CIA 沒登記但已納管／RVTools 有的機器（新增登記），附說明頁，可以直接交給 CIA。
        主機名校正：CIA 的名字如果在別的 IP 還活著，判成「CIA IP 填錯」，不校正。
        只差大小寫、網域（.xxx.com.tw）的不算不符；數字補 0 不同（CIA SECSVR161-22 vs 機器 SECSVR161-022）算不符，應改為機器的真實名字（短名）。
        <b>紅字</b>＝要被改掉的值（原值）；需誰處理：CIA 改登記（主機名以機器真實名字為主）。沒有紅字＝資訊。
        要修改什麼：待確認＝疑似不符、還沒人要改；不用改 CIA（vCenter 舊值）＝對照 RVTools 的列。
        哪些欄位可以被哪種證據校正：correction_fields.json。
      </InfoNote>
      <span class="spacer" />
      <a class="btn small" :href="ciaReportHref">⬇ CIA 回報表</a>
      <a class="btn small" :href="exportHref">⬇ 匯出這一頁</a>
    </div>
    <p v-if="loadError" class="dim">{{ loadError }}</p>
    <p v-if="data?.sync_error" class="dim">歷程沒寫進去：{{ data.sync_error }}</p>

    <div v-if="data" class="cards">
      <button v-for="t in TABS" :key="t.k" class="card" :class="{ on: tab === t.k }" @click="tab = t.k; field = ''">
        <span class="n mono">{{ (data[t.k] ?? []).length.toLocaleString() }}</span><span class="l">{{ t.label }}</span>
      </button>
    </div>
    <div v-if="data" class="levels">
      <span class="ll">欄位</span>
      <button class="chip" :class="{ on: !field }" @click="field = ''">全部 <b class="mono">{{ tabRows.length }}</b></button>
      <button class="chip" :class="{ on: onlyNeeds }" @click="onlyNeeds = !onlyNeeds">需人處理 <b class="mono">{{ needCount }}</b></button>
      <button v-for="f in data.fields.filter((x) => fieldCounts[x.key])" :key="f.key" class="chip"
              :class="{ on: field === f.key }" @click="field = f.key">
        {{ f.label }} <b class="mono">{{ fieldCounts[f.key] }}</b>
      </button>
    </div>

    <p v-if="loading" class="dim">載入中…</p>
    <template v-else-if="data">
      <p v-if="!rows.length" class="dim">沒有資料。</p>
      <div v-else class="tbl-wrap">
        <table>
          <thead>
            <tr>
              <SortTh k="no" :active="sortKey" :dir="sortDir" @sort="toggle">#</SortTh>
              <SortTh k="asset_serial" :active="sortKey" :dir="sortDir" @sort="toggle">資產序號</SortTh>
              <SortTh k="hostname" :active="sortKey" :dir="sortDir" @sort="toggle">主機名稱</SortTh>
              <SortTh k="field" :active="sortKey" :dir="sortDir" @sort="toggle">欄位</SortTh>
              <SortTh k="against" :active="sortKey" :dir="sortDir" @sort="toggle">對照</SortTh>
              <SortTh k="cia" :active="sortKey" :dir="sortDir" @sort="toggle">原值</SortTh>
              <SortTh k="measured" :active="sortKey" :dir="sortDir" @sort="toggle">{{ tab === 'suspect' ? '實測值' : '校正值' }}</SortTh>
              <SortTh k="source" :active="sortKey" :dir="sortDir" @sort="toggle">依據</SortTh>
              <SortTh k="confidence" :active="sortKey" :dir="sortDir" @sort="toggle">信心</SortTh>
              <SortTh k="kind" :active="sortKey" :dir="sortDir" @sort="toggle">狀態</SortTh>
              <SortTh k="action" :active="sortKey" :dir="sortDir" @sort="toggle">要修改什麼</SortTh>
              <SortTh k="needs" :active="sortKey" :dir="sortDir" @sort="toggle">需誰處理</SortTh>
              <SortTh k="at" :active="sortKey" :dir="sortDir" @sort="toggle">{{ tab === 'history' ? '關閉時間' : '最後確認' }}</SortTh>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in sorted" :key="r.no">
              <td class="mono">{{ r.no }}</td>
              <td class="mono"><NuxtLink :to="`/assets/${encodeURIComponent(r.asset_serial)}`" class="dl">{{ r.asset_serial }}</NuxtLink></td>
              <td>{{ r.hostname || '—' }}</td>
              <td>{{ r.field }}</td>
              <td>{{ r.against || 'CIA' }}</td>
              <td class="mono" :class="{ fixme: r.needs && r.cia }">{{ r.cia || '—' }}</td>
              <td class="mono">{{ r.measured || '—' }}<template v-if="r.note"><InfoNote>{{ r.note }}</InfoNote></template></td>
              <td>{{ r.source }}</td>
              <td>{{ r.confidence }}</td>
              <td><span class="pill" :class="{ ok: r.kind === 'corrected' || r.kind === 'cia_fixed', warn: r.kind === 'suspect' }">{{ KIND[r.kind] || r.kind }}</span></td>
              <td>{{ r.action || '不知道' }}</td>
              <td>{{ r.needs || '—' }}</td>
              <td class="mono">{{ r.at || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>

<style scoped>
.hd { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.hd h1 { margin: 0; }
.spacer { flex: 1; }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 8px; margin: 12px 0 8px; }
.card { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; padding: 10px 12px;
        background: var(--card); border: 1px solid var(--border); border-radius: var(--radius);
        cursor: pointer; text-align: left; }
.card:hover { border-color: var(--border-strong); }
.card.on { border-color: var(--brand); background: var(--mint); }
.card .n { font-size: 20px; font-weight: 700; color: var(--ink); font-family: var(--disp); }
.card .l { font-size: 12.5px; color: var(--ink-soft); }
.levels { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-bottom: 12px; font-size: 12.5px; }
.ll { color: var(--muted); }
.chip { border: 1px solid var(--border); background: var(--sub); border-radius: 999px; padding: 3px 10px;
        cursor: pointer; color: var(--ink-soft); font-size: 12px; }
.chip.on { border-color: var(--brand); color: var(--brand-dark); background: var(--mint); }
.tbl-wrap { overflow-x: auto; }
.pill { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 11.5px;
        border: 1px solid var(--border); background: var(--sub); white-space: nowrap; }
.pill.ok { color: var(--brand-dark); border-color: var(--brand); background: var(--mint); }
.pill.warn { color: var(--warn-text); border-color: var(--warn); background: var(--warn-soft); }
.dl { color: var(--brand-dark); text-decoration: none; }
.dl:hover { text-decoration: underline; }
.dim { color: var(--muted); }
.fixme { color: var(--bad); font-weight: 700; }
.ptbl { margin: 6px 0; border-collapse: collapse; font-size: 12px; }
.ptbl td { border: 1px solid var(--border); padding: 2px 6px; }
</style>
