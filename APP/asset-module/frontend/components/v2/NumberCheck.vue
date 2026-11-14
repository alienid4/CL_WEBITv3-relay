<script setup lang="ts">
// 9.2 數量對帳「數字健檢」頁籤（V39，2.048.4 起 V2 原生；舊版 /number-check 轉到這裡）。
// 納管漏斗 5 個數字的 畫面值／獨立重算值／差異／差異解釋，全部由後端 number_proof 算（/api/number-proof），這裡不另算。
// 點「看原因」＝?tab=numcheck&nk=<數字 key>，在本頁列出每一類原因與前 20 台樣本；完整逐台清單用「匯出完整差異清單」。
const { apiFetch } = useApi()
const route = useRoute()
const errMsg = (e: any) => { const d = e?.data?.detail; return typeof d === 'string' ? d : d?.message ?? e?.message ?? '未知錯誤' }
const q = (k: string) => (typeof route.query[k] === 'string' ? String(route.query[k]) : '')

const data = ref<any>(null)
const err = ref('')
const loading = ref(false)
const begin = useLatest()
async function load() {
  const isLatest = begin()
  loading.value = true
  try {
    const r = await apiFetch<any>('/api/number-proof')
    if (!isLatest()) return
    data.value = r; err.value = ''
  } catch (e: any) { if (isLatest()) err.value = `載入失敗：${errMsg(e)}` } finally { if (isLatest()) loading.value = false }
}
onMounted(load)

const u = (extra: Record<string, string | undefined> = {}) =>
  ({ path: '/v2/reconcile', query: Object.fromEntries(Object.entries({ tab: 'numcheck', ...extra }).filter(([, v]) => v !== undefined && v !== '')) })
const sign = (d: number) => `${d > 0 ? '+' : ''}${d}`
const numRows = computed(() => (data.value?.numbers ?? []).map((x: any, i: number) => ({
  ...x, __no: i + 1,
  label_t: x.population ? `${x.label}（${x.population}，${x.unit}）` : x.label,
  diff_t: sign(x.diff) + (x.mismatch && x.mismatch !== Math.abs(x.diff) ? `（${x.mismatch} 台不同）` : ''),
  verdict: !x.mismatch ? '逐台一致' : x.unexplained ? `${x.unexplained} 台未解釋・${x.explanations.length} 類原因` : `已歸因・${x.explanations.length} 類原因`,
  zero_t: x.zero?.text || `${x.zero?.checked ? '查過' : '沒查'}：${x.zero?.why ?? ''}`,
})))
const tone = (r: any) => (!r.mismatch ? 'dr' : r.unexplained ? 'test' : '')
const pick = computed(() => numRows.value.find((x: any) => x.key === q('nk')) ?? null)
const exRows = computed(() => (pick.value?.explanations ?? []).map((e: any, i: number) => ({
  ...e, __no: i + 1, kind: e.unexplained ? '未解釋' : '已歸因',
  sample_t: (e.sample ?? []).map((s: any) => `${s.hostname || '—'} ${s.ip || ''}`.trim()).join('、') + (e.n > (e.sample ?? []).length ? ' …' : ''),
})))
const defRows = computed(() => (data.value?.numbers ?? []).map((x: any, i: number) => ({ ...x, __no: i + 1 })))

// W221-007：完整差異清單（逐台、不截斷）。後端 USER_READ_EXACT：一般帳號以上才能讀。
const { canWrite } = useAuth()
const { exportTable, busy: exporting } = useTableExport()
const exportErr = ref('')
async function exportDiff() {
  exportErr.value = ''
  try {
    const d = await apiFetch<any>('/api/number-proof/diff')
    const rows = d.items.map((i: any) => [i.no, i.label, i.side, i.reason, i.unexplained ? '未解釋' : '已歸因',
      i.hostname ?? '', i.ip ?? '', i.asset_serial ?? '', i.stage_label ?? '', i.os_type ?? ''])
    await exportTable('數字健檢_完整差異清單', ['項次', '數字', '哪一邊有', '原因', '歸因', '主機名', 'IP', '資產序號', '畫面階段', 'OS 類型'], rows)
  } catch (e: any) { exportErr.value = `匯出失敗：${errMsg(e)}` }
}
</script>

<template>
  <div>
    <p v-if="err" class="err">{{ err }}</p>
    <p v-else-if="!data" class="muted">{{ loading ? '重算中…（要把全部機器重新判一次，約數秒）' : '沒有資料' }}</p>
    <template v-else>
      <div class="bar">
        <span class="muted">
          <template v-if="data.machine_state_mode">口徑：{{ data.machine_state_mode.label }}（v{{ data.machine_state_mode.version }}）；</template>
          最近一次掃描 {{ data.evidence?.scan_time || '（沒有）' }}（掃到 {{ data.evidence?.alive }} 台、涵蓋 {{ data.evidence?.covered_segments }} 段）；CIA／帳外 {{ data.evidence?.hardware_rows }} 筆；試連過 {{ data.evidence?.collect_tried_rows }} 筆
        </span>
        <InfoNote>
          納管漏斗的 5 個數字，用第二種、獨立的算法重算一次對照。獨立重算照每個數字的定義直接查資料表另寫，不呼叫畫面那套判定程式——
          同一套程式寫兩次只會一致地錯。「未解釋」＝兩邊都講不出為什麼不一樣，就是要查的錯。
          母體：{{ data.population }}（單位：{{ data.unit }}）；產生時間 {{ data.generated_at }}；資料指紋 {{ data.db_fingerprint?.hash }}
          （各母體資料表筆數的短雜湊，兩次健檢指紋相同代表在同一份資料上算的：{{ data.db_fingerprint ? Object.entries(data.db_fingerprint.counts).map(([k, v]) => `${k}=${v ?? '—'}`).join('、') : '' }}）。
          完整差異清單超過 2,000 列時，匯出要管理員（全站表格匯出的規則）。五個數字的母體不同，不應強制相等。
        </InfoNote>
        <button type="button" class="btn2" :disabled="loading" @click="load">{{ loading ? '重算中…' : '重新計算' }}</button>
        <button v-if="canWrite" type="button" class="btn2" :disabled="exporting" @click="exportDiff">{{ exporting ? '匯出中…' : '匯出完整差異清單' }}</button>
        <span v-else class="muted" data-test="user-only">完整差異清單限一般帳號以上</span>
        <span v-if="exportErr" class="err">{{ exportErr }}</span>
      </div>
      <template v-if="pick">
        <div class="bar"><NuxtLink :to="u()">‹ 返回</NuxtLink><b>{{ pick.label }}：畫面 {{ pick.screen }}／重算 {{ pick.recalc }}（差 {{ pick.diff_t }}）</b></div>
        <V2Table :rows="exRows" prefix="W" row-key="why" unit="類" empty="沒有差異"
                 :cols="[{ k: 'n', label: '台數', mono: true }, { k: 'kind', label: '歸因' }, { k: 'why', label: '原因' }, { k: 'sample_t', label: '樣本（前 20 台）' }]" />
      </template>
      <template v-else>
        <V2Table :rows="numRows" prefix="N" row-key="key" unit="個"
                 :cols="[{ k: 'label_t', label: '數字' },
                         { k: 'screen', label: '畫面值', mono: true, link: (r: any) => `/v2/onboard?tab=funnel&drill=${encodeURIComponent(r.key)}` },
                         { k: 'recalc', label: '獨立重算值', mono: true },
                         { k: 'diff', label: '差異', mono: true, text: (r: any) => r.diff_t },
                         { k: 'verdict', label: '差異解釋', tone, link: (r: any) => (r.mismatch ? u({ nk: r.key }) : null) },
                         { k: 'zero_t', label: '是 0 的話', tone: (r: any) => (r.zero?.text ? (r.zero.checked ? 'dr' : 'test') : '') }]" />
        <h3 class="h3">每個數字怎麼算（跟漏斗同一份）</h3>
        <V2Table :rows="defRows" prefix="F" row-key="key" unit="個"
                 :cols="[{ k: 'label', label: '數字' }, { k: 'how', label: '怎麼算' }, { k: 'source', label: '資料來源' }, { k: 'limits', label: '已知限制' }]" />
      </template>
    </template>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; }
.muted { color: var(--muted); font-size: 13px; }
.err { color: var(--bad); }
.h3 { margin: 16px 0 6px; font-size: 15px; }
.btn2 { font-family: inherit; font-size: 12px; padding: 3px 10px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--card); color: var(--ink); cursor: pointer; }
.btn2:disabled { opacity: .5; cursor: default; }
</style>
